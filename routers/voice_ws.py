"""
Production WebSocket Voice Router
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Full real-time voice pipeline over WebSocket.

Endpoints:
  WS  /api/call/ws/voice             — Primary voice conversation endpoint
  GET /api/call/health               — Health check with latency stats
  GET /api/call/debug/transcript     — Last transcript debug (dev-only)
  GET /api/call/ws/voice/info        — Protocol documentation

Pipeline (per turn):
  PCM Audio (base64) →
  STT (Gemini) →
  [parallel] Emotion Detection | Redis Context Fetch →
  Intent Classifier →
  API Router (real data fetch) →
  Gemini Formatter (hallucination-safe) →
  TTS (gTTS / Google Cloud) →
  MP3 Audio (base64) → Client

Session Features:
  ✔ 5-minute inactivity timeout
  ✔ Interrupt handling (cancels in-flight asyncio.Task)
  ✔ Auto language detection (6 Indian languages)
  ✔ Emotion-aware response injection
  ✔ Redis + SQLite cache for repeated queries
  ✔ Circuit-breaker protection on all external calls
  ✔ Backpressure: one processing task per session
"""

import json
import uuid
import asyncio
import base64
import time
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.session import session_registry, VoiceSession
from services.stt_service import stt_service
from services.intent_classifier import intent_classifier
from services.api_router import api_router
from services.tts_service import tts_service
from services.emotion_detector import emotion_detector, EMOTION_PROMPTS
from services.redis_cache import redis_cache, RedisTTL

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Protocol
# ─────────────────────────────────────────────────────────────────────────────
#
# CLIENT → SERVER:
#   { "type": "audio_data",    "data": "<base64 PCM 16kHz>" }
#   { "type": "audio_complete" }              ← silence detected, process buffer
#   { "type": "interrupt" }                   ← farmer speaks mid-response
#   { "type": "update_language", "language": "hindi" }
#   { "type": "end_session" }
#
# SERVER → CLIENT:
#   { "type": "session_started", "session_id": "...", "language": "..." }
#   { "type": "listening" }
#   { "type": "processing", "stage": "stt|intent|api|tts" }
#   { "type": "response",
#     "transcript": "...", "response_text": "...",
#     "audio_base64": "...", "language": "...",
#     "emotion": "...", "intent": "...", "latency_ms": 0 }
#   { "type": "transcript_partial", "text": "..." }
#   { "type": "interrupt_ack" }
#   { "type": "language_updated", "language": "..." }
#   { "type": "error", "message": "..." }
#   { "type": "timeout" }
#   { "type": "session_ended", "stats": {...} }
# ─────────────────────────────────────────────────────────────────────────────


# ─── Connection Manager ───────────────────────────────────────────────────────

class VoiceConnectionManager:
    """Tracks active WebSocket connections and their processing tasks."""

    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws
        logger.info(f"[WS] Session connected: {session_id}")

    def disconnect(self, session_id: str):
        self._connections.pop(session_id, None)
        self._cancel_task(session_id)
        logger.info(f"[WS] Session disconnected: {session_id}")

    def _cancel_task(self, session_id: str):
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            logger.debug(f"[WS] Task cancelled for session: {session_id}")

    def cancel_processing(self, session_id: str):
        self._cancel_task(session_id)

    def set_task(self, session_id: str, task: asyncio.Task):
        # Cancel any previous task first (backpressure)
        self._cancel_task(session_id)
        self._tasks[session_id] = task

    async def send(self, session_id: str, data: dict):
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.debug(f"[WS] Send failed ({session_id}): {e}")

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = VoiceConnectionManager()


# ─── Main WebSocket Endpoint ──────────────────────────────────────────────────

@router.websocket("/ws/voice")
async def voice_websocket(
    websocket: WebSocket,
    language: str = Query(default="tamil"),
    farmer_id: Optional[str] = Query(default=None),
):
    """
    Primary real-time voice AI WebSocket.

    Connect: ws://localhost:8000/api/call/ws/voice?language=tamil&farmer_id=<id>
    Protocol: JSON text frames (see comments above)
    """
    session_id = str(uuid.uuid4())

    # Accept and register
    await manager.connect(session_id, websocket)
    session = session_registry.create(session_id, language=language, farmer_id=farmer_id)

    db: Optional[AsyncSession] = None

    try:
        db = async_session()

        await websocket.send_json({
            "type": "session_started",
            "session_id": session_id,
            "language": session.language,
            "language_code": session.language_code,
            "features": ["stt", "emotion", "intent", "api_router", "tts", "interrupt"],
        })

        # ── Auto-Greeting (Bot speaks first like a real phone call) ────────
        _GREETINGS = {
            "tamil": "வணக்கம் அண்ணா! நான் உழவன் AI. விவசாயம் பற்றி எதாவது கேளுங்க, நான் உதவி செய்றேன்.",
            "english": "Hello! I am Uzhavan AI, your farming assistant. Ask me anything about weather, crop prices, or farming tips.",
            "hindi": "नमस्ते! मैं उझवन AI हूं, आपका खेती सहायक। मौसम, फसल भाव, या खेती के बारे में कुछ भी पूछिए।",
            "telugu": "నమస్కారం! నేను ఉళవన్ AI. వ్యవసాయం గురించి ఏదైనా అడగండి, నేను సహాయం చేస్తాను.",
            "kannada": "ನಮಸ್ಕಾರ! ನಾನು ಉಳವನ್ AI. ಕೃಷಿ ಬಗ್ಗೆ ಏನಾದರೂ ಕೇಳಿ, ನಾನು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ.",
            "malayalam": "നമസ്കാരം! ഞാൻ ഉഴവൻ AI ആണ്. കൃഷിയെ കുറിച്ച് എന്തെങ്കിലും ചോദിക്കൂ, ഞാൻ സഹായിക്കാം.",
        }
        greeting_text = _GREETINGS.get(session.language, _GREETINGS["tamil"])
        session.add_ai_message(greeting_text)

        # Send text-only greeting (frontend will use browser SpeechSynthesis — instant!)
        await websocket.send_json({
            "type": "greeting",
            "response_text": greeting_text,
            "audio_base64": "",
            "language": session.language,
        })

        await websocket.send_json({"type": "listening"})

        audio_buffer = bytearray()

        # ── Message Loop ──────────────────────────────────────────────────────
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=300.0,  # 5-minute inactivity timeout
                )
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "timeout",
                    "message": "Session timed out — no activity for 5 minutes.",
                })
                break

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = message.get("type", "")
            session.touch()

            # ── Audio Data Accumulation ───────────────────────────────────────
            if msg_type == "audio_data":
                b64 = message.get("data", "")
                if b64:
                    try:
                        chunk = base64.b64decode(b64)
                        audio_buffer.extend(chunk)
                    except Exception:
                        pass  # Ignore malformed chunk

            # ── Audio Complete — Process Buffer ───────────────────────────────
            elif msg_type == "audio_complete":
                if len(audio_buffer) < 3200:  # < 0.1s of audio
                    await websocket.send_json({"type": "listening"})
                    audio_buffer.clear()
                    continue

                audio_data = bytes(audio_buffer)
                audio_buffer.clear()

                await websocket.send_json({"type": "processing", "stage": "stt"})

                # Create cancellable processing task
                task = asyncio.create_task(
                    _process_turn(session_id, audio_data, db, websocket, session)
                )
                manager.set_task(session_id, task)

                try:
                    await task
                except asyncio.CancelledError:
                    await websocket.send_json({
                        "type": "interrupt_ack",
                        "message": "Processing cancelled — listening again.",
                    })

            # ── Text Input (Browser Speech Recognition) ────────────────────────
            elif msg_type == "text_input":
                transcript = message.get("text", "").strip()
                text_lang = message.get("language", session.language)
                if not transcript:
                    await websocket.send_json({"type": "listening"})
                    continue

                # Update language if provided
                if text_lang != session.language:
                    session.update_language(text_lang)

                await websocket.send_json({"type": "processing", "stage": "intent"})

                # Create processing task (skips STT entirely)
                task = asyncio.create_task(
                    _process_text_turn(session_id, transcript, db, websocket, session)
                )
                manager.set_task(session_id, task)

                try:
                    await task
                except asyncio.CancelledError:
                    await websocket.send_json({
                        "type": "interrupt_ack",
                        "message": "Processing cancelled — listening again.",
                    })

            # ── Interrupt ─────────────────────────────────────────────────────
            elif msg_type == "interrupt":
                manager.cancel_processing(session_id)
                session.handle_interrupt()
                audio_buffer.clear()
                await websocket.send_json({
                    "type": "interrupt_ack",
                    "message": "Stopped — listening.",
                })
                await websocket.send_json({"type": "listening"})

            # ── Language Update ───────────────────────────────────────────────
            elif msg_type == "update_language":
                new_lang = message.get("language", session.language)
                session.update_language(new_lang)
                await websocket.send_json({
                    "type": "language_updated",
                    "language": session.language,
                    "language_code": session.language_code,
                })

            # ── Voice Query (Frontend-driven AI via Puter.js) ────────────────
            elif msg_type == "voice_query":
                transcript = message.get("text", "").strip()
                query_lang = message.get("language", session.language)
                if not transcript:
                    await websocket.send_json({"type": "listening"})
                    continue

                if query_lang != session.language:
                    session.update_language(query_lang)

                session.add_farmer_message(transcript)
                await websocket.send_json({"type": "processing", "stage": "data"})

                # Rule-based intent classification (fast, no Gemini)
                intent_result = intent_classifier._classify_rules(transcript)
                intent = intent_result.get("intent", "general_query")
                crop = intent_result.get("crop")
                location = intent_result.get("location", "Tamil Nadu")

                # Fetch real API data
                api_data = await _fetch_data_for_intent(
                    intent, transcript, query_lang, crop, location, db
                )

                await websocket.send_json({
                    "type": "api_data",
                    "intent": intent,
                    "data": api_data,
                    "transcript": transcript,
                    "language": query_lang,
                    "crop": crop,
                    "location": location,
                })

            # ── TTS Request (Frontend sends formatted text for speech) ────────
            elif msg_type == "tts_request":
                tts_text = message.get("text", "").strip()
                tts_lang = message.get("language", session.language)
                if not tts_text:
                    await websocket.send_json({"type": "listening"})
                    continue

                session.add_ai_message(tts_text)
                await websocket.send_json({"type": "processing", "stage": "tts"})

                audio_b64 = ""
                try:
                    audio_b64 = await tts_service.synthesize_to_base64(
                        text=tts_text,
                        language=tts_lang,
                        emotion="neutral",
                    )
                except Exception as e:
                    logger.error(f"[WS] TTS failed: {e}")

                session.is_bot_speaking = True
                await websocket.send_json({
                    "type": "response",
                    "response_text": tts_text,
                    "audio_base64": audio_b64 or "",
                    "language": tts_lang,
                    "intent": "puter_ai",
                    "api_source": "puter_gemini",
                    "hallucination_safe": True,
                })
                session.is_bot_speaking = False

            # ── End Session ───────────────────────────────────────────────────
            elif msg_type == "end_session":
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"[WS] Session error ({session_id}): {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        manager.disconnect(session_id)
        session.end("disconnected")
        try:
            await websocket.send_json({
                "type": "session_ended",
                "stats": session.to_summary(),
            })
        except Exception:
            pass
        session_registry.remove(session_id)
        if db:
            await db.close()
        logger.info(f"[WS] Session cleaned up: {session_id}")


# ─── Core Turn Processor ──────────────────────────────────────────────────────

async def _process_turn(
    session_id: str,
    audio_data: bytes,
    db: AsyncSession,
    websocket: WebSocket,
    session: VoiceSession,
) -> None:
    """
    Full pipeline for one voice turn.

    Stages:
      1. STT           — Gemini audio transcription + language detection
      2. Emotion       — Parallel audio feature analysis
      3. Redis Context — Parallel cache / context fetch
      4. Intent        — Gemini + rule-based classifier
      5. API Router    — Real data fetch + Gemini hallucination-safe formatter
      6. TTS Cache     — Check Redis for pre-synthesized audio
      7. TTS Synthesis — gTTS / Google Cloud TTS
      8. Stream back   — Send audio + metadata to client
    """
    start_ts = time.time()
    try:
        # ── Stage 1: STT ─────────────────────────────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "stt"})
        stt_result = await stt_service.transcribe(
            audio_bytes=audio_data,
            language_hint=session.language,
            sample_rate=16000,
        )

        transcript = stt_result.get("text", "").strip()
        if not transcript:
            await websocket.send_json({
                "type": "listening",
                "message": "No speech detected — please try again.",
            })
            return

        # Update language if auto-detected
        detected_lang = stt_result.get("language", session.language)
        detected_conf = stt_result.get("confidence", 0.0)
        if detected_lang != session.language:
            session.update_language(detected_lang, detected_conf)

        session.add_farmer_message(transcript)

        # Emit partial transcript to UI
        await websocket.send_json({
            "type": "transcript_partial",
            "text": transcript,
            "language": session.language,
        })

        # ── Stage 2 + 3: Parallel (Emotion | Intent) ─────────────────────────
        await websocket.send_json({"type": "processing", "stage": "intent"})

        emotion_task = asyncio.create_task(
            asyncio.to_thread(emotion_detector.analyze_audio_bytes, audio_data, 16000)
        )
        intent_task = asyncio.create_task(
            intent_classifier.classify(transcript, session.language)
        )

        emotion_result, intent_result = await asyncio.gather(emotion_task, intent_task)

        # Update session emotion
        session.update_emotion(
            emotion_result.get("emotion", "neutral"),
            emotion_result.get("confidence", 0.0),
        )
        emotion_prompt = emotion_result.get("prompt_injection", EMOTION_PROMPTS.get("neutral", ""))

        # Extract intent metadata
        intent = intent_result.get("intent", "general_query")
        crop = intent_result.get("crop")
        location = intent_result.get("location", "Tamil Nadu")

        # ── Stage 4: API Router + Gemini Formatter ────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "api"})

        router_result = await api_router.route_and_respond(
            intent=intent,
            transcript=transcript,
            language=session.language,
            crop=crop,
            location=location,
            emotion=session.emotion,
            emotion_instruction=emotion_prompt,
            conversation_history=[
                {"role": m.role, "text": m.text}
                for m in session.conversation_history[-8:]
            ],
            db=db,
        )

        response_text = router_result.get("response_text", "")
        if not response_text:
            await websocket.send_json({"type": "listening"})
            return

        session.add_ai_message(response_text)

        # ── Stage 5: TTS (Redis cache → synth) ───────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "tts"})

        audio_b64 = await redis_cache.get_tts(
            text=response_text,
            language=session.language,
            emotion=session.emotion,
        )

        if not audio_b64:
            audio_b64 = await tts_service.synthesize_to_base64(
                text=response_text,
                language=session.language,
                emotion=session.emotion,
            )
            if audio_b64:
                # Cache common short responses
                if len(response_text) < 200:
                    asyncio.create_task(
                        redis_cache.set_tts(response_text, session.language, session.emotion, audio_b64)
                    )

        # ── Stage 6: Stream Response Back ────────────────────────────────────
        latency_ms = int((time.time() - start_ts) * 1000)
        session.record_latency(latency_ms)
        session.is_bot_speaking = True

        await websocket.send_json({
            "type": "response",
            "transcript": transcript,
            "response_text": response_text,
            "audio_base64": audio_b64 or "",
            "language": session.language,
            "language_code": session.language_code,
            "emotion": session.emotion,
            "emotion_confidence": round(session.emotion_confidence, 2),
            "intent": intent,
            "crop": crop,
            "location": location,
            "api_source": router_result.get("api_source", "unknown"),
            "hallucination_safe": router_result.get("hallucination_safe", True),
            "latency_ms": latency_ms,
            "turn": session.turn_count,
        })

        session.is_bot_speaking = False
        await websocket.send_json({"type": "listening"})

        logger.info(
            f"[WS] Turn {session.turn_count} | {session.language} | intent={intent} "
            f"| emotion={session.emotion} | latency={latency_ms}ms | session={session_id[:8]}"
        )

    except asyncio.CancelledError:
        session.is_bot_speaking = False
        raise
    except Exception as e:
        logger.error(f"[WS] Turn processing error ({session_id}): {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Processing error — please speak again.",
            })
            await websocket.send_json({"type": "listening"})
        except Exception:
            pass


# ─── Text-Input Turn Processor (Browser STT) ─────────────────────────────────

async def _process_text_turn(
    session_id: str,
    transcript: str,
    db: AsyncSession,
    websocket: WebSocket,
    session: VoiceSession,
) -> None:
    """
    Streamlined pipeline for text input from browser Speech Recognition.
    Skips STT and emotion stages (saves 2 Gemini API calls per turn).
    
    Stages:
      1. Intent classification
      2. API Router + Gemini formatter
      3. TTS synthesis
      4. Stream response back
    """
    start_ts = time.time()
    try:
        session.add_farmer_message(transcript)

        # Emit transcript to UI
        await websocket.send_json({
            "type": "transcript_partial",
            "text": transcript,
            "language": session.language,
        })

        # ── Stage 1: Intent Classification ────────────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "intent"})

        intent_result = await intent_classifier.classify(transcript, session.language)
        intent = intent_result.get("intent", "general_query")
        crop = intent_result.get("crop")
        location = intent_result.get("location", "Tamil Nadu")

        # ── Stage 2: API Router + Gemini Formatter ────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "api"})

        router_result = await api_router.route_and_respond(
            intent=intent,
            transcript=transcript,
            language=session.language,
            crop=crop,
            location=location,
            emotion="neutral",
            emotion_instruction="",
            conversation_history=[
                {"role": m.role, "text": m.text}
                for m in session.conversation_history[-8:]
            ],
            db=db,
        )

        response_text = router_result.get("response_text", "")
        if not response_text:
            await websocket.send_json({"type": "listening"})
            return

        session.add_ai_message(response_text)

        # ── Stage 3: TTS ──────────────────────────────────────────────────────
        await websocket.send_json({"type": "processing", "stage": "tts"})

        audio_b64 = await redis_cache.get_tts(
            text=response_text,
            language=session.language,
            emotion="neutral",
        )

        if not audio_b64:
            audio_b64 = await tts_service.synthesize_to_base64(
                text=response_text,
                language=session.language,
                emotion="neutral",
            )
            if audio_b64 and len(response_text) < 200:
                asyncio.create_task(
                    redis_cache.set_tts(response_text, session.language, "neutral", audio_b64)
                )

        # ── Stage 4: Response ─────────────────────────────────────────────────
        latency_ms = int((time.time() - start_ts) * 1000)
        session.record_latency(latency_ms)
        session.is_bot_speaking = True

        await websocket.send_json({
            "type": "response",
            "transcript": transcript,
            "response_text": response_text,
            "audio_base64": audio_b64 or "",
            "language": session.language,
            "language_code": session.language_code,
            "emotion": "neutral",
            "emotion_confidence": 0.0,
            "intent": intent,
            "crop": crop,
            "location": location,
            "api_source": router_result.get("api_source", "unknown"),
            "hallucination_safe": router_result.get("hallucination_safe", True),
            "latency_ms": latency_ms,
            "turn": session.turn_count,
        })

        session.is_bot_speaking = False
        await websocket.send_json({"type": "listening"})

        logger.info(
            f"[WS-Text] Turn {session.turn_count} | {session.language} | intent={intent} "
            f"| latency={latency_ms}ms | session={session_id[:8]}"
        )

    except asyncio.CancelledError:
        session.is_bot_speaking = False
        raise
    except Exception as e:
        logger.error(f"[WS-Text] Turn error ({session_id}): {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Processing error — please speak again.",
            })
            await websocket.send_json({"type": "listening"})
        except Exception:
            pass

# ─── Data Fetcher (No Gemini — supplies raw data for frontend AI) ────────────

async def _fetch_data_for_intent(
    intent: str,
    transcript: str,
    language: str,
    crop: Optional[str],
    location: str,
    db,
) -> Optional[Any]:
    """Fetch real API data based on intent. No Gemini calls."""
    try:
        if intent == "market_price":
            from services.mandi_service import mandi_service
            from services.cache_service import cache_manager, CacheConfig
            cache_key = f"voice:price:{(location or 'Tamil Nadu').lower()}:{(crop or 'all').lower()}"
            cached, _ = await cache_manager.get(cache_key)
            if cached:
                return cached
            prices = await asyncio.wait_for(
                mandi_service.get_today_prices(
                    db=db, state=location or "Tamil Nadu",
                    commodity=crop or None, limit=5,
                ),
                timeout=5.0,
            )
            if prices:
                data = [
                    {
                        "commodity": p.commodity,
                        "market": p.market,
                        "district": p.district,
                        "min_price": float(p.min_price or 0),
                        "max_price": float(p.max_price or 0),
                        "modal_price": float(p.modal_price or 0),
                        "unit": "quintal",
                    }
                    for p in prices[:5]
                ]
                await cache_manager.set(cache_key, data, CacheConfig.MANDI_PRICES_TODAY, "prices")
                return data

        elif intent == "weather":
            from services.weather_service import weather_service
            from services.cache_service import cache_manager, CacheConfig
            cache_key = f"voice:weather:{(location or 'Tamil Nadu').lower()}"
            cached, _ = await cache_manager.get(cache_key)
            if cached:
                return cached
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
                await cache_manager.set(cache_key, data, CacheConfig.WEATHER_CURRENT, "weather")
                return data

        elif intent == "news":
            from services.cache_service import cache_manager, CacheConfig
            cache_key = f"voice:news:{language}"
            cached, _ = await cache_manager.get(cache_key)
            if cached:
                return cached
            
            from routers.agriculture_news import fetch_rss_feeds
            # We fetch rss feeds and just take the top 1
            feeds = await asyncio.wait_for(fetch_rss_feeds(), timeout=5.0)
            if feeds and len(feeds) > 0:
                top_news = feeds[0]
                data = {
                    "headline": top_news.get("title", "No news"),
                    "summary": top_news.get("summary", "No summary available")
                }
                await cache_manager.set(cache_key, data, CacheConfig.NEWS_FEED, "news")
                return data

        elif intent == "disease":
            # Disease relies on images, so we just return a flag indicating to ask the user to upload a photo
            return {"action": "upload_photo"}

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"[DataFetch] Error for intent={intent}: {e}")

    return None


# ─── REST Endpoints ───────────────────────────────────────────────────────────

@router.get("/health")
async def voice_health():
    """Comprehensive health check for the Voice AI subsystem."""
    redis_status = await redis_cache.health_check()
    sessions = session_registry.stats()

    return {
        "status": "healthy",
        "service": "uzhavan-voice-ai",
        "version": "3.0.0",
        "active_connections": manager.active_count,
        "sessions": sessions,
        "redis": redis_status,
        "features": {
            "stt": "gemini-2.0-flash",
            "tts": "google-cloud-tts + gtts-fallback",
            "intent_classifier": "gemini-primary + rule-based-fallback",
            "api_router": "hallucination-safe",
            "emotion_detector": "audio-feature-analysis",
            "languages": ["tamil", "english", "hindi", "malayalam", "kannada", "telugu"],
        },
        "latency_target_ms": 1200,
    }


@router.get("/debug/transcript")
async def debug_transcript():
    """Debug endpoint — shows last processed transcript per active session."""
    sessions = session_registry.get_all_active()
    return {
        "active_sessions": [
            {
                "session_id": s.session_id[:8] + "...",
                "language": s.language,
                "last_transcript": s.last_transcript,
                "last_intent": s.last_intent,
                "last_response": s.last_response_text[:100] if s.last_response_text else "",
                "turn_count": s.turn_count,
                "emotion": s.emotion,
                "latency_ms": round(s.avg_latency_ms, 1),
                "idle_seconds": round(s.idle_seconds, 1),
            }
            for s in sessions
        ],
        "total": len(sessions),
    }


@router.get("/ws/voice/info")
async def voice_ws_info():
    """WebSocket protocol documentation."""
    return {
        "endpoint": "/api/call/ws/voice",
        "protocol": "WebSocket (JSON text frames)",
        "query_params": {
            "language": "Initial language (tamil/english/hindi/malayalam/kannada/telugu)",
            "farmer_id": "Optional farmer ID for personalization",
        },
        "session_timeout_seconds": 300,
        "features": [
            "Real-time streaming voice (< 1.2s latency target)",
            "Auto language detection (6 Indian languages)",
            "Emotion-aware responses (worried/angry/confused/happy/neutral)",
            "Interrupt handling (cancel in-flight processing)",
            "Intent classification (market/weather/disease/news/general)",
            "Hallucination-safe (Gemini only formats real API data)",
            "Redis cache for ultra-fast repeat queries",
            "TTS audio caching for common phrases",
        ],
        "supported_languages": [
            {"name": "tamil",    "bcp47": "ta-IN"},
            {"name": "english",  "bcp47": "en-IN"},
            {"name": "hindi",    "bcp47": "hi-IN"},
            {"name": "malayalam","bcp47": "ml-IN"},
            {"name": "kannada",  "bcp47": "kn-IN"},
            {"name": "telugu",   "bcp47": "te-IN"},
        ],
        "client_message_types": [
            "audio_data", "audio_complete", "interrupt",
            "update_language", "end_session",
        ],
        "server_message_types": [
            "session_started", "listening", "processing",
            "transcript_partial", "response", "interrupt_ack",
            "language_updated", "error", "timeout", "session_ended",
        ],
        "pipeline": [
            "PCM 16kHz audio (base64)",
            "STT: Gemini Audio Understanding",
            "Emotion: Audio feature analysis (parallel)",
            "Intent: Gemini + rule-based classifier",
            "API Router: Real data fetch (market/weather/disease/news)",
            "Gemini: Hallucination-safe formatter",
            "TTS: Google Cloud TTS / gTTS fallback",
            "MP3 audio (base64) → client",
        ],
    }
