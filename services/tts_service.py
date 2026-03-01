"""
Neural TTS Service for Uzhavan AI
Supports 6 Indian languages with Google Cloud TTS Neural Voices.
Falls back to gTTS when Google Cloud credentials are unavailable.
"""

import os
import base64
import tempfile
import asyncio
from typing import Optional, Dict
from enum import Enum

# Try Google Cloud TTS first, fall back to gTTS
try:
    from google.cloud import texttospeech
    GOOGLE_CLOUD_TTS_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_TTS_AVAILABLE = False

from gtts import gTTS


class VoiceSpeed(str, Enum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


# Language-specific Neural TTS configuration
LANGUAGE_VOICES: Dict[str, Dict] = {
    "tamil": {
        "code": "ta-IN",
        "gtts_code": "ta",
        "voice_name": "ta-IN-Wavenet-A",
        "speaking_rate": 0.9,   # Slightly slower for clarity
        "pitch": -1.0,          # Calm, grounded tone
    },
    "english": {
        "code": "en-IN",
        "gtts_code": "en",
        "voice_name": "en-IN-Wavenet-A",
        "speaking_rate": 0.95,
        "pitch": -0.5,
    },
    "hindi": {
        "code": "hi-IN",
        "gtts_code": "hi",
        "voice_name": "hi-IN-Wavenet-A",
        "speaking_rate": 0.9,
        "pitch": -1.0,
    },
    "malayalam": {
        "code": "ml-IN",
        "gtts_code": "ml",
        "voice_name": "ml-IN-Wavenet-A",
        "speaking_rate": 0.85,  # Malayalam is naturally fast, slow down
        "pitch": -1.0,
    },
    "kannada": {
        "code": "kn-IN",
        "gtts_code": "kn",
        "voice_name": "kn-IN-Wavenet-A",
        "speaking_rate": 0.9,
        "pitch": -1.0,
    },
    "telugu": {
        "code": "te-IN",
        "gtts_code": "te",
        "voice_name": "te-IN-Wavenet-A",
        "speaking_rate": 0.9,
        "pitch": -1.0,
    },
}

# Emotion-based TTS adjustments
EMOTION_TTS_ADJUSTMENTS: Dict[str, Dict] = {
    "worried": {"speaking_rate_delta": -0.1, "pitch_delta": -0.5},
    "angry": {"speaking_rate_delta": -0.15, "pitch_delta": 0.0},
    "confused": {"speaking_rate_delta": -0.2, "pitch_delta": 0.0},
    "happy": {"speaking_rate_delta": 0.05, "pitch_delta": 1.0},
    "neutral": {"speaking_rate_delta": 0.0, "pitch_delta": 0.0},
}


class TTSService:
    """
    Text-to-Speech service with Neural voice support.
    Uses Google Cloud TTS for premium voices, falls back to gTTS.
    """

    def __init__(self):
        self.cloud_client = None
        if GOOGLE_CLOUD_TTS_AVAILABLE:
            try:
                self.cloud_client = texttospeech.TextToSpeechClient()
                print("[OK] Google Cloud TTS initialized")
            except Exception as e:
                print(f"[WARN] Google Cloud TTS not available, using gTTS fallback: {e}")
                self.cloud_client = None

    async def synthesize(
        self,
        text: str,
        language: str = "english",
        emotion: str = "neutral",
        speed: VoiceSpeed = VoiceSpeed.NORMAL,
    ) -> Optional[bytes]:
        """
        Synthesize speech from text.
        Returns raw MP3 audio bytes.
        """
        if not text or not text.strip():
            return None

        lang_config = LANGUAGE_VOICES.get(language.lower(), LANGUAGE_VOICES["english"])
        emotion_adj = EMOTION_TTS_ADJUSTMENTS.get(emotion, EMOTION_TTS_ADJUSTMENTS["neutral"])

        if self.cloud_client:
            return await self._synthesize_cloud(text, lang_config, emotion_adj)
        else:
            return await self._synthesize_gtts(text, lang_config)

    async def synthesize_to_base64(
        self,
        text: str,
        language: str = "english",
        emotion: str = "neutral",
    ) -> str:
        """Synthesize and return as base64 string"""
        audio_bytes = await self.synthesize(text, language, emotion)
        if audio_bytes:
            return base64.b64encode(audio_bytes).decode("utf-8")
        return ""

    async def _synthesize_cloud(
        self,
        text: str,
        lang_config: Dict,
        emotion_adj: Dict,
    ) -> Optional[bytes]:
        """Use Google Cloud TTS Neural voices"""
        try:
            synthesis_input = texttospeech.SynthesisInput(text=text)

            voice = texttospeech.VoiceSelectionParams(
                language_code=lang_config["code"],
                name=lang_config["voice_name"],
            )

            # Apply emotion-based adjustments
            speaking_rate = lang_config["speaking_rate"] + emotion_adj["speaking_rate_delta"]
            pitch = lang_config["pitch"] + emotion_adj["pitch_delta"]

            # Clamp values
            speaking_rate = max(0.5, min(2.0, speaking_rate))
            pitch = max(-10.0, min(10.0, pitch))

            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=pitch,
                effects_profile_id=["small-bluetooth-speaker-class-device"],
            )

            # Run sync API in thread pool
            response = await asyncio.to_thread(
                self.cloud_client.synthesize_speech,
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )

            return response.audio_content

        except Exception as e:
            print(f"Cloud TTS error, falling back to gTTS: {e}")
            return await self._synthesize_gtts(text, lang_config)

    async def _synthesize_gtts(
        self,
        text: str,
        lang_config: Dict,
    ) -> Optional[bytes]:
        """Fallback: use gTTS for speech synthesis"""
        try:
            def _generate():
                gtts_code = lang_config.get("gtts_code", "en")
                fp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                fp.close()
                temp_path = fp.name

                try:
                    tts = gTTS(text=text, lang=gtts_code, slow=False)
                    tts.save(temp_path)

                    with open(temp_path, "rb") as f:
                        return f.read()
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except:
                            pass

            return await asyncio.to_thread(_generate)

        except Exception as e:
            print(f"gTTS error: {e}")
            return None

    async def synthesize_streaming(
        self,
        text: str,
        language: str = "english",
        emotion: str = "neutral",
        chunk_size: int = 4096,
    ):
        """
        Generator that yields audio chunks for streaming playback.
        Splits text into sentences and synthesizes each one.
        """
        # Split text into sentences for faster first-byte
        sentences = self._split_sentences(text)

        for sentence in sentences:
            if not sentence.strip():
                continue

            audio_bytes = await self.synthesize(sentence, language, emotion)
            if audio_bytes:
                # Yield in chunks
                for i in range(0, len(audio_bytes), chunk_size):
                    yield audio_bytes[i:i + chunk_size]

    def _split_sentences(self, text: str) -> list:
        """Split text into sentences for streaming TTS"""
        import re
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?।])\s+', text)
        return [s for s in sentences if s.strip()]


# Singleton
tts_service = TTSService()
