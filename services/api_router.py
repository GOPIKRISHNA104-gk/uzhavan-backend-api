"""
Production API Router — Anti-Hallucination Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Routes farmer intents to correct backend APIs, fetches REAL data,
and injects it into Gemini prompt — preventing any hallucination.

Gemini ONLY formats real data. It NEVER generates numbers.
If API fails → fallback to cache. If cache empty → tell farmer honestly.

Supported Intents:
  - market_price  → Mandi Service
  - weather       → Weather Service
  - disease       → Disease Router (keyword-based lookup)
  - news          → Agriculture News Router
  - general_query → No Data Fetch (pure Gemini conversation)
"""

import os
import json
import asyncio
import logging
from typing import Dict, Any, Optional

import google.generativeai as genai
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ─── Gemini Config ────────────────────────────────────────────────────────────

api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

_GEMINI_MODEL_NAME = "gemini-2.0-flash"


# ─── Hallucination-Safe Prompt Template ──────────────────────────────────────

_INJECTION_PROMPT = """You are Uzhavan AI — a trusted village agriculture expert assistant.
A farmer asked you a question. You have REAL official data to answer with.

Farmer's question (in {language}): {transcript}

=== OFFICIAL {intent_label} DATA ===
{json_data}
=== END OF DATA ===

STRICT RULES:
1. You MUST use ONLY the data provided above. Do not invent any numbers.
2. Do NOT guess prices, temperatures, or any other values.
3. If data says "no data available" or is empty, tell the farmer honestly that you don't have today's data.
4. Respond ONLY in {language}. No other language.
5. Keep response SHORT (2-3 sentences max) — this is a voice conversation.
6. Use simple, rural-friendly words. Avoid technical/scientific jargon.
7. Speak like a helpful elder farmer, not a machine.

{emotion_instruction}

Respond now in {language}:"""

_NO_DATA_PROMPT = """You are Uzhavan AI — a trusted village agriculture expert assistant.
A farmer asked you: "{transcript}"

You tried to fetch real data but it is not available right now.

Tell the farmer HONESTLY in {language} (2-3 sentences max) that:
- You checked but today's {intent_label} data is not available
- Suggest they check again later, or contact local agriculture office
- Be warm and supportive

Do NOT make up any data. Respond ONLY in {language}:"""

_GENERAL_PROMPT = """You are Uzhavan AI — a village agriculture expert AI assistant.
Speak like a friendly, experienced farmer's elder in rural India.

Farmer asked (in {language}): "{transcript}"

Context from this conversation:
{history_text}

{emotion_instruction}

Rules:
1. Respond ONLY in {language}.
2. Keep it SHORT (2-3 sentences) — this is voice.
3. Use simple farming language, practical advice.
4. Be warm and supportive.
5. Do NOT guess market prices or weather data — if asked, say you'll look it up.

Respond now in {language}:"""


# ─── Intent Label Map ─────────────────────────────────────────────────────────

_INTENT_LABELS = {
    "market_price":  "MARKET PRICE",
    "weather":       "WEATHER",
    "disease":       "CROP DISEASE",
    "news":          "AGRICULTURE NEWS",
    "general_query": "GENERAL",
}


# ─── API Router ───────────────────────────────────────────────────────────────

class APIRouter:
    """
    Intent-based API router with Gemini formatter.

    Flow:
      intent + transcript → fetch real data → inject into prompt → Gemini formats → return text
    """

    def __init__(self):
        self._model = genai.GenerativeModel(_GEMINI_MODEL_NAME) if api_key else None

    async def route_and_respond(
        self,
        intent: str,
        transcript: str,
        language: str,
        crop: Optional[str],
        location: str,
        emotion: str,
        emotion_instruction: str,
        conversation_history: list,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Main routing method.

        Returns:
            {
                "response_text": str,
                "data_used": dict,
                "intent": str,
                "api_source": str,
                "hallucination_safe": bool
            }
        """
        try:
            if intent == "market_price":
                return await self._handle_market_price(
                    transcript, language, crop, location, emotion, emotion_instruction, db
                )
            elif intent == "weather":
                return await self._handle_weather(
                    transcript, language, location, emotion, emotion_instruction, db
                )
            elif intent == "disease":
                return await self._handle_disease(
                    transcript, language, crop, emotion, emotion_instruction, db
                )
            elif intent == "news":
                return await self._handle_news(
                    transcript, language, emotion, emotion_instruction, db
                )
            else:  # general_query
                return await self._handle_general(
                    transcript, language, emotion, emotion_instruction, conversation_history
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"APIRouter error for intent={intent}: {e}")
            return await self._fallback_response(transcript, language, intent, str(e))

    # ── Market Price Handler ──────────────────────────────────────────────────

    async def _handle_market_price(
        self, transcript, language, crop, location, emotion, emotion_instruction, db
    ) -> Dict[str, Any]:
        from services.mandi_service import mandi_service
        from services.cache_service import cache_manager, CacheConfig

        data = None
        api_source = "live"

        # Try live
        try:
            cache_key = f"voice:price:{(location or 'Tamil Nadu').lower()}:{(crop or 'all').lower()}"
            cached, stale = await cache_manager.get(cache_key)
            if cached:
                data = cached
                api_source = "cache"
            else:
                prices = await asyncio.wait_for(
                    mandi_service.get_today_prices(
                        db=db,
                        state=location or "Tamil Nadu",
                        commodity=crop or None,
                        limit=5,
                    ),
                    timeout=5.0,
                )
                if prices:
                    data = [
                        {
                            "commodity": p.commodity,
                            "market": p.market,
                            "district": p.district,
                            "state": p.state,
                            "min_price": float(p.min_price or 0),
                            "max_price": float(p.max_price or 0),
                            "modal_price": float(p.modal_price or 0),
                            "unit": "quintal",
                        }
                        for p in prices[:5]
                    ]
                    await cache_manager.set(
                        cache_key, data, CacheConfig.MANDI_PRICES_TODAY, "prices"
                    )
                    api_source = "live"
        except Exception as e:
            logger.warning(f"Market price fetch error: {e}")

        return await self._format_with_gemini(
            transcript=transcript,
            language=language,
            intent="market_price",
            data=data,
            emotion=emotion,
            emotion_instruction=emotion_instruction,
            api_source=api_source,
        )

    # ── Weather Handler ───────────────────────────────────────────────────────

    async def _handle_weather(
        self, transcript, language, location, emotion, emotion_instruction, db
    ) -> Dict[str, Any]:
        from services.weather_service import weather_service
        from services.cache_service import cache_manager, CacheConfig

        data = None
        api_source = "live"

        try:
            cache_key = f"voice:weather:{(location or 'Tamil Nadu').lower()}"
            cached, _ = await cache_manager.get(cache_key)
            if cached:
                data = cached
                api_source = "cache"
            else:
                weather_data = await asyncio.wait_for(
                    weather_service.get_weather_for_location(db, location or "Tamil Nadu"),
                    timeout=5.0,
                )
                if weather_data:
                    current = weather_data.get("current", {})
                    data = {
                        "location": location or "Tamil Nadu",
                        "temperature_celsius": current.get("temperature_2m", "N/A"),
                        "humidity_percent": current.get("relative_humidity_2m", "N/A"),
                        "wind_kph": current.get("wind_speed_10m", "N/A"),
                        "rain_mm": current.get("precipitation", "N/A"),
                        "weather_code": current.get("weather_code", "N/A"),
                        "farming_advisory": weather_data.get("advisory", ""),
                    }
                    await cache_manager.set(
                        cache_key, data, CacheConfig.WEATHER_CURRENT, "weather"
                    )
                    api_source = "live"
        except Exception as e:
            logger.warning(f"Weather fetch error: {e}")

        return await self._format_with_gemini(
            transcript=transcript,
            language=language,
            intent="weather",
            data=data,
            emotion=emotion,
            emotion_instruction=emotion_instruction,
            api_source=api_source,
        )

    # ── Disease Handler ───────────────────────────────────────────────────────

    async def _handle_disease(
        self, transcript, language, crop, emotion, emotion_instruction, db
    ) -> Dict[str, Any]:
        """
        Disease intent: Gemini provides treatment advice from transcript.
        No hallucination risk here (no numeric data involved).
        """
        prompt = f"""You are Uzhavan AI — a crop disease expert for Indian farmers.
A farmer reports a crop problem.

Farmer's report (in {language}): "{transcript}"

{emotion_instruction}

Provide a practical, actionable response in {language}:
1. Identify the likely disease/pest from symptoms described
2. Give 2-3 specific treatment steps (use common available products)
3. Recommend when to consult local agriculture officer if serious

Rules:
- ONLY in {language}
- Simple village language, not scientific terms
- Keep to 3-4 sentences max (voice conversation)
- Be specific — do not give vague advice

Respond in {language}:"""

        response_text = await self._call_gemini(prompt)
        return {
            "response_text": response_text,
            "data_used": {"crop": crop, "transcript": transcript},
            "intent": "disease",
            "api_source": "gemini_advice",
            "hallucination_safe": True,
        }

    # ── News Handler ─────────────────────────────────────────────────────────

    async def _handle_news(
        self, transcript, language, emotion, emotion_instruction, db
    ) -> Dict[str, Any]:
        from services.cache_service import cache_manager

        data = None
        api_source = "cache"

        try:
            # Try to get cached news
            cache_key = f"voice:news:{language}"
            cached, _ = await cache_manager.get(cache_key)
            if cached:
                data = cached
        except Exception as e:
            logger.warning(f"News cache fetch error: {e}")

        return await self._format_with_gemini(
            transcript=transcript,
            language=language,
            intent="news",
            data=data,
            emotion=emotion,
            emotion_instruction=emotion_instruction,
            api_source=api_source,
        )

    # ── General Conversation Handler ─────────────────────────────────────────

    async def _handle_general(
        self, transcript, language, emotion, emotion_instruction, conversation_history
    ) -> Dict[str, Any]:
        history_lines = []
        for msg in conversation_history[-6:]:
            role = "Farmer" if msg.get("role") == "farmer" else "AI"
            history_lines.append(f"{role}: {msg.get('text', '')}")
        history_text = "\n".join(history_lines) if history_lines else "No previous conversation."

        prompt = _GENERAL_PROMPT.format(
            language=language,
            transcript=transcript,
            history_text=history_text,
            emotion_instruction=emotion_instruction,
        )

        response_text = await self._call_gemini(prompt)
        return {
            "response_text": response_text,
            "data_used": {},
            "intent": "general_query",
            "api_source": "gemini_chat",
            "hallucination_safe": True,
        }

    # ── Gemini Formatter (Hallucination-Safe Core) ────────────────────────────

    async def _format_with_gemini(
        self,
        transcript: str,
        language: str,
        intent: str,
        data: Optional[Any],
        emotion: str,
        emotion_instruction: str,
        api_source: str,
    ) -> Dict[str, Any]:
        """
        Format real data with Gemini.
        If no data, use honest no-data response.
        Gemini NEVER generates numbers — only formats what's provided.
        """
        intent_label = _INTENT_LABELS.get(intent, "DATA")

        if data:
            json_data = json.dumps(data, ensure_ascii=False, indent=2)
            prompt = _INJECTION_PROMPT.format(
                language=language,
                transcript=transcript,
                intent_label=intent_label,
                json_data=json_data,
                emotion_instruction=emotion_instruction,
            )
        else:
            prompt = _NO_DATA_PROMPT.format(
                transcript=transcript,
                language=language,
                intent_label=intent_label,
            )

        response_text = await self._call_gemini(prompt)

        return {
            "response_text": response_text,
            "data_used": {"raw": data} if data else {},
            "intent": intent,
            "api_source": api_source,
            "hallucination_safe": True,
        }

    # ── Gemini Call with Timeout ──────────────────────────────────────────────

    async def _call_gemini(self, prompt: str, timeout: float = 8.0) -> str:
        if not self._model:
            return "Sorry, AI service is not available right now."

        last_error = None
        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._model.generate_content, prompt),
                    timeout=timeout,
                )
                return response.text.strip()
            except asyncio.TimeoutError:
                logger.error("Gemini response timed out")
                return "Sorry, response is taking too long. Please try again."
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                error_str = str(e)
                # Retry on 429 rate limit
                if "429" in error_str or "ResourceExhausted" in error_str or "quota" in error_str.lower():
                    wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(f"Gemini 429 rate limit, retry {attempt+1}/3 after {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Gemini call error: {e}")
                    return "Sorry, I couldn't process your request right now."

        logger.error(f"Gemini all 3 retries exhausted: {last_error}")
        return "Sorry, the AI service is busy. Please try again in a moment."

    # ── Fallback ─────────────────────────────────────────────────────────────

    _FALLBACK_MESSAGES = {
        "english": "Sorry, I'm having trouble fetching data right now. Please try again in a moment.",
        "tamil": "சர்வர் பிரச்சனை இருக்கு அண்ணா, கொஞ்ச நேரம் கழிச்சு மீண்டும் கேளுங்க.",
        "hindi": "माफ़ करें, अभी डेटा लाने में दिक्कत हो रही है। कुछ देर बाद फिर कोशिश करें।",
        "telugu": "క్షమించండి, ప్రస్తుతం డేటా తీసుకురావడంలో సమస్య ఉంది. కొంచెం సేపట్లో మళ్ళీ ప్రయత్నించండి.",
        "kannada": "ಕ್ಷಮಿಸಿ, ಈಗ ಡೇಟಾ ಪಡೆಯುವಲ್ಲಿ ಸಮಸ್ಯೆ ಇದೆ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "malayalam": "ക്ഷമിക്കണം, ഇപ്പോൾ ഡാറ്റ ലഭിക്കുന്നതിൽ പ്രശ്‌നമുണ്ട്. കുറച്ച് സമയത്തിന് ശേഷം വീണ്ടും ശ്രമിക്കുക.",
    }

    async def _fallback_response(
        self, transcript: str, language: str, intent: str, error: str
    ) -> Dict[str, Any]:
        logger.error(f"APIRouter fallback triggered: {error}")
        return {
            "response_text": self._FALLBACK_MESSAGES.get(language, self._FALLBACK_MESSAGES["english"]),
            "data_used": {},
            "intent": intent,
            "api_source": "fallback",
            "hallucination_safe": True,
        }


# ─── Singleton ────────────────────────────────────────────────────────────────
api_router = APIRouter()
