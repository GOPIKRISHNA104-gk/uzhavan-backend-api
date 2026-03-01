"""
Uzhavan AI - FastAPI Backend
Main application entry point — v3.1.0

New in v3.1:
  - WhatsApp Cloud API alert system
  - Daily 6 AM cron job for farmer alerts
  - Multilingual message generator (6 languages)
  - Delivery logging + opt-out management
  - GAE cron endpoint (/api/whatsapp/cron/daily-alert)
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import logging

logger = logging.getLogger(__name__)

# Import routers
from routers import auth, chat, disease, market, weather, crop, call, firebase_auth, openmeteo_weather
from routers import prices  # New mandi prices router
from routers import farmer_auth  # Firestore-based farmer auth
from routers import weather_intelligence  # Weather intelligence for farmers
from routers import agriculture_news  # Agriculture news for farmers
from routers import dashboard  # Unified Dashboard
from routers import market_prices  # Unified Market Prices (All Fruits, Vegetables, Grains)
from routers import live_prices  # Live Market Prices & LSTM engine
from routers import whatsapp_alerts  # WhatsApp Daily Alert System
from routers import fcm_sms  # FCM Push Notification SMS
from routers import tts  # Text-to-Speech proxy
from routers import call_history  # Call History & Recordings
from database import init_db

# Import scheduler for background tasks
from scheduler import start_scheduler, stop_scheduler, get_scheduler_status

# Import cache service for performance optimization
try:
    from services.cache_service import cache_manager
    CACHE_ENABLED = True
except ImportError:
    cache_manager = None
    CACHE_ENABLED = False

# Lifespan context manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    print("[START] Uzhavan AI Backend v3.0.0 Starting...")
    await init_db()
    print("[OK] Database initialized")

    # Start SQLite/in-memory cache manager
    if CACHE_ENABLED and cache_manager:
        await cache_manager.start()
        print("[OK] Cache manager started (SQLite + in-memory)")

    # Connect to Redis (fails gracefully to in-memory fallback)
    try:
        from services.redis_cache import redis_cache
        connected = await redis_cache.connect()
        if connected:
            print("[OK] Redis cache connected")
        else:
            print("[WARN] Redis unavailable — using in-memory fallback cache")
    except Exception as e:
        print(f"[WARN] Redis startup error: {e} — using in-memory fallback cache")

    # Start background scheduler for daily price fetching
    await start_scheduler()
    print("[OK] Background scheduler started for daily mandi price updates")

    print("[READY] Uzhavan AI Backend ready!")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await stop_scheduler()
    print("[OK] Background scheduler stopped")

    # Disconnect Redis
    try:
        from services.redis_cache import redis_cache
        await redis_cache.disconnect()
        print("[OK] Redis disconnected")
    except Exception:
        pass

    # Stop cache manager
    if CACHE_ENABLED and cache_manager:
        await cache_manager.stop()
        print("[OK] Cache manager stopped")

    print("Uzhavan AI Backend shut down cleanly.")

# Create FastAPI app
app = FastAPI(
    title="Uzhavan AI API",
    description="Backend API for Uzhavan AI - Smart Farming Assistant",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://uzhavan-ai-eta.vercel.app",
    ],
    allow_origin_regex=r"https://uzhavan-.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enable Gzip Compression for Performance
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Include routers - Farmer Auth with Firestore is the primary auth method
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Unified Dashboard (Primary)"])
app.include_router(farmer_auth.router, prefix="/api/v2/auth", tags=["Farmer Auth (Firestore)"])
app.include_router(firebase_auth.router, prefix="/api/v1/auth", tags=["Firebase Auth (SQLite - Legacy)"])
app.include_router(auth.router, prefix="/api/auth", tags=["Legacy Authentication (JWT)"])
app.include_router(chat.router, prefix="/api/chat", tags=["AI Chat"])
app.include_router(disease.router, prefix="/api/disease", tags=["Disease Prediction"])
app.include_router(market.router, prefix="/api/market/legacy", tags=["Market Insights (Legacy)"])
app.include_router(market_prices.router, prefix="/api/market", tags=["Market Prices (Unified - Primary)"])
app.include_router(weather.router, prefix="/api/weather", tags=["Weather (OpenWeatherMap)"])
app.include_router(openmeteo_weather.router, prefix="/api/weather/v2", tags=["Weather (Open-Meteo - Free)"])
app.include_router(weather_intelligence.router, prefix="/api/weather/v3", tags=["Weather Intelligence (Farmers)"])
app.include_router(crop.router, prefix="/api/crop", tags=["Crop Recommendation"])

# New Mandi Prices Router - Daily prices from data.gov.in and predictions
app.include_router(prices.router, prefix="/api/prices", tags=["Mandi Prices & Predictions"])

# Agriculture News Router - Localized news for Indian farmers
app.include_router(agriculture_news.router, prefix="/api/news", tags=["Agriculture News"])

# Live Market Prices & Prediction Engine wrapper
app.include_router(live_prices.router, tags=["LSTM Prediction & Live Market"])

# WhatsApp Daily Alert System
app.include_router(whatsapp_alerts.router, prefix="/api/whatsapp", tags=["WhatsApp Alerts (Daily 6AM)"])

# Push Notification "SMS" Generator
app.include_router(fcm_sms.router, prefix="/api", tags=["FCM SMS-Only Backend"])

# Text-to-Speech proxy (Google Translate TTS)
app.include_router(tts.router, prefix="/api/tts", tags=["Text-to-Speech"])

# Call History & Recordings
app.include_router(call_history.router, prefix="/api/calls", tags=["Call History"])

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": "🌾 Welcome to Uzhavan AI API",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
        "features": {
            "mandi_prices": "/api/prices/today",
            "price_prediction": "/api/prices/predict",
            "historical_data": "/api/prices/historical"
        }
    }

# Health check endpoint
@app.get("/health")
async def health_check():
    from services.redis_cache import redis_cache
    from models.session import session_registry

    scheduler_status = get_scheduler_status()
    redis_status = await redis_cache.health_check()
    sessions = session_registry.stats()

    return {
        "status": "healthy",
        "service": "uzhavan-ai-backend",
        "version": "3.0.0",
        "scheduler": scheduler_status,
        "cache_enabled": CACHE_ENABLED,
        "redis": redis_status,
        "voice_sessions": sessions,
        "features": {
            "voice_ai": "WebSocket /api/call/ws/voice",
            "voice_health": "/api/call/health",
            "debug_transcript": "/api/call/debug/transcript",
        },
    }

# Performance monitoring endpoint
@app.get("/performance")
async def performance_status():
    """
    Get performance metrics for monitoring
    - Cache hit rates
    - Circuit breaker status
    - Service health
    """
    result = {
        "cache_enabled": CACHE_ENABLED,
        "cache_stats": None,
        "circuit_breakers": None,
        "scheduler": get_scheduler_status()
    }
    
    if CACHE_ENABLED and cache_manager:
        result["cache_stats"] = cache_manager.get_stats()
        
        # Try to get HTTP client circuit breaker status
        try:
            from services.http_client import http_client
            result["circuit_breakers"] = http_client.get_circuit_status()
        except ImportError:
            result["circuit_breakers"] = "not available"
    
    return result

# Reset circuit breakers (fix tripped weather API)
@app.post("/reset-circuits")
async def reset_circuits():
    """Reset all circuit breakers to closed state"""
    try:
        from services.http_client import http_client
        for name, cb in http_client._circuits.items():
            cb._state = "closed"
            cb._failures = 0
        return {"status": "ok", "message": "All circuit breakers reset"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Debug weather endpoint to diagnose API connectivity
@app.get("/debug/weather")
async def debug_weather():
    """Test ALL methods of reaching Open-Meteo from this server"""
    import traceback
    results = {}
    url = "https://api.open-meteo.com/v1/forecast?latitude=13.08&longitude=80.27&current=temperature_2m&timezone=auto"
    
    # Test 1: aiohttp
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                results["aiohttp"] = {"status": resp.status, "data": (await resp.text())[:200]}
    except Exception as e:
        results["aiohttp"] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-300:]}
    
    # Test 2: httpx
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            results["httpx"] = {"status": resp.status_code, "data": resp.text[:200]}
    except Exception as e:
        results["httpx"] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-300:]}
    
    # Test 3: requests (sync)
    try:
        import requests as req
        import asyncio
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: req.get(url, timeout=10))
        results["requests"] = {"status": resp.status_code, "data": resp.text[:200]}
    except Exception as e:
        results["requests"] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-300:]}
    
    return results


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
