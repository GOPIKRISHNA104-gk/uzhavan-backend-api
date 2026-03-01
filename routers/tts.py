"""
TTS Router — Google Translate Text-to-Speech proxy
Bypasses CORS by fetching audio server-side
"""

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from gtts import gTTS
import io
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/speak")
async def text_to_speech(
    text: str = Query(..., description="Text to speak"),
    lang: str = Query("ta", description="Language code: ta, en, hi, te, kn, ml"),
):
    """Convert text to speech audio (MP3) using Google Translate TTS"""
    try:
        # Limit text length for safety
        safe_text = text[:1000]

        # Map language codes
        lang_map = {
            "ta": "ta", "en": "en", "hi": "hi",
            "te": "te", "kn": "kn", "ml": "ml",
            "ta-IN": "ta", "en-IN": "en", "hi-IN": "hi",
        }
        tts_lang = lang_map.get(lang, "ta")

        # Generate audio
        tts = gTTS(text=safe_text, lang=tts_lang, slow=False)
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)

        return StreamingResponse(
            audio_buffer,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "public, max-age=3600",
            },
        )
    except Exception as e:
        logger.error(f"TTS error: {e}")
        # Return empty audio on error
        return StreamingResponse(
            io.BytesIO(b""),
            media_type="audio/mpeg",
        )
