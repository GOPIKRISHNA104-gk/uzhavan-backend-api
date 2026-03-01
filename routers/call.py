"""
Phone Call Router - Voice-based AI assistant
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
import shutil
import tempfile
import os

from database import get_db, User
from schemas import CallResponse
from config import settings
from auth_deps import get_current_user
from services.voice_service import voice_service

router = APIRouter()

@router.post("/voice", response_model=CallResponse)
async def process_voice_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Process voice query (Audio File).
    1. STT -> Intent -> Data -> Answer -> TTS
    2. Returns audio base64 + text
    """
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="Voice service unavailable (API Key missing)")

    temp_path = None
    try:
        # Save upload to temp file
        # Check file extension or content type if needed, but Gemini handles most audio
        suffix = os.path.splitext(file.filename)[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_audio:
            shutil.copyfileobj(file.file, temp_audio)
            temp_path = temp_audio.name
        
        # Process via VoiceService
        result = await voice_service.process_voice_query(temp_path, db)
        
        if "error" in result:
             raise HTTPException(status_code=500, detail=result["error"])

        return CallResponse(
            response_text=result["text"],
            audio_base64=result["audio"],
            language=result.get("language", "english")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice processing invalid: {str(e)}")
    finally:
        # Cleanup input file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

@router.get("/supported-languages")
async def get_supported_languages():
    """Get list of supported languages for voice calls"""
    return {
        "languages": [
            {"code": "english", "name": "English", "native": "English"},
            {"code": "tamil", "name": "Tamil", "native": "தமிழ்"},
            {"code": "hindi", "name": "Hindi", "native": "हिंदी"},
            {"code": "telugu", "name": "Telugu", "native": "తెలుగు"},
            {"code": "kannada", "name": "Kannada", "native": "ಕನ್ನಡ"},
            {"code": "malayalam", "name": "Malayalam", "native": "മലയാളം"}
        ]
    }
