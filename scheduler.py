"""
Background Scheduler for Daily Tasks
- Fetches mandi prices daily from data.gov.in
- Runs price prediction updates
"""

import asyncio
from datetime import datetime, time
from typing import Optional
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import async_session
from services.mandi_service import mandi_service
from services.weather_service import weather_service
from services.whatsapp_alert_job import daily_whatsapp_job as _whatsapp_job

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None


async def update_weather_cache_job():
    """
    Periodic job to refresh stale weather data in cache.
    Runs every hour.
    """
    logger.info("⛅ Starting weather cache update job...")
    try:
        from services.weather_service import weather_service
        # Prefetch all locations using the service (handles caching internally)
        await weather_service.prefetch_all_locations()
        logger.info("✅ Weather cache updated successfully")
    except Exception as e:
        logger.error(f"❌ Error updating weather cache: {e}")


async def fetch_daily_mandi_prices():
    """
    Daily job to fetch mandi prices from data.gov.in
    Runs every day at 6:00 AM IST (00:30 UTC)
    """
    logger.info("🌾 Starting daily mandi price fetch job...")
    
    try:
        async with async_session() as db:
            result = await mandi_service.fetch_and_store_daily_prices(
                db=db,
                max_records=10000  # Fetch up to 10,000 records per day
            )
            
            if result["success"]:
                logger.info(
                    f"✅ Daily mandi price fetch completed: "
                    f"Fetched={result['total_fetched']}, "
                    f"Inserted={result['total_inserted']}"
                )
            else:
                logger.warning(f"⚠️ Daily mandi price fetch had issues")
                
    except Exception as e:
        logger.error(f"❌ Error in daily mandi price fetch: {e}")


async def initial_price_fetch():
    """
    Initial fetch on startup if no recent data exists
    """
    logger.info("🌾 Checking if initial price fetch is needed...")
    
    try:
        async with async_session() as db:
            status = await mandi_service.get_fetch_status(db)
            
            if status["status"] == "never_fetched":
                logger.info("📦 No price data found. Starting initial fetch...")
                await fetch_daily_mandi_prices()
            else:
                last_fetch = datetime.fromisoformat(status["last_fetch"])
                hours_since_fetch = (datetime.utcnow() - last_fetch).total_seconds() / 3600
                
                if hours_since_fetch > 24:
                    logger.info(f"📦 Last fetch was {hours_since_fetch:.1f} hours ago. Refreshing...")
                    await fetch_daily_mandi_prices()
                else:
                    logger.info(f"✅ Price data is recent ({hours_since_fetch:.1f} hours old)")
                    
    except Exception as e:
        logger.error(f"❌ Error checking initial fetch status: {e}")


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler"""
    global scheduler
    
    scheduler = AsyncIOScheduler(
        timezone="Asia/Kolkata"  # IST timezone
    )
    
    # Daily mandi price fetch at 6:00 AM IST
    scheduler.add_job(
        fetch_daily_mandi_prices,
        trigger=CronTrigger(
            hour=6,
            minute=0,
            timezone="Asia/Kolkata"
        ),
        id="daily_mandi_fetch",
        name="Daily Mandi Price Fetch",
        replace_existing=True
    )
    
    # Also fetch at 6:00 PM IST for evening updates
    scheduler.add_job(
        fetch_daily_mandi_prices,
        trigger=CronTrigger(
            hour=18,
            minute=0,
            timezone="Asia/Kolkata"
        ),
        id="evening_mandi_fetch",
        name="Evening Mandi Price Fetch",
        replace_existing=True
    )

    # Periodic Weather Cache Update (Every 6 hours - conserve API quota)
    scheduler.add_job(
        update_weather_cache_job,
        trigger=IntervalTrigger(hours=6),
        id="weather_cache_update",
        name="Update Stale Weather Cache",
        replace_existing=True
    )

    # Daily WhatsApp farmer alert at 6:05 AM IST
    # (5 minutes after mandi fetch at 6:00 AM to ensure prices are loaded)
    async def _run_whatsapp_job():
        try:
            logger.info("[Scheduler] Starting daily WhatsApp alert job...")
            result = await _whatsapp_job(triggered_by="cron")
            logger.info(f"[Scheduler] WhatsApp job done: {result}")
        except Exception as e:
            logger.error(f"[Scheduler] WhatsApp job error: {e}")

    scheduler.add_job(
        _run_whatsapp_job,
        trigger=CronTrigger(
            hour=6,
            minute=5,
            timezone="Asia/Kolkata"
        ),
        id="daily_whatsapp_alerts",
        name="Daily WhatsApp Farmer Alerts (6:05 AM IST)",
        replace_existing=True
    )

    # Daily FCM "SMS" alert at 6:00 AM IST
    async def _run_fcm_sms_job():
        try:
            logger.info("[Scheduler] Starting daily FCM SMS job...")
            from routers.fcm_sms import _send_fcm_sms
            sent, total, data = await _send_fcm_sms()
            logger.info(f"[Scheduler] FCM SMS job done: Sent {sent} to {total} farmers")
        except Exception as e:
            logger.error(f"[Scheduler] FCM SMS job error: {e}")

    scheduler.add_job(
        _run_fcm_sms_job,
        trigger=CronTrigger(
            hour=6,
            minute=0,
            timezone="Asia/Kolkata"
        ),
        id="daily_fcm_sms_alerts",
        name="Daily FCM SMS Alerts (6:00 AM IST)",
        replace_existing=True
    )
    
    logger.info("[Scheduler] Registered: daily_whatsapp_alerts at 6:05 AM IST")
    logger.info("[Scheduler] Registered: daily_fcm_sms_alerts at 6:00 AM IST")
    
    # Backend-only 7904223010 trigger at 6:00 AM IST
    async def _run_backend_auto_whatsapp():
        try:
            logger.info("[Scheduler] Starting direct backend auto message...")
            from routers.whatsapp_alerts import build_tamil_auto_message
            from services.whatsapp_service import whatsapp_service
            
            msg = await build_tamil_auto_message()
            res = await whatsapp_service.send_text(phone="917904223010", message=msg)
            
            if res.success:
                logger.info(f"[Scheduler] Direct backend msg sent successfully: {res.message_id}")
            else:
                logger.error(f"[Scheduler] Direct backend msg failed: {res.error}")
        except Exception as e:
            logger.error(f"[Scheduler] Direct backend msg error: {e}")

    scheduler.add_job(
        _run_backend_auto_whatsapp,
        trigger=CronTrigger(
            hour=6,
            minute=0,
            timezone="Asia/Kolkata"
        ),
        id="backend_auto_whatsapp",
        name="Direct Backend Auto Message (6:00 AM IST)",
        replace_existing=True
    )
    
    return scheduler


async def start_scheduler():
    """Start the background scheduler"""
    global scheduler
    
    if scheduler is None:
        scheduler = create_scheduler()
    
    if not scheduler.running:
        scheduler.start()
        logger.info("🚀 Background scheduler started")
        
        # Run initial fetch in background
        asyncio.create_task(initial_price_fetch())


async def stop_scheduler():
    """Stop the background scheduler"""
    global scheduler
    
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 Background scheduler stopped")


def get_scheduler_status():
    """Get current scheduler status"""
    global scheduler
    
    if scheduler is None:
        return {"status": "not_initialized"}
    
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None
        })
    
    return {
        "status": "running" if scheduler.running else "stopped",
        "jobs": jobs
    }


async def trigger_manual_fetch():
    """Manually trigger a price fetch (for testing or admin use)"""
    logger.info("🔄 Manual mandi price fetch triggered")
    await fetch_daily_mandi_prices()
    return {"status": "completed", "message": "Manual fetch completed"}
