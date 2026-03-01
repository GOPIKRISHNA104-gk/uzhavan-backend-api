"""
Production-Grade Speech-to-Text (STT) Service
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses Google Gemini Audio Understanding as primary STT engine.
Falls back gracefully with retry logic.

Supports:
- 6 Indian languages (Tamil, Hindi, Telugu, Kannada, Malayalam, English)
- Auto language detection
- Confidence scoring
- Streaming partial transcripts
- Circuit-breaker pattern
"""

import os
import asyncio
import base64
import json
import struct
import tempfile
import time
import logging
from typing import Dict, Any, Optional, AsyncGenerator

import google.generativeai as genai

logger = logging.getLogger(__name__)

# ─── Language Config ────────────────────────────────────────────────────────

SUPPORTED_LANGUAGES = {
    "ta-IN": "tamil",
    "en-IN": "english",
    "hi-IN": "hindi",
    "ml-IN": "malayalam",
    "kn-IN": "kannada",
    "te-IN": "telugu",
}

BIDI_LANGUAGE_CODES = {
    "tamil":    "ta-IN",
    "english":  "en-IN",
    "hindi":    "hi-IN",
    "malayalam":"ml-IN",
    "kannada":  "kn-IN",
    "telugu":   "te-IN",
}


# ─── Circuit Breaker ────────────────────────────────────────────────────────

class CircuitBreaker:
    """Prevents cascading failures to upstream STT API."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = self.CLOSED

    def record_success(self):
        self.failure_count = 0
        self.state = self.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning("STT Circuit Breaker: OPEN — too many failures")

    def is_available(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if self.last_failure_time and (time.time() - self.last_failure_time > self.recovery_timeout):
                self.state = self.HALF_OPEN
                logger.info("STT Circuit Breaker: HALF_OPEN — trying recovery")
                return True
            return False
        return True  # HALF_OPEN: allow one attempt


# ─── STT Service ────────────────────────────────────────────────────────────

class STTService:
    """
    Production Speech-to-Text Service.

    Pipeline:
      PCM bytes → WAV file → Gemini Audio Upload → Transcription JSON
      ↳ Auto language detection from transcript
      ↳ Confidence scoring
      ↳ Circuit-breaker protection
      ↳ Retry with exponential back-off (max 2 retries)
    """

    def __init__(self):
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel("gemini-2.0-flash")
        else:
            self._model = None
            logger.error("GEMINI_API_KEY not set — STT service will fail")

    # ── Public API ──────────────────────────────────────────────────────────

    async def transcribe(
        self,
        audio_bytes: bytes,
        language_hint: str = "tamil",
        sample_rate: int = 16000,
        channels: int = 1,
        bits: int = 16,
    ) -> Dict[str, Any]:
        """
        Transcribe PCM audio to text.

        Returns:
            {
                "text": str,
                "language": str,      # detected language name
                "language_code": str, # BCP-47 code
                "confidence": float,
                "duration_ms": int,
                "error": Optional[str]
            }
        """
        if not self._model:
            return self._empty_result(language_hint, error="STT model not configured")

        if not audio_bytes or len(audio_bytes) < 3200:  # < 0.1s of 16kHz
            return self._empty_result(language_hint)

        if not self._circuit_breaker.is_available():
            logger.warning("STT Circuit breaker OPEN — returning empty result")
            return self._empty_result(language_hint, error="STT temporarily unavailable")

        duration_ms = int((len(audio_bytes) / (sample_rate * channels * (bits // 8))) * 1000)

        for attempt in range(3):
            try:
                result = await self._transcribe_gemini(
                    audio_bytes, language_hint, sample_rate, channels, bits
                )
                result["duration_ms"] = duration_ms
                self._circuit_breaker.record_success()
                return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"STT attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(0.3 * (2 ** attempt))  # 0.3s, 0.6s
                else:
                    self._circuit_breaker.record_failure()
                    return self._empty_result(language_hint, error=str(e))

        return self._empty_result(language_hint)

    async def transcribe_streaming(
        self,
        audio_chunks: AsyncGenerator[bytes, None],
        language_hint: str = "tamil",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streaming transcription — accumulates chunks and emits partial results.
        Yields partial dicts with 'partial': True until final result.
        """
        buffer = bytearray()
        min_chunk_bytes = 16000 * 2 * 2  # 2 seconds of audio to emit partial

        async for chunk in audio_chunks:
            buffer.extend(chunk)
            if len(buffer) >= min_chunk_bytes:
                partial = await self.transcribe(bytes(buffer), language_hint)
                partial["partial"] = True
                yield partial
                # Retain last 0.5s as overlap for continuity
                overlap = int(16000 * 0.5 * 2)
                buffer = bytearray(buffer[-overlap:])

        # Final transcription of remaining buffer
        if len(buffer) > 3200:
            final = await self.transcribe(bytes(buffer), language_hint)
            final["partial"] = False
            yield final

    # ── Internal ──────────────────────────────────────────────────────────

    async def _transcribe_gemini(
        self,
        audio_bytes: bytes,
        language_hint: str,
        sample_rate: int,
        channels: int,
        bits: int,
    ) -> Dict[str, Any]:
        """Send WAV to Gemini inline to bypass intermediate file upload flakiness."""
        # Create WAV buffer in memory
        import io
        wav_buffer = io.BytesIO()
        _write_wav_header(wav_buffer, audio_bytes, sample_rate, channels, bits)
        wav_buffer.write(audio_bytes)
        wav_data = wav_buffer.getvalue()

        prompt = _build_stt_prompt(language_hint)

        response = await asyncio.to_thread(
            self._model.generate_content,
            [
                {
                    "mime_type": "audio/wav",
                    "data": wav_data,
                },
                prompt
            ],
            generation_config={"response_mime_type": "application/json"},
        )

        raw = response.text.strip()
        data = json.loads(raw)

        text = data.get("text", "").strip()
        detected_lang = data.get("language", language_hint).lower()
        confidence = float(data.get("confidence", 0.8))

        # Normalize
        detected_lang = _normalize_language(detected_lang, language_hint)
        lang_code = BIDI_LANGUAGE_CODES.get(detected_lang, "ta-IN")

        return {
            "text": text,
            "language": detected_lang,
            "language_code": lang_code,
            "confidence": round(confidence, 3),
            "duration_ms": 0,  # filled by caller
            "error": None,
        }

    def _empty_result(self, language_hint: str, error: Optional[str] = None) -> Dict[str, Any]:
        return {
            "text": "",
            "language": language_hint,
            "language_code": BIDI_LANGUAGE_CODES.get(language_hint, "ta-IN"),
            "confidence": 0.0,
            "duration_ms": 0,
            "error": error,
        }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _write_wav_header(f, data: bytes, sample_rate: int, channels: int, bits: int):
    """Write a standards-compliant WAV header."""
    data_size = len(data)
    byte_rate = sample_rate * channels * (bits // 8)
    block_align = channels * (bits // 8)
    f.write(b"RIFF")
    f.write(struct.pack("<I", 36 + data_size))
    f.write(b"WAVE")
    f.write(b"fmt ")
    f.write(struct.pack("<I", 16))          # PCM sub-chunk size
    f.write(struct.pack("<H", 1))           # PCM format
    f.write(struct.pack("<H", channels))
    f.write(struct.pack("<I", sample_rate))
    f.write(struct.pack("<I", byte_rate))
    f.write(struct.pack("<H", block_align))
    f.write(struct.pack("<H", bits))
    f.write(b"data")
    f.write(struct.pack("<I", data_size))


def _build_stt_prompt(language_hint: str) -> str:
    return f"""Listen to this audio carefully.
The farmer is likely speaking in one of: Tamil, English, Hindi, Malayalam, Kannada, or Telugu.
Language hint (session language): {language_hint}

Instructions:
- Transcribe EXACTLY what is spoken. Do not paraphrase.
- Detect the actual language used in speech.
- Estimate confidence in your transcription accuracy (0.0 to 1.0).

Return ONLY this JSON (no markdown, no extra text):
{{
  "text": "<exact spoken text>",
  "language": "<tamil|english|hindi|malayalam|kannada|telugu>",
  "confidence": <0.0-1.0>
}}

If no clear speech is heard, return:
{{"text": "", "language": "{language_hint}", "confidence": 0.0}}"""


def _normalize_language(detected: str, fallback: str) -> str:
    """Map any variant to canonical name."""
    valid = {"tamil", "english", "hindi", "malayalam", "kannada", "telugu"}
    cleaned = detected.lower().strip()
    return cleaned if cleaned in valid else fallback


# ─── Singleton ───────────────────────────────────────────────────────────────
stt_service = STTService()
