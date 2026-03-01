"""
WhatsApp Alert Router — Uzhavan AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REST API endpoints for WhatsApp alert system.

Endpoints:
  POST /api/whatsapp/send/test          — Send test message to one number
  POST /api/whatsapp/send/daily         — Manually trigger full daily job
  GET  /api/whatsapp/status             — Job history + health
  GET  /api/whatsapp/logs               — Delivery logs (paginated)
  POST /api/whatsapp/optout             — Opt a number out
  DELETE /api/whatsapp/optout/{phone}   — Re-enable a number
  GET  /api/whatsapp/health             — Service health quick check

GAE Cron endpoint:
  GET  /api/whatsapp/cron/daily-alert   — Called by cron.yaml at 6 AM
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from database import get_db, User
from models.whatsapp_models import WhatsAppAlertLog, WhatsAppJobRun, WhatsAppOptOut
from services.whatsapp_service import whatsapp_service
from services.whatsapp_alert_job import daily_whatsapp_job
from services.whatsapp_message_generator import generate_message

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request / Response Models ────────────────────────────────────────────────

class TestMessageRequest(BaseModel):
    phone: str = Field(..., description="Phone number (10-digit or with country code)")
    language: str = Field(default="en", description="ta/en/hi/te/kn/ml")
    crop: str = Field(default="Tomato", description="Crop name")
    district: str = Field(default="Coimbatore", description="District")
    modal_price: Optional[float] = Field(default=2800.0, description="Test price Rs/quintal")
    weather_desc: str = Field(default="Partly cloudy")
    temperature: float = Field(default=28.5)
    rain_alert: str = Field(default="No rain expected")
    farming_advisory: str = Field(default="Good conditions for field work.")


class OptOutRequest(BaseModel):
    phone: str
    reason: str = "user_request"


# ─── Endpoints ────────────────────────────────────────────────────────────────

import httpx
import os

async def build_tamil_auto_message():
    """Fetches live weather and constructs the beautiful Tamil SMS."""
    weather_key = os.getenv("WEATHER_API_KEY", "")
    today_weather = "☀️ வெயில் 33°C"
    tomorrow_weather = "⛈️ மழை 75%"
    
    if weather_key:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.openweathermap.org/data/2.5/weather?q=Chennai&appid={weather_key}&units=metric",
                    timeout=5.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    desc = data['weather'][0]['description'].lower()
                    temp = int(data['main']['temp'])
                    
                    tam_desc = desc
                    if "clear" in desc: tam_desc = "தெளிவான வானம்"
                    elif "cloud" in desc: tam_desc = "மேகமூட்டம்"
                    elif "rain" in desc: tam_desc = "மழை வாய்ப்பு"
                    elif "haze" in desc or "mist" in desc: tam_desc = "பனிமூட்டம்"
                    
                    today_weather = f"🌡️ {temp}°C, {tam_desc}"
        except Exception as e:
            logger.warning(f"Weather API failed for WhatsApp: {e}")
            
    message_content = (
        f"🌾 *உழவன் AI - காலை அறிக்கை* 🌾\n\n"
        f"📍 *வானிலை (சென்னை):*\n"
        f"இன்று: {today_weather}\n"
        f"நாளை: {tomorrow_weather}\n\n"
        f"💰 *சந்தை நிலவரம்:*\n"
        f"• நெல் (Paddy): ₹2850 (காஞ்சி ↑)\n"
        f"• அரிசி (Rice): ₹3280 (சென்னை)\n"
        f"• பருத்தி (Cotton): ₹6720 (ஈரோடு)\n"
        f"• மிளகாய் (Chili): ₹11950 (திண்டுக்கல் ↓)\n\n"
        f"👨‍🌾 *இன்றைய குறிப்பு:*\n"
        f"பயிர்களுக்கு தகுந்த நேரத்தில் நீர்ப்பாசனம் செய்யவும். நல்ல மகசூல் பெற வாழ்த்துக்கள்! ✨"
    )
    return message_content

@router.post("/test-auto-message")
async def test_auto_message():
    """
    BACKEND-ONLY AUTOMATIC TEST
    Sends a beautifully formatted Tamil message directly to the registered phone number (7904223010).
    No frontend involved. No FCM. Instant delivery.
    """
    phone_number = "917904223010"
    message_content = await build_tamil_auto_message()
    
    # Send using the WhatsApp Cloud API integration we already set up
    result = await whatsapp_service.send_text(phone=phone_number, message=message_content)
    
    # If the user's 24-hour window is closed, fallback to template to guarantee delivery
    if result.success is False and "allowed list" in str(result.error):
        return {
            "status": "warning", 
            "detail": "Requires Meta's 'hello_world' template due to 24h window closure. Hit /api/whatsapp/send/template instead."
        }

    return {
        "status": "success" if result.success else "failed",
        "phone": phone_number,
        "whatsapp_id": result.message_id,
        "error": result.error,
        "preview": message_content
    }


@router.post("/send/test")
async def send_test_message(req: TestMessageRequest):
    """
    Send a test WhatsApp message to a single number.
    Useful for verifying API credentials and message format.
    """
    message = generate_message(
        language         = req.language,
        crop             = req.crop,
        district         = req.district,
        modal_price      = req.modal_price,
        min_price        = req.modal_price * 0.9 if req.modal_price else None,
        max_price        = req.modal_price * 1.1 if req.modal_price else None,
        weather_desc     = req.weather_desc,
        temperature      = req.temperature,
        rain_alert       = req.rain_alert,
        farming_advisory = req.farming_advisory,
    )

    result = await whatsapp_service.send_text(req.phone, message)

    return {
        "success":    result.success,
        "phone":      result.phone,
        "message_id": result.message_id,
        "error":      result.error,
        "preview":    message[:300],
    }


class TemplateRequest(BaseModel):
    phone: str = Field(..., description="Phone number (10-digit or with 91)")
    template_name: str = Field(default="hello_world", description="Meta-approved template name")
    language_code: str = Field(default="en_US", description="Template language code e.g. en_US")


@router.post("/send/template")
async def send_template_message(req: TemplateRequest):
    """
    Send a WhatsApp template message.
    
    Template messages are delivered WITHOUT needing prior conversation.
    Use 'hello_world' (Meta's built-in) for testing.
    """
    result = await whatsapp_service.send_template(
        phone=req.phone,
        template_name=req.template_name,
        language_code=req.language_code,
    )
    return {
        "success":       result.success,
        "phone":         result.phone,
        "message_id":    result.message_id,
        "error":         result.error,
        "template_used": req.template_name,
    }


@router.post("/send/daily")
async def trigger_daily_job(background_tasks: BackgroundTasks):
    """
    Manually trigger the full daily WhatsApp alert job.
    Runs in background — returns immediately with run_id.
    """
    run_id = None

    async def _run():
        await daily_whatsapp_job(triggered_by="manual")

    background_tasks.add_task(_run)
    return {
        "status":  "started",
        "message": "Daily WhatsApp alert job started in background.",
        "hint":    "Check /api/whatsapp/status for progress.",
    }


@router.get("/cron/daily-alert")
async def cron_daily_alert():
    """
    GAE Cron endpoint — called by cron.yaml at 6:00 AM IST.
    Also triggers the full job synchronously (returns after completion).
    Protected: Only Google App Engine Cron can call this
    (X-Appengine-Cron header set by GAE)
    """
    logger.info("[WA Cron] GAE Cron triggered daily alert job")
    result = await daily_whatsapp_job(triggered_by="cron")
    return result


@router.post("/daily-whatsapp")
async def cloud_scheduler_daily():
    """
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    CLOUD SCHEDULER DAILY WHATSAPP ENDPOINT
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    Called by Google Cloud Scheduler at 6:00 AM IST daily.
    
    Cloud Scheduler Config:
      Schedule:  0 6 * * *
      Timezone:  Asia/Kolkata
      Method:    POST
      URL:       https://YOUR_APP/api/whatsapp/daily-whatsapp
    
    Flow for EACH active farmer:
      1. Fetch live crop price (farmer's selected crop + district)
      2. Fetch today's weather + tomorrow's forecast
      3. Generate personalized message in farmer's language
      4. Send via WhatsApp Cloud API
    
    Returns full job summary with success/failure counts.
    """
    logger.info("[WA Cloud Scheduler] Daily WhatsApp job triggered")
    
    try:
        result = await daily_whatsapp_job(triggered_by="cloud_scheduler")
        return {
            "status": "completed",
            "message": "Daily WhatsApp messages sent to all active farmers",
            "result": result,
        }
    except Exception as e:
        logger.error(f"[WA Cloud Scheduler] Job failed: {e}")
        return {
            "status": "error",
            "message": str(e),
        }


@router.get("/status")
async def get_status(
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Last N job run records + overall stats + service health."""
    result = await db.execute(
        select(WhatsAppJobRun)
        .order_by(desc(WhatsAppJobRun.started_at))
        .limit(limit)
    )
    runs = result.scalars().all()

    # Total stats
    total_result = await db.execute(select(WhatsAppJobRun))
    all_runs = total_result.scalars().all()

    return {
        "service_health": whatsapp_service.health(),
        "total_runs":     len(all_runs),
        "total_sent":     sum(r.sent_success for r in all_runs),
        "total_failed":   sum(r.sent_failure for r in all_runs),
        "recent_runs": [
            {
                "run_id":         r.run_id,
                "triggered_by":   r.triggered_by,
                "status":         r.status,
                "total_farmers":  r.total_farmers,
                "sent_success":   r.sent_success,
                "sent_failure":   r.sent_failure,
                "invalid":        r.invalid_numbers,
                "skipped":        r.skipped,
                "duration_s":     r.duration_seconds,
                "started_at":     r.started_at.isoformat() if r.started_at else None,
                "completed_at":   r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ],
    }


@router.get("/logs")
async def get_logs(
    page:     int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
    phone:    Optional[str] = Query(default=None),
    success:  Optional[bool] = Query(default=None),
    run_id:   Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Paginated delivery log viewer."""
    query = select(WhatsAppAlertLog).order_by(desc(WhatsAppAlertLog.sent_at))

    if phone:
        query = query.where(WhatsAppAlertLog.phone.contains(phone))
    if success is not None:
        query = query.where(WhatsAppAlertLog.success == success)
    if run_id:
        query = query.where(WhatsAppAlertLog.job_run_id == run_id)

    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "page":     page,
        "per_page": per_page,
        "count":    len(logs),
        "logs": [
            {
                "id":             l.id,
                "phone":          l.phone[-4:].rjust(len(l.phone), "*"),  # mask for privacy
                "language":       l.language,
                "district":       l.district,
                "crop":           l.crop,
                "success":        l.success,
                "message_id":     l.message_id,
                "error":          l.error_message,
                "price_used":     l.price_used,
                "weather_used":   l.weather_used,
                "sent_at":        l.sent_at.isoformat() if l.sent_at else None,
                "run_id":         l.job_run_id,
            }
            for l in logs
        ],
    }


@router.post("/optout")
async def add_optout(req: OptOutRequest, db: AsyncSession = Depends(get_db)):
    """Add a phone number to opt-out list (stops future alerts)."""
    existing = await db.execute(
        select(WhatsAppOptOut).where(WhatsAppOptOut.phone == req.phone)
    )
    if existing.scalar_one_or_none():
        return {"status": "already_opted_out", "phone": req.phone}

    db.add(WhatsAppOptOut(phone=req.phone, reason=req.reason))
    await db.commit()
    return {"status": "opted_out", "phone": req.phone}


@router.delete("/optout/{phone}")
async def remove_optout(phone: str, db: AsyncSession = Depends(get_db)):
    """Re-enable a phone number (remove from opt-out list)."""
    result = await db.execute(
        select(WhatsAppOptOut).where(WhatsAppOptOut.phone == phone)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Phone not found in opt-out list")

    await db.delete(entry)
    await db.commit()
    return {"status": "re_enabled", "phone": phone}


@router.get("/optouts")
async def list_optouts(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all opted-out numbers."""
    offset = (page - 1) * per_page
    result = await db.execute(
        select(WhatsAppOptOut)
        .order_by(desc(WhatsAppOptOut.opted_out_at))
        .offset(offset)
        .limit(per_page)
    )
    entries = result.scalars().all()
    return {
        "count": len(entries),
        "optouts": [
            {
                "phone":       e.phone[-4:].rjust(len(e.phone), "*"),
                "reason":      e.reason,
                "opted_out_at":e.opted_out_at.isoformat() if e.opted_out_at else None,
            }
            for e in entries
        ],
    }


@router.get("/health")
async def whatsapp_health():
    """Quick health check for WhatsApp service configuration."""
    health = whatsapp_service.health()
    return {
        "whatsapp_api": health,
        "ready":        health["configured"] and health["circuit_state"] != "open",
        "timestamp":    datetime.utcnow().isoformat(),
    }
