"""
Real-Time WebRTC Voice Service for Uzhavan AI
Streaming pipeline: Audio -> STT -> Emotion -> Gemini -> TTS -> Audio
Supports 6 Indian languages with auto language detection.
"""

import os
import json
import asyncio
import base64
import tempfile
import time
from typing import Dict, Any, Optional, AsyncGenerator
from dataclasses import dataclass, field

import google.generativeai as genai
from services.emotion_detector import emotion_detector, EMOTION_PROMPTS, Emotion
from services.tts_service import tts_service
from services.mandi_service import mandi_service
from services.weather_service import weather_service
from sqlalchemy.ext.asyncio import AsyncSession

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# Language mapping
LANGUAGE_MAP = {
    "ta-IN": "tamil",
    "en-IN": "english",
    "hi-IN": "hindi",
    "ml-IN": "malayalam",
    "kn-IN": "kannada",
    "te-IN": "telugu",
    "tamil": "tamil",
    "english": "english",
    "hindi": "hindi",
    "malayalam": "malayalam",
    "kannada": "kannada",
    "telugu": "telugu",
}

STT_LANGUAGE_CODES = ["ta-IN", "en-IN", "hi-IN", "ml-IN", "kn-IN", "te-IN"]

# Village-tone system prompt template
VILLAGE_SYSTEM_PROMPT = """You are Uzhavan AI.
Speak like a friendly village agriculture expert.
Use simple spoken {language}.
Avoid scientific jargon.
Use practical examples from farming experience.
Keep answers short (maximum 2-3 sentences) suitable for voice conversation.
If farmer interrupts, stop immediately.
Be respectful and supportive.
Never mix languages - respond ONLY in {language}.

{emotion_context}

{data_context}
"""


@dataclass
class VoiceSession:
    """Tracks state for an active voice session"""
    session_id: str
    language: str = "tamil"
    language_code: str = "ta-IN"
    emotion: str = "neutral"
    emotion_confidence: float = 0.0
    is_active: bool = True
    is_bot_speaking: bool = False
    conversation_history: list = field(default_factory=list)
    audio_buffer: bytes = b""
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def update_language(self, lang_code: str):
        """Update session language from STT detection"""
        lang = LANGUAGE_MAP.get(lang_code, None)
        if lang:
            self.language = lang
            self.language_code = lang_code

    def add_to_history(self, role: str, text: str):
        """Add message to conversation history (keep last 10)"""
        self.conversation_history.append({"role": role, "text": text})
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]


class WebRTCVoiceService:
    """
    Real-time streaming voice pipeline.
    Manages voice sessions and processes audio through the full pipeline.
    """

    def __init__(self):
        self.model = genai.GenerativeModel("gemini-2.0-flash")
        self.sessions: Dict[str, VoiceSession] = {}

    def create_session(self, session_id: str, language: str = "tamil") -> VoiceSession:
        """Create a new voice session"""
        session = VoiceSession(
            session_id=session_id,
            language=language,
            language_code=self._get_language_code(language),
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[VoiceSession]:
        """Get existing session"""
        return self.sessions.get(session_id)

    def remove_session(self, session_id: str):
        """Remove session on disconnect"""
        self.sessions.pop(session_id, None)

    async def process_audio_chunk(
        self,
        session_id: str,
        audio_data: bytes,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Process a complete audio chunk (after silence detection).
        Full pipeline: STT -> Emotion -> Context -> Gemini -> TTS
        
        Returns dict with response audio and metadata.
        """
        session = self.get_session(session_id)
        if not session:
            return {"error": "Session not found"}

        session.last_activity = time.time()
        start_time = time.time()

        try:
            # Step 1: Speech-to-Text using Gemini Audio Understanding
            stt_result = await self._speech_to_text(audio_data, session)
            
            if not stt_result.get("text"):
                return {"type": "no_speech", "message": "No speech detected"}

            transcript = stt_result["text"]
            detected_language = stt_result.get("language", session.language)
            
            # Update session language if changed
            if detected_language != session.language:
                session.language = detected_language
                session.language_code = self._get_language_code(detected_language)

            session.add_to_history("farmer", transcript)

            # Step 2: Emotion Detection (parallel with context fetch)
            emotion_task = asyncio.create_task(
                asyncio.to_thread(emotion_detector.analyze_audio_bytes, audio_data, 16000)
            )

            # Step 3: Fetch relevant context data
            context_task = asyncio.create_task(
                self._get_context_data(transcript, detected_language, db)
            )

            # Wait for both
            emotion_result, context_data = await asyncio.gather(
                emotion_task, context_task
            )

            session.emotion = emotion_result.get("emotion", "neutral")
            session.emotion_confidence = emotion_result.get("confidence", 0.0)

            # Step 4: Generate AI Response with Gemini
            ai_response = await self._generate_response(
                transcript=transcript,
                language=detected_language,
                emotion=session.emotion,
                emotion_prompt=emotion_result.get("prompt_injection", ""),
                context_data=context_data,
                history=session.conversation_history,
            )

            if not ai_response:
                return {"type": "error", "message": "Failed to generate response"}

            session.add_to_history("ai", ai_response)

            # Step 5: Text-to-Speech
            session.is_bot_speaking = True
            audio_base64 = await tts_service.synthesize_to_base64(
                text=ai_response,
                language=detected_language,
                emotion=session.emotion,
            )
            session.is_bot_speaking = False

            elapsed = time.time() - start_time

            return {
                "type": "response",
                "transcript": transcript,
                "response_text": ai_response,
                "audio_base64": audio_base64,
                "language": detected_language,
                "emotion": session.emotion,
                "emotion_confidence": session.emotion_confidence,
                "latency_ms": round(elapsed * 1000),
            }

        except asyncio.CancelledError:
            session.is_bot_speaking = False
            return {"type": "interrupted", "message": "Response interrupted by farmer"}
        except Exception as e:
            session.is_bot_speaking = False
            print(f"Voice pipeline error: {e}")
            return {"type": "error", "message": str(e)}

    async def handle_interrupt(self, session_id: str) -> Dict[str, Any]:
        """
        Handle farmer interruption.
        Immediately stops bot from speaking and prepares for new input.
        """
        session = self.get_session(session_id)
        if session:
            session.is_bot_speaking = False
            return {"type": "interrupt_ack", "message": "Bot stopped, listening..."}
        return {"type": "error", "message": "No active session"}

    async def _speech_to_text(
        self, audio_data: bytes, session: VoiceSession
    ) -> Dict[str, Any]:
        """
        Use Gemini to transcribe audio and detect language.
        Gemini handles multi-language audio natively.
        """
        try:
            # Save audio to temp file for Gemini upload
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".wav", mode="wb"
            ) as f:
                # Write WAV header for 16-bit PCM mono 16kHz
                self._write_wav_header(f, audio_data, 16000, 1, 16)
                f.write(audio_data)
                temp_path = f.name

            try:
                # Upload to Gemini
                sample_file = await asyncio.to_thread(
                    genai.upload_file, path=temp_path, display_name="farmer_voice"
                )

                language_hint = session.language
                prompt = f"""Listen to this audio carefully.
The farmer may be speaking in one of these languages: Tamil, English, Hindi, Malayalam, Kannada, or Telugu.
Language hint: {language_hint}

Return JSON with:
- "text": exact transcription of what was spoken
- "language": detected language name in lowercase (tamil/english/hindi/malayalam/kannada/telugu)
- "confidence": transcription confidence 0.0-1.0

If no clear speech, return {{"text": "", "language": "{language_hint}", "confidence": 0.0}}"""

                response = await asyncio.to_thread(
                    self.model.generate_content,
                    [sample_file, prompt],
                    generation_config={"response_mime_type": "application/json"},
                )

                result = json.loads(response.text)
                return result

            finally:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except:
                        pass

        except Exception as e:
            print(f"STT Error: {e}")
            return {"text": "", "language": session.language, "confidence": 0.0}

    def _write_wav_header(self, f, data: bytes, sample_rate: int, channels: int, bits: int):
        """Write a minimal WAV header"""
        import struct
        data_size = len(data)
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + data_size))
        f.write(b'WAVE')
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))  # chunk size
        f.write(struct.pack('<H', 1))   # PCM format
        f.write(struct.pack('<H', channels))
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * channels * bits // 8))
        f.write(struct.pack('<H', channels * bits // 8))
        f.write(struct.pack('<H', bits))
        f.write(b'data')
        f.write(struct.pack('<I', data_size))

    async def _get_context_data(
        self, transcript: str, language: str, db: AsyncSession
    ) -> str:
        """
        Analyze transcript and fetch relevant market/weather data for context.
        """
        try:
            # Use Gemini to extract intent quickly
            intent_prompt = f"""Analyze this farmer's question: "{transcript}"
Return JSON:
- "intent": "market_price", "weather", "crop_advice", "disease", or "general"
- "crop": crop name if mentioned, else null
- "location": location if mentioned, else "Tamil Nadu"
"""
            response = await asyncio.to_thread(
                self.model.generate_content,
                intent_prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            
            intent_data = json.loads(response.text)
            intent = intent_data.get("intent", "general")
            crop = intent_data.get("crop")
            location = intent_data.get("location", "Tamil Nadu")

            context = ""

            if intent == "market_price" and crop:
                prices = await mandi_service.get_today_prices(
                    db=db, state=location, commodity=crop, limit=5
                )
                if not prices:
                    prices = await mandi_service.get_today_prices(
                        db=db, district=location, commodity=crop, limit=5
                    )
                
                if prices:
                    price_list = [
                        {
                            "commodity": p.commodity,
                            "market": p.market,
                            "modal_price": p.modal_price,
                        }
                        for p in prices[:3]
                    ]
                    context = f"Market Prices: {json.dumps(price_list, default=str)}"
                else:
                    context = f"No market price data available for {crop} today."

            elif intent == "weather":
                weather_data = await weather_service.get_weather_for_location(
                    db, location
                )
                if weather_data:
                    current = weather_data.get("current", {})
                    context = f"Weather in {location}: Temperature {current.get('temperature_2m', 'N/A')}C"

            return context

        except Exception as e:
            print(f"Context fetch error: {e}")
            return ""

    async def _generate_response(
        self,
        transcript: str,
        language: str,
        emotion: str,
        emotion_prompt: str,
        context_data: str,
        history: list,
    ) -> str:
        """
        Generate AI response using Gemini with language + emotion context.
        """
        try:
            # Build conversation context from history
            history_text = ""
            for msg in history[-6:]:  # Last 3 exchanges
                role = "Farmer" if msg["role"] == "farmer" else "AI"
                history_text += f"{role}: {msg['text']}\n"

            system_prompt = VILLAGE_SYSTEM_PROMPT.format(
                language=language,
                emotion_context=f"Emotion detected: {emotion}. {emotion_prompt}" if emotion != "neutral" else "",
                data_context=f"Context Data:\n{context_data}" if context_data else "",
            )

            full_prompt = f"""{system_prompt}

Conversation so far:
{history_text}

Farmer says: "{transcript}"

Respond ONLY in {language}. Keep it short for voice (2-3 sentences max)."""

            response = await asyncio.to_thread(
                self.model.generate_content, full_prompt
            )

            return response.text.strip()

        except Exception as e:
            print(f"Gemini response error: {e}")
            return ""

    def _get_language_code(self, language: str) -> str:
        """Convert language name to BCP-47 code"""
        code_map = {
            "tamil": "ta-IN",
            "english": "en-IN",
            "hindi": "hi-IN",
            "malayalam": "ml-IN",
            "kannada": "kn-IN",
            "telugu": "te-IN",
        }
        return code_map.get(language.lower(), "ta-IN")


# Singleton
webrtc_voice_service = WebRTCVoiceService()
