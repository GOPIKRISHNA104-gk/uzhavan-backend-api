"""
Gemini Hallucination-Safe Wrapper
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Standalone wrapper for Google Gemini API with:
- Anti-hallucination enforcement
- Rate-limit retry with exponential backoff
- Circuit breaker protection
- Multi-language response generation
- Emotion-aware tone adjustment

Rules:
1. NEVER generate numbers not present in API data
2. NEVER guess missing fields
3. If API returns null → respond clearly
4. Response must be under 4 sentences
5. Use selected language ONLY
6. Use simple farmer-friendly tone
7. Inject emotion modifier
"""

import os
import json
import asyncio
import time
import logging
from typing import Dict, Any, Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────

_API_KEY = os.getenv("GEMINI_API_KEY")
if _API_KEY:
    genai.configure(api_key=_API_KEY)

_MODEL_NAME = "gemini-2.0-flash"

# ─── Circuit Breaker ─────────────────────────────────────────────────────────

class GeminiCircuitBreaker:
    """Prevents cascading failures when Gemini API is down or rate-limited."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
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
            logger.warning("Gemini Circuit Breaker: OPEN — too many failures")

    def is_available(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if self.last_failure_time and (time.time() - self.last_failure_time > self.recovery_timeout):
                self.state = self.HALF_OPEN
                logger.info("Gemini Circuit Breaker: HALF_OPEN — trying recovery")
                return True
            return False
        return True  # HALF_OPEN: allow one attempt


# ─── Multilingual Fallback Messages ──────────────────────────────────────────

FALLBACK_MESSAGES = {
    "english": "Sorry, I couldn't process your request right now. Please try again.",
    "tamil": "சர்வர் பிரச்சனை இருக்கு அண்ணா, கொஞ்ச நேரம் கழிச்சு மீண்டும் கேளுங்க.",
    "hindi": "माफ़ करें, अभी जवाब देने में दिक्कत हो रही है। कुछ देर बाद फिर कोशिश करें।",
    "telugu": "క్షమించండి, ప్రస్తుతం సమాధానం ఇవ్వడంలో సమస్య ఉంది. కొంచెం సేపట్లో మళ్ళీ ప్రయత్నించండి.",
    "kannada": "ಕ್ಷಮಿಸಿ, ಈಗ ಉತ್ತರ ನೀಡುವಲ್ಲಿ ಸಮಸ್ಯೆ ಇದೆ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    "malayalam": "ക്ഷമിക്കണം, ഇപ്പോൾ ഉത്തരം നൽകാൻ കഴിയുന്നില്ല. കുറച്ച് സമയത്തിന് ശേഷം വീണ്ടും ശ്രമിക്കുക.",
}

NO_DATA_MESSAGES = {
    "english": "I checked but today's data is not available. Please try again later or contact your local agriculture office.",
    "tamil": "இன்றைய தகவல் கிடைக்கவில்லை அண்ணா. கொஞ்ச நேரம் கழிச்சு மீண்டும் பாருங்க.",
    "hindi": "आज का डेटा अभी उपलब्ध नहीं है। थोड़ी देर बाद फिर से देखें।",
    "telugu": "ఈరోజు డేటా ఇప్పుడు అందుబాటులో లేదు. కొంచెం సేపట్లో మళ్ళీ చూడండి.",
    "kannada": "ಇಂದಿನ ಡೇಟಾ ಈಗ ಲಭ್ಯವಿಲ್ಲ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ನೋಡಿ.",
    "malayalam": "ഇന്നത്തെ ഡാറ്റ ഇപ്പോൾ ലഭ്യമല്ല. കുറച്ച് കഴിഞ്ഞ് വീണ്ടും നോക്കുക.",
}

# ─── Emotion-Aware Prompt Modifiers ──────────────────────────────────────────

EMOTION_MODIFIERS = {
    "worried": "The farmer sounds worried. Use a calm, reassuring, confident tone. ",
    "angry": "The farmer sounds frustrated. Be respectful, patient, and understanding. ",
    "confused": "The farmer seems confused. Speak extra simply and slowly. Give step-by-step clarity. ",
    "happy": "The farmer is happy. Match their energy with encouraging, positive words. ",
    "neutral": "",
}


# ─── Hallucination-Safe Prompt Template ──────────────────────────────────────

_DATA_PROMPT = """You are Uzhavan AI — a trusted village agriculture expert.
A farmer asked you a question. You have REAL official data.

Farmer Question: {transcript}

Official API Data:
{json_data}

Emotion: {emotion}
Language: {language}

{emotion_modifier}

STRICT Rules:
- Do NOT modify numeric values from the data
- Do NOT add new data that is not in API response
- Do NOT guess missing fields
- Explain clearly and simply in {language}
- Keep under 4 sentences
- Use simple farmer-friendly words
- Speak like a helpful village elder, not a machine

Respond ONLY in {language}:"""

_NO_DATA_PROMPT = """You are Uzhavan AI — a trusted village agriculture expert.
A farmer asked: "{transcript}"

You checked for {data_type} data but it is NOT available right now.

Tell the farmer HONESTLY in {language} (2-3 sentences):
- Data is not available right now
- Suggest checking again later
- Be warm and supportive

{emotion_modifier}

Respond ONLY in {language}:"""

_GENERAL_PROMPT = """You are Uzhavan AI — a village agriculture expert assistant.
Speak like a friendly, experienced farmer's elder.

Farmer asked (in {language}): "{transcript}"

{emotion_modifier}

Rules:
1. Respond ONLY in {language}
2. Keep to 3-4 sentences max
3. Use simple farming language
4. Be warm and practical
5. Do NOT guess prices or weather data

Respond in {language}:"""


# ─── Main Wrapper Class ──────────────────────────────────────────────────────

class GeminiWrapper:
    """
    Production-grade Gemini API wrapper with:
    - Anti-hallucination data injection
    - Rate-limit retry (3 attempts, exponential backoff)
    - Circuit breaker (5 failures → 60s cooldown)
    - Multi-language support (6 Indian languages)
    - Emotion-aware response tone
    """

    def __init__(self):
        self._model = genai.GenerativeModel(_MODEL_NAME) if _API_KEY else None
        self._circuit_breaker = GeminiCircuitBreaker()
        self._call_count = 0
        self._error_count = 0

        if not self._model:
            logger.error("GEMINI_API_KEY not set — GeminiWrapper will return fallbacks")

    async def format_response(
        self,
        transcript: str,
        language: str,
        data: Optional[Any] = None,
        data_type: str = "DATA",
        emotion: str = "neutral",
    ) -> str:
        """
        Format real API data into a farmer-friendly response.
        
        If data is provided → inject into hallucination-safe prompt.
        If data is None → return honest no-data message.
        """
        emotion_modifier = EMOTION_MODIFIERS.get(emotion, "")

        if data:
            json_data = json.dumps(data, ensure_ascii=False, indent=2)
            prompt = _DATA_PROMPT.format(
                transcript=transcript,
                json_data=json_data,
                emotion=emotion,
                language=language,
                emotion_modifier=emotion_modifier,
            )
        else:
            prompt = _NO_DATA_PROMPT.format(
                transcript=transcript,
                language=language,
                data_type=data_type,
                emotion_modifier=emotion_modifier,
            )

        result = await self._call(prompt, language)
        return result

    async def general_response(
        self,
        transcript: str,
        language: str,
        emotion: str = "neutral",
    ) -> str:
        """Generate a general conversation response (no data injection)."""
        emotion_modifier = EMOTION_MODIFIERS.get(emotion, "")
        prompt = _GENERAL_PROMPT.format(
            transcript=transcript,
            language=language,
            emotion_modifier=emotion_modifier,
        )
        return await self._call(prompt, language)

    async def _call(self, prompt: str, language: str, timeout: float = 8.0) -> str:
        """
        Call Gemini with rate-limit retry and circuit breaker.
        Returns generated text or fallback message.
        """
        if not self._model:
            return FALLBACK_MESSAGES.get(language, FALLBACK_MESSAGES["english"])

        if not self._circuit_breaker.is_available():
            logger.warning("Gemini circuit breaker OPEN — using fallback")
            return FALLBACK_MESSAGES.get(language, FALLBACK_MESSAGES["english"])

        self._call_count += 1
        last_error = None

        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._model.generate_content, prompt),
                    timeout=timeout,
                )
                self._circuit_breaker.record_success()
                return response.text.strip()

            except asyncio.TimeoutError:
                logger.error(f"Gemini timeout (attempt {attempt + 1}/3)")
                self._error_count += 1
                self._circuit_breaker.record_failure()
                return FALLBACK_MESSAGES.get(language, FALLBACK_MESSAGES["english"])

            except asyncio.CancelledError:
                raise

            except Exception as e:
                last_error = e
                error_str = str(e)
                self._error_count += 1

                # Retry on rate limit
                if "429" in error_str or "ResourceExhausted" in error_str or "quota" in error_str.lower():
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"Gemini 429, retry {attempt + 1}/3 after {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Gemini error: {e}")
                    self._circuit_breaker.record_failure()
                    return FALLBACK_MESSAGES.get(language, FALLBACK_MESSAGES["english"])

        # All retries exhausted
        logger.error(f"Gemini all retries exhausted: {last_error}")
        self._circuit_breaker.record_failure()
        return FALLBACK_MESSAGES.get(language, FALLBACK_MESSAGES["english"])

    @property
    def stats(self) -> Dict[str, Any]:
        """Return wrapper statistics for monitoring."""
        return {
            "total_calls": self._call_count,
            "total_errors": self._error_count,
            "error_rate": round(self._error_count / max(self._call_count, 1), 3),
            "circuit_breaker_state": self._circuit_breaker.state,
            "model": _MODEL_NAME,
            "api_key_configured": bool(_API_KEY),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────
gemini_wrapper = GeminiWrapper()
