"""
Daily WhatsApp Alert Job — Uzhavan AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs every day at 6:00 AM IST.
All heavy imports are lazy (inside functions) to prevent startup crashes.

Flow per farmer:
  1. Load all active opted-in farmers from DB
  2. Fetch market price (district + crop) → Mandi DB → Redis cache
  3. Fetch weather (district) → Open-Meteo → Redis cache
  4. Generate language-specific WhatsApp message
  5. Send via Meta WhatsApp Cloud API (parallel, rate-limited)
  6. Log result (success/failure/invalid) to DB
  7. Auto-remove invalid numbers
"""

import asyncio
import logging
import uuid
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Set

logger = logging.getLogger(__name__)

BATCH_SIZE  = 100    # farmers processed per batch
CONCURRENCY = 20     # parallel WhatsApp sends within a batch


# ─── Language Normalizer ──────────────────────────────────────────────────────

_LANG_MAP = {
    "tamil":     "ta", "english":   "en", "hindi":     "hi",
    "telugu":    "te", "kannada":   "kn", "malayalam": "ml",
    "ta": "ta", "en": "en", "hi": "hi",
    "te": "te", "kn": "kn", "ml": "ml",
}

def _lang_code(language: str) -> str:
    return _LANG_MAP.get((language or "").lower().strip(), "en")


# ─── Data Fetchers ────────────────────────────────────────────────────────────

async def _get_market_price(db, district: str, crop: str) -> Dict[str, Any]:
    """Fetch today's market price — Redis cached per district+crop."""
    from services.redis_cache import redis_cache, RedisTTL
    from services.mandi_service import mandi_service

    cache_key = f"wa:price:{district.lower()}:{crop.lower()}"
    cached = await redis_cache.get(cache_key)
    if cached:
        return cached

    try:
        prices = await asyncio.wait_for(
            mandi_service.get_today_prices(db=db, district=district, commodity=crop, limit=3),
            timeout=5.0,
        )
        if not prices:
            prices = await asyncio.wait_for(
                mandi_service.get_today_prices(db=db, commodity=crop, limit=3),
                timeout=5.0,
            )

        if prices:
            p = prices[0]
            result = {
                "modal_price": float(p.modal_price or 0),
                "min_price":   float(p.min_price or 0),
                "max_price":   float(p.max_price or 0),
                "market":      p.market,
                "found":       True,
            }
        else:
            result = {"found": False, "modal_price": None, "min_price": None, "max_price": None}

        await redis_cache.set(cache_key, result, ttl=RedisTTL.MARKET_PRICES)
        return result

    except Exception as e:
        logger.warning(f"[WA Job] Price fetch failed for {district}/{crop}: {e}")
        return {"found": False, "modal_price": None, "min_price": None, "max_price": None}


async def _get_weather(district: str) -> Dict[str, Any]:
    """Fetch weather summary — Redis cached per district."""
    from services.redis_cache import redis_cache, RedisTTL
    from services.weather_service import weather_service

    cache_key = f"wa:weather:{district.lower()}"
    cached = await redis_cache.get(cache_key)
    if cached:
        return cached

    try:
        forecast = await asyncio.wait_for(weather_service.get_forecast(district), timeout=6.0)
        raw = forecast.get("raw_data", {})
        cw  = raw.get("current_weather", {})
        daily = raw.get("daily", {})

        temp = float(cw.get("temperature", 30))
        code = int(cw.get("weathercode", 0))
        desc = weather_service._get_weather_description(code)

        rain_sums  = (daily.get("precipitation_sum") or [])[1:4]
        total_rain = sum(r for r in rain_sums if r)

        if total_rain == 0:
            rain_alert = "No rain expected"
        elif total_rain < 5:
            rain_alert = f"Light rain possible ({total_rain:.0f}mm)"
        elif total_rain < 20:
            rain_alert = f"Moderate rain expected ({total_rain:.0f}mm)"
        else:
            rain_alert = f"Heavy rain alert ({total_rain:.0f}mm)"

        advisory = weather_service._generate_farming_advisory(
            {"temperature": temp, "humidity": 70, "windspeed": 10,
             "cloudcover": 40, "precipitation": 0},
            {"has_alert": total_rain > 5,
             "alert_level": "moderate" if total_rain < 20 else "heavy",
             "alert_message": rain_alert, "rain_days": []},
        )

        result = {"desc": desc, "temp": temp, "rain": rain_alert, "advisory": advisory, "found": True}
        await redis_cache.set(cache_key, result, ttl=RedisTTL.WEATHER)
        return result

    except Exception as e:
        logger.warning(f"[WA Job] Weather fetch failed for {district}: {e}")
        return {
            "desc": "Weather data unavailable", "temp": 30.0,
            "rain": "No data", "advisory": "Continue your regular farming activities.", "found": False,
        }


async def _load_opted_out(db) -> Set[str]:
    from sqlalchemy import select
    from models.whatsapp_models import WhatsAppOptOut
    result = await db.execute(select(WhatsAppOptOut.phone))
    return {row[0] for row in result.fetchall()}


async def _mark_invalid(db, phone: str):
    from sqlalchemy import select
    from models.whatsapp_models import WhatsAppOptOut
    existing = await db.execute(select(WhatsAppOptOut).where(WhatsAppOptOut.phone == phone))
    if not existing.scalar_one_or_none():
        db.add(WhatsAppOptOut(phone=phone, reason="invalid_number"))
        await db.commit()
        logger.info(f"[WA Job] Auto-removed invalid number: {phone}")


async def _log_result(db, delivery, user, message_preview, price, weather_desc, run_id):
    from models.whatsapp_models import WhatsAppAlertLog
    db.add(WhatsAppAlertLog(
        user_id         = user.id,
        phone           = user.phone,
        language        = _lang_code(user.language or "en"),
        district        = user.district or "",
        crop            = user.crop_type or "",
        message_preview = message_preview[:500],
        message_id      = delivery.message_id,
        success         = delivery.success,
        error_message   = delivery.error,
        attempt_count   = delivery.attempt,
        price_used      = price,
        weather_used    = (weather_desc or "")[:200],
        job_run_id      = run_id,
        sent_at         = delivery.sent_at,
    ))


# ─── Main Daily Job ───────────────────────────────────────────────────────────

async def daily_whatsapp_job(triggered_by: str = "cron") -> Dict[str, Any]:
    """
    Full daily WhatsApp alert job.
    Safe to call from scheduler, REST endpoint, or GAE cron.
    """
    from sqlalchemy import select
    from database import async_session, User
    from models.whatsapp_models import WhatsAppJobRun
    from services.whatsapp_service import whatsapp_service
    from services.whatsapp_message_generator import generate_message, generate_no_price_message

    run_id  = str(uuid.uuid4())[:12]
    started = time.monotonic()
    today   = datetime.now(timezone.utc)

    logger.info(f"[WA Job] Starting | run_id={run_id} | {today.strftime('%Y-%m-%d %H:%M UTC')}")

    stats: Dict[str, Any] = {
        "run_id": run_id, "total_farmers": 0,
        "sent_success": 0, "sent_failure": 0,
        "invalid_numbers": 0, "skipped": 0,
    }

    async with async_session() as db:
        # ── Create job-run row ────────────────────────────────────────────────
        job_run = WhatsAppJobRun(
            run_id=run_id, triggered_by=triggered_by,
            status="running", started_at=datetime.utcnow(),
        )
        db.add(job_run)
        await db.commit()

        try:
            # ── Load farmers ──────────────────────────────────────────────────
            res = await db.execute(
                select(User).where(User.is_active == True, User.phone != None)  # noqa
            )
            farmers: List[Any] = list(res.scalars().all())
            stats["total_farmers"] = len(farmers)
            logger.info(f"[WA Job] {len(farmers)} active farmers found")

            opted_out = await _load_opted_out(db)

            # ── Process in batches ────────────────────────────────────────────
            for batch_start in range(0, len(farmers), BATCH_SIZE):
                batch = farmers[batch_start: batch_start + BATCH_SIZE]
                send_list, meta_list = [], []

                for user in batch:
                    phone = user.phone or ""
                    if not phone or phone in opted_out:
                        stats["skipped"] += 1
                        continue

                    district = user.district or "Tamil Nadu"
                    crop     = user.crop_type or "General"
                    lang     = _lang_code(user.language or "en")

                    price_data = await _get_market_price(db, district, crop)
                    weather    = await _get_weather(district)

                    if price_data["found"]:
                        msg = generate_message(
                            language=lang, crop=crop, district=district,
                            modal_price=price_data["modal_price"],
                            min_price=price_data["min_price"],
                            max_price=price_data["max_price"],
                            weather_desc=weather["desc"], temperature=weather["temp"],
                            rain_alert=weather["rain"], farming_advisory=weather["advisory"],
                        )
                    else:
                        msg = generate_no_price_message(
                            language=lang, crop=crop, district=district,
                            weather_desc=weather["desc"], temperature=weather["temp"],
                            rain_alert=weather["rain"], farming_advisory=weather["advisory"],
                        )

                    send_list.append((phone, msg))
                    meta_list.append((user, msg[:500], price_data.get("modal_price"), weather["desc"]))

                # Send batch in parallel
                deliveries = await whatsapp_service.send_batch(send_list, concurrency=CONCURRENCY)

                for i, delivery in enumerate(deliveries):
                    user, preview, price, wd = meta_list[i]
                    await _log_result(db, delivery, user, preview, price, wd, run_id)

                    if delivery.success:
                        stats["sent_success"] += 1
                    else:
                        stats["sent_failure"] += 1
                        if "INVALID_NUMBER" in (delivery.error or ""):
                            stats["invalid_numbers"] += 1
                            await _mark_invalid(db, delivery.phone)

                await db.commit()
                logger.info(
                    f"[WA Job] Batch {batch_start//BATCH_SIZE+1} done | "
                    f"success={stats['sent_success']} failure={stats['sent_failure']}"
                )
                await asyncio.sleep(0.5)

            # ── Finalize job-run ──────────────────────────────────────────────
            duration = round(time.monotonic() - started, 2)
            job_run.total_farmers    = stats["total_farmers"]
            job_run.sent_success     = stats["sent_success"]
            job_run.sent_failure     = stats["sent_failure"]
            job_run.invalid_numbers  = stats["invalid_numbers"]
            job_run.skipped          = stats["skipped"]
            job_run.duration_seconds = duration
            job_run.status           = "completed"
            job_run.completed_at     = datetime.utcnow()
            await db.commit()

            stats.update({"duration_seconds": duration, "status": "completed"})
            logger.info(f"[WA Job] Done in {duration:.1f}s | {stats}")
            return stats

        except Exception as e:
            logger.error(f"[WA Job] Job failed: {e}", exc_info=True)
            job_run.status = "failed"
            job_run.error_message = str(e)
            job_run.completed_at  = datetime.utcnow()
            await db.commit()
            stats.update({"status": "failed", "error": str(e)})
            return stats
