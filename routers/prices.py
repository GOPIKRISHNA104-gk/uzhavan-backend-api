"""
Mandi Prices Router - API endpoints for daily mandi prices and predictions
SAME data served to both farmer and buyer dashboards
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field

from database import get_db, User, MandiPrice
from auth_deps import get_current_user
from services.mandi_service import mandi_service
from services.price_predictor import price_predictor
from config import settings
import google.generativeai as genai

router = APIRouter()

# Configure Gemini for Tamil explanations
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)


# ============== Pydantic Schemas ==============

class PriceItem(BaseModel):
    """Individual price record"""
    id: int
    arrival_date: str
    state: str
    district: str
    market: str
    commodity: str
    variety: Optional[str]
    min_price: float
    max_price: float
    modal_price: float
    
    class Config:
        from_attributes = True


class TodayPricesResponse(BaseModel):
    """Response for today's prices"""
    success: bool
    date: str
    total_records: int
    prices: List[PriceItem]
    location_filter: dict


class PredictionRequest(BaseModel):
    """Request for price prediction"""
    commodity: str = Field(..., min_length=1, description="Commodity name (e.g., Tomato, Onion)")
    state: Optional[str] = None
    district: Optional[str] = None
    market: Optional[str] = None
    days_ahead: int = Field(default=1, ge=1, le=7, description="Days ahead to predict (1-7)")
    language: str = Field(default="english", description="Language for explanations")


class PredictionResponse(BaseModel):
    """Response for price prediction"""
    success: bool
    commodity: str
    location: dict
    prediction: Optional[dict]
    analysis: Optional[dict]
    confidence: Optional[dict]
    message: Optional[str]
    reason: Optional[str]


class TrendExplanationRequest(BaseModel):
    """Request for price trend explanation"""
    commodity: str
    state: Optional[str] = None
    language: str = Field(default="english")


# ============== API Endpoints ==============

@router.get("/today", response_model=TodayPricesResponse)
async def get_today_prices(
    commodity: Optional[str] = Query(None, description="Filter by commodity name"),
    state: Optional[str] = Query(None, description="Filter by state"),
    district: Optional[str] = Query(None, description="Filter by district"),
    market: Optional[str] = Query(None, description="Filter by market"),
    limit: int = Query(100, ge=1, le=500, description="Maximum records to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get today's mandi prices.
    Public endpoint - no authentication required.
    """
    try:
        prices = await mandi_service.get_today_prices(
            db=db,
            commodity=commodity,
            state=state,
            district=district,
            market=market,
            limit=limit
        )
        
        price_items = [
            PriceItem(
                id=p.id,
                arrival_date=p.arrival_date.strftime("%Y-%m-%d") if p.arrival_date else "",
                state=p.state,
                district=p.district,
                market=p.market,
                commodity=p.commodity,
                variety=p.variety,
                min_price=p.min_price,
                max_price=p.max_price,
                modal_price=p.modal_price
            )
            for p in prices
        ]
        
        return TodayPricesResponse(
            success=True,
            date=date.today().isoformat(),
            total_records=len(price_items),
            prices=price_items,
            location_filter={
                "commodity": commodity,
                "state": state,
                "district": district,
                "market": market
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching prices: {str(e)}")


@router.post("/predict", response_model=PredictionResponse)
async def predict_price(
    request: PredictionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Get price prediction for a commodity.
    Public endpoint - no authentication required.
    Uses historical mandi price data for prediction.
    Returns "Prediction not available" if insufficient data.
    AI does NOT generate prices - only uses actual database values.
    """
    try:
        result = await price_predictor.predict_price(
            db=db,
            commodity=request.commodity,
            state=request.state,
            district=request.district,
            market=request.market,
            days_ahead=request.days_ahead
        )
        
        if result["success"]:
            return PredictionResponse(
                success=True,
                commodity=result["commodity"],
                location=result["location"],
                prediction=result["prediction"],
                analysis=result["analysis"],
                confidence=result["confidence"],
                message=None,
                reason=None
            )
        else:
            return PredictionResponse(
                success=False,
                commodity=result["commodity"],
                location=result.get("location", {}),
                prediction=None,
                analysis=None,
                confidence=None,
                message=result.get("message", "Prediction not available"),
                reason=result.get("reason")
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error predicting price: {str(e)}")


@router.post("/predict/multi-day")
async def predict_multi_day_prices(
    request: PredictionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Predict prices for multiple days ahead (up to 7 days)
    """
    try:
        days = min(request.days_ahead, 7)
        
        result = await price_predictor.predict_multi_day(
            db=db,
            commodity=request.commodity,
            state=request.state,
            district=request.district,
            market=request.market,
            days=days
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error predicting prices: {str(e)}")


@router.post("/trend/explain")
async def explain_price_trend(
    request: TrendExplanationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a simple explanation of price trends
    
    Optionally uses Gemini AI to provide explanations in Tamil.
    AI ONLY explains trends - does NOT generate prices.
    All prices come from the database.
    """
    try:
        # Get basic trend explanation from predictor
        explanation = await price_predictor.get_price_trend_explanation(
            db=db,
            commodity=request.commodity,
            state=request.state,
            language=request.language
        )
        
        # If Tamil is requested and Gemini is available, enhance the explanation
        if request.language == "tamil" and settings.GEMINI_API_KEY:
            try:
                # Get additional context from historical data
                historical = await price_predictor.get_prediction_data(
                    db=db,
                    commodity=request.commodity,
                    state=request.state,
                    days=14
                )
                
                if len(historical) >= 3:
                    prices = [d["avg_price"] for d in historical]
                    current = prices[-1]
                    avg_price = sum(prices) / len(prices)
                    
                    prompt = f"""You are an agricultural advisor. Explain the following price trend in simple Tamil for Indian farmers:

Commodity: {request.commodity}
Current Price: ₹{current:.0f}/Quintal
Average Price (last 2 weeks): ₹{avg_price:.0f}/Quintal
Price Data Points: {len(historical)}
Basic Analysis: {explanation}

Provide advice in 2-3 sentences in Tamil. Focus on:
1. Whether to sell now or wait
2. Simple market outlook

IMPORTANT: Only use the prices provided above. Do not make up numbers."""

                    model = genai.GenerativeModel('gemini-1.5-flash')
                    response = model.generate_content(prompt)
                    
                    if response.text:
                        explanation = response.text.strip()
                        
            except Exception as ai_error:
                # If AI fails, use the basic explanation
                pass
        
        return {
            "success": True,
            "commodity": request.commodity,
            "state": request.state,
            "language": request.language,
            "explanation": explanation
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error explaining trend: {str(e)}")


@router.get("/commodities")
async def get_available_commodities(
    state: Optional[str] = Query(None, description="Filter by state"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of commodities with price data"""
    try:
        commodities = await mandi_service.get_available_commodities(db, state)
        return {
            "success": True,
            "total": len(commodities),
            "commodities": commodities
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching commodities: {str(e)}")


@router.get("/states")
async def get_available_states(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of states with price data"""
    try:
        states = await mandi_service.get_available_states(db)
        return {
            "success": True,
            "total": len(states),
            "states": states
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching states: {str(e)}")


@router.get("/markets")
async def get_available_markets(
    state: Optional[str] = Query(None, description="Filter by state"),
    district: Optional[str] = Query(None, description="Filter by district"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get list of markets with price data"""
    try:
        markets = await mandi_service.get_available_markets(db, state, district)
        return {
            "success": True,
            "total": len(markets),
            "markets": markets
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching markets: {str(e)}")


@router.get("/historical")
async def get_historical_prices(
    commodity: str = Query(..., description="Commodity name"),
    state: Optional[str] = Query(None, description="Filter by state"),
    district: Optional[str] = Query(None, description="Filter by district"),
    market: Optional[str] = Query(None, description="Filter by market"),
    days: int = Query(30, ge=1, le=90, description="Number of days of history"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get historical prices for a commodity"""
    try:
        prices = await mandi_service.get_historical_prices(
            db=db,
            commodity=commodity,
            state=state,
            district=district,
            market=market,
            days=days
        )
        
        price_items = [
            {
                "date": p.arrival_date.strftime("%Y-%m-%d") if p.arrival_date else "",
                "state": p.state,
                "district": p.district,
                "market": p.market,
                "min_price": p.min_price,
                "max_price": p.max_price,
                "modal_price": p.modal_price
            }
            for p in prices
        ]
        
        return {
            "success": True,
            "commodity": commodity,
            "days_requested": days,
            "total_records": len(price_items),
            "prices": price_items
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching historical prices: {str(e)}")


@router.get("/status")
async def get_price_fetch_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get status of price data fetching"""
    try:
        status = await mandi_service.get_fetch_status(db)
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching status: {str(e)}")


@router.post("/refresh")
async def trigger_price_refresh(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Manually trigger a price data refresh
    Only available to authenticated users
    """
    from scheduler import trigger_manual_fetch
    
    background_tasks.add_task(trigger_manual_fetch)
    
    return {
        "success": True,
        "message": "Price refresh triggered. Data will be updated in the background."
    }
