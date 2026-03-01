"""
Market Insights Router - Crop prices and market data
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import google.generativeai as genai
from typing import List, Optional

from database import get_db, User
from schemas import MarketRequest, MarketResponse, MarketPriceItem
from config import settings
from auth_deps import get_current_user  # Unified Firebase + JWT auth

router = APIRouter()

# Configure Gemini for market analysis
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

# Sample market data (fallback when API is not available)
SAMPLE_MARKET_DATA = {
    "rice": [
        {"market": "Chennai", "min_price": 2200, "max_price": 2800, "modal_price": 2500, "arrival_date": "2026-02-08"},
        {"market": "Madurai", "min_price": 2100, "max_price": 2700, "modal_price": 2400, "arrival_date": "2026-02-08"},
        {"market": "Coimbatore", "min_price": 2300, "max_price": 2900, "modal_price": 2600, "arrival_date": "2026-02-08"},
    ],
    "wheat": [
        {"market": "Delhi", "min_price": 2400, "max_price": 3000, "modal_price": 2700, "arrival_date": "2026-02-08"},
        {"market": "Punjab", "min_price": 2300, "max_price": 2900, "modal_price": 2600, "arrival_date": "2026-02-08"},
    ],
    "tomato": [
        {"market": "Chennai", "min_price": 1500, "max_price": 2500, "modal_price": 2000, "arrival_date": "2026-02-08"},
        {"market": "Bangalore", "min_price": 1400, "max_price": 2400, "modal_price": 1900, "arrival_date": "2026-02-08"},
    ],
    "onion": [
        {"market": "Nashik", "min_price": 1200, "max_price": 2000, "modal_price": 1600, "arrival_date": "2026-02-08"},
        {"market": "Chennai", "min_price": 1300, "max_price": 2100, "modal_price": 1700, "arrival_date": "2026-02-08"},
    ],
    "potato": [
        {"market": "Agra", "min_price": 800, "max_price": 1400, "modal_price": 1100, "arrival_date": "2026-02-08"},
        {"market": "Kolkata", "min_price": 900, "max_price": 1500, "modal_price": 1200, "arrival_date": "2026-02-08"},
    ],
    "cotton": [
        {"market": "Gujarat", "min_price": 6500, "max_price": 7500, "modal_price": 7000, "arrival_date": "2026-02-08"},
        {"market": "Maharashtra", "min_price": 6400, "max_price": 7400, "modal_price": 6900, "arrival_date": "2026-02-08"},
    ],
    "sugarcane": [
        {"market": "Uttar Pradesh", "min_price": 350, "max_price": 450, "modal_price": 400, "arrival_date": "2026-02-08"},
        {"market": "Maharashtra", "min_price": 340, "max_price": 440, "modal_price": 390, "arrival_date": "2026-02-08"},
    ],
}

def get_language_instruction(language: str) -> str:
    """Get language instruction for AI response"""
    language_map = {
        "tamil": "Respond in Tamil (தமிழ்).",
        "hindi": "Respond in Hindi (हिंदी).",
        "telugu": "Respond in Telugu (తెలుగు).",
        "english": "Respond in English."
    }
    return language_map.get(language.lower(), "Respond in English.")

async def get_ai_market_recommendation(crop_name: str, prices: List[dict], language: str) -> tuple:
    """Get AI-powered market recommendation"""
    if not settings.GEMINI_API_KEY:
        return "stable", "Check local market for best prices."
    
    try:
        language_instruction = get_language_instruction(language)
        prompt = f"""Analyze these market prices for {crop_name} and provide:
1. A one-word trend: "rising", "falling", or "stable"
2. A short recommendation for farmers (2-3 sentences max)

Market data: {prices}

{language_instruction}

Respond in this exact format:
TREND: [trend]
RECOMMENDATION: [your recommendation]"""
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        text = response.text
        trend = "stable"
        recommendation = "Monitor prices and sell when favorable."
        
        if "TREND:" in text:
            trend_line = text.split("TREND:")[1].split("\n")[0].strip().lower()
            if "rising" in trend_line:
                trend = "rising"
            elif "falling" in trend_line:
                trend = "falling"
        
        if "RECOMMENDATION:" in text:
            recommendation = text.split("RECOMMENDATION:")[1].strip()
        
        return trend, recommendation
        
    except Exception:
        return "stable", "Check local market for best prices."

@router.post("/prices", response_model=MarketResponse)
async def get_market_prices(
    request: MarketRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get market prices for a crop"""
    
    crop_name = request.crop_name.lower().strip()
    
    # Try to get data from sample data first
    prices_data = SAMPLE_MARKET_DATA.get(crop_name, [])
    
    if not prices_data:
        # Generate fallback data using AI
        prices_data = [
            {
                "market": request.state or "Local Market",
                "min_price": 1000,
                "max_price": 1500,
                "modal_price": 1250,
                "arrival_date": "2026-02-08"
            }
        ]
    
    # Filter by state/district if provided
    if request.state:
        filtered = [p for p in prices_data if request.state.lower() in p.get("market", "").lower()]
        if filtered:
            prices_data = filtered
    
    # Get AI recommendation
    trend, recommendation = await get_ai_market_recommendation(
        crop_name, prices_data, request.language
    )
    
    # Convert to response format
    prices = [
        MarketPriceItem(
            market=p["market"],
            min_price=p["min_price"],
            max_price=p["max_price"],
            modal_price=p["modal_price"],
            arrival_date=p["arrival_date"]
        )
        for p in prices_data
    ]
    
    return MarketResponse(
        crop_name=request.crop_name,
        prices=prices,
        trend=trend,
        recommendation=recommendation
    )

@router.get("/crops")
async def get_available_crops():
    """Get list of crops with available market data"""
    return {
        "crops": list(SAMPLE_MARKET_DATA.keys()),
        "message": "Select a crop to get market prices"
    }

@router.get("/trending")
async def get_trending_crops(
    current_user: User = Depends(get_current_user)
):
    """Get trending crops with best prices"""
    trending = []
    
    for crop, prices in SAMPLE_MARKET_DATA.items():
        if prices:
            avg_price = sum(p["modal_price"] for p in prices) / len(prices)
            trending.append({
                "crop": crop,
                "average_price": avg_price,
                "markets": len(prices)
            })
    
    # Sort by average price descending
    trending.sort(key=lambda x: x["average_price"], reverse=True)
    
    return {"trending_crops": trending[:5]}
