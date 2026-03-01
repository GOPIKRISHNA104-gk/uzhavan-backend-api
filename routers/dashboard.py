
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
import asyncio
import logging
from pydantic import BaseModel

from database import get_db
from services.weather_service import weather_service
from services.mandi_service import mandi_service
from services.localization import get_safe_error_message
from services.cache_service import cache_manager, CacheConfig
from routers.agriculture_news import get_news_cards, NewsCardsResponse

# Logger
logger = logging.getLogger(__name__)

router = APIRouter()

# --- Response Models for Payload Optimization ---

class DashboardResponse(BaseModel):
    status: str  # "success" or "partial"
    message: str
    weather: Optional[Dict[str, Any]] = None
    mandi_prices: List[Dict[str, Any]] = []
    news: Optional[NewsCardsResponse] = None
    meta: Dict[str, Any]

@router.get("/", response_model=DashboardResponse)
async def get_dashboard_data(
    request: Request,
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    state: Optional[str] = Query(None, description="State for mandi prices"),
    district: Optional[str] = Query(None, description="District for mandi prices"),
    lang: str = Query("en", description="Language code (en, ta, hi, etc.)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Unified API Endpoint for Farmer Dashboard.
    Fetches Weather + Mandi Prices + News in PARALLEL.
    Never fails - returns fallback data or safe error messages.
    """
    
    # localized safe message
    safe_message = get_safe_error_message(lang)
    
    # Default state if not provided
    news_state = state if state else "all_india"
    
    try:
        # Parallel Execution
        # We use return_exceptions=True to ensure one failure doesn't crash the other
        results = await asyncio.gather(
            _fetch_weather_safe(lat, lon, lang),
            _fetch_mandi_safe(db, state, district),
            _fetch_news_safe(lang, news_state),
            return_exceptions=True
        )
        
        weather_data = results[0]
        mandi_data = results[1]
        news_data = results[2]
        
        # Check for exceptions in results
        if isinstance(weather_data, Exception):
            logger.error(f"Dashboard weather error: {weather_data}")
            weather_data = None
            
        if isinstance(mandi_data, Exception):
            logger.error(f"Dashboard mandi error: {mandi_data}")
            mandi_data = []
            
        if isinstance(news_data, Exception):
            logger.error(f"Dashboard news error: {news_data}")
            news_data = None

        # Construct Status
        is_partial = False
        status_msg = "Data loaded successfully"
        
        if not weather_data:
            is_partial = True
            status_msg = safe_message # "Data temporarily unavailable..."
        
        # Determine if mandi data is genuinely missing or just unavailable
        # Mandi service returns [] if no data.
        
        return {
            "status": "partial" if is_partial else "success",
            "message": status_msg,
            "weather": weather_data,
            "mandi_prices": mandi_data,
            "news": news_data,
            "meta": {
                "lang": lang,
                "timestamp": str(asyncio.get_running_loop().time()) 
            }
        }

    except Exception as e:
        logger.error(f"Critical Dashboard Error: {e}")
        # GLOBAL FALLBACK - NEVER CRASH
        return {
            "status": "error",
            "message": safe_message,
            "weather": None,
            "mandi_prices": [],
            "news": None,
            "meta": {"error": str(e)}
        }

async def _fetch_weather_safe(lat: float, lon: float, lang: str = 'en') -> Optional[Dict]:
    """Helper to fetch weather safely"""
    try:
        # Use the NEW coordinate-based method
        return await weather_service.get_forecast_by_coords(lat, lon, lang)
    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return None

async def _fetch_mandi_safe(db, state, district) -> List[Dict]:
    """Helper to fetch mandi prices safely"""
    try:
        # If no state provided, maybe try to guess from lat/lon? 
        # For now, if no state, return empty or top commoodities.
        # User app should send state.
        
        limit = 10 # Optimize payload size
        
        prices = await mandi_service.get_today_prices(
            db=db,
            state=state,
            district=district,
            limit=limit
        )
        return prices
    except Exception as e:
        logger.error(f"Mandi fetch failed: {e}")
        return []

async def _fetch_news_safe(lang: str, state: str) -> Optional[NewsCardsResponse]:
    """Helper to fetch news safely"""
    try:
        return await get_news_cards(language=lang, state=state)
    except Exception as e:
        logger.error(f"News fetch failed: {e}")
        return None
