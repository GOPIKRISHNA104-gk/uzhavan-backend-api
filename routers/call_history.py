"""
Call History Router - Save and retrieve voice call recordings + transcripts

Enterprise Features:
- Audio recording storage (WebM/MP3)
- Full transcript persistence
- Metadata: duration, language, timestamps
- Authenticated access via Firebase token
- Auto-cleanup after 90 days
- Protected file serving (not public)
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timedelta
import os
import uuid

from database import get_db, CallHistory
from firebase_admin_config import get_current_firebase_user, FirebaseUser

router = APIRouter()

# Protected recordings directory
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "protected", "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)


@router.post("/save")
async def save_call(
    audio: UploadFile = File(None),
    transcript: str = Form(""),
    duration: float = Form(0),
    language: str = Form("tamil"),
    start_time: str = Form(""),
    end_time: str = Form(""),
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save a voice call recording + transcript.
    Audio file is optional (may fail to record on some devices).
    """
    user_id = firebase_user.uid

    # Parse timestamps
    try:
        start_dt = datetime.fromisoformat(start_time) if start_time else datetime.utcnow()
    except:
        start_dt = datetime.utcnow()

    try:
        end_dt = datetime.fromisoformat(end_time) if end_time else datetime.utcnow()
    except:
        end_dt = datetime.utcnow()

    # Save audio file if provided
    audio_path = None
    if audio and audio.filename:
        # Generate unique filename
        ext = os.path.splitext(audio.filename)[1] or ".webm"
        filename = f"{user_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(RECORDINGS_DIR, filename)

        # Write file (limit to 50MB)
        content = await audio.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio file too large (max 50MB)"
            )

        with open(filepath, "wb") as f:
            f.write(content)

        audio_path = f"protected/recordings/{filename}"

    # Save to database
    call_record = CallHistory(
        user_id=user_id,
        language=language,
        transcript=transcript or "",
        audio_path=audio_path,
        start_time=start_dt,
        end_time=end_dt,
        duration_seconds=int(duration),
    )

    db.add(call_record)
    await db.flush()

    return {
        "status": "saved",
        "call_id": call_record.id,
        "duration_seconds": int(duration),
        "has_audio": audio_path is not None,
        "has_transcript": bool(transcript),
    }


@router.get("/history")
async def get_call_history(
    limit: int = 20,
    offset: int = 0,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db),
):
    """Get call history for the authenticated user."""
    user_id = firebase_user.uid

    result = await db.execute(
        select(CallHistory)
        .where(CallHistory.user_id == user_id)
        .order_by(desc(CallHistory.created_at))
        .limit(limit)
        .offset(offset)
    )
    calls = result.scalars().all()

    # Count total
    count_result = await db.execute(
        select(CallHistory.id).where(CallHistory.user_id == user_id)
    )
    total = len(count_result.all())

    return {
        "calls": [
            {
                "id": c.id,
                "language": c.language,
                "transcript": c.transcript,
                "has_audio": c.audio_path is not None,
                "start_time": c.start_time.isoformat() if c.start_time else None,
                "end_time": c.end_time.isoformat() if c.end_time else None,
                "duration_seconds": c.duration_seconds,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in calls
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/audio/{call_id}")
async def get_call_audio(
    call_id: int,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Serve audio file for a specific call.
    Only the call owner can access their recordings (authenticated route).
    """
    result = await db.execute(
        select(CallHistory)
        .where(CallHistory.id == call_id, CallHistory.user_id == firebase_user.uid)
    )
    call = result.scalar_one_or_none()

    if not call or not call.audio_path:
        raise HTTPException(status_code=404, detail="Recording not found")

    filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), call.audio_path)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Audio file missing")

    # Determine media type
    ext = os.path.splitext(filepath)[1].lower()
    media_types = {".webm": "audio/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}
    media_type = media_types.get(ext, "audio/webm")

    return FileResponse(filepath, media_type=media_type)


@router.delete("/{call_id}")
async def delete_call(
    call_id: int,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific call record and its audio file."""
    result = await db.execute(
        select(CallHistory)
        .where(CallHistory.id == call_id, CallHistory.user_id == firebase_user.uid)
    )
    call = result.scalar_one_or_none()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Delete audio file if exists
    if call.audio_path:
        filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), call.audio_path)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass

    await db.delete(call)

    return {"status": "deleted", "call_id": call_id}


@router.post("/cleanup")
async def cleanup_old_recordings(
    db: AsyncSession = Depends(get_db),
):
    """Auto-delete recordings older than 90 days (admin endpoint)."""
    cutoff = datetime.utcnow() - timedelta(days=90)

    result = await db.execute(
        select(CallHistory).where(CallHistory.created_at < cutoff)
    )
    old_calls = result.scalars().all()

    deleted = 0
    for call in old_calls:
        if call.audio_path:
            filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), call.audio_path)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
        await db.delete(call)
        deleted += 1

    return {"deleted": deleted, "cutoff_date": cutoff.isoformat()}
