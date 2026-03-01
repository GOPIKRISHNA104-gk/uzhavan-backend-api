from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from services.voice_service import voice_service
import shutil
import tempfile
import os

router = APIRouter(prefix="/api/voice", tags=["voice"])

@router.post("/query")
async def process_voice(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Process voice query from frontend.
    1. Save uploaded audio to temp file.
    2. Pass to VoiceService (Gemini STT -> Intent -> Data -> Gemini -> TTS).
    3. Return transcript, answer text, and audio base64.
    """
    try:
        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
            shutil.copyfileobj(file.file, temp_audio)
            temp_path = temp_audio.name
        
        # Process
        result = await voice_service.process_voice_query(temp_path, db)
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
        if "error" in result:
             raise HTTPException(status_code=500, detail=result["error"])
             
        return result
        
    except Exception as e:
        if 'temp_path' in locals() and os.path.exists(temp_path):
             os.remove(temp_path)
        raise HTTPException(status_code=500, detail=str(e))
