"""
Price Prediction Service - Uses historical mandi price data to predict future prices
Uses time-series analysis (moving average and trend detection)
Does NOT guess - only provides predictions when sufficient data exists
"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
import logging
import statistics

from database import MandiPrice, PricePrediction

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PricePredictionService:
    """
    Service for predicting mandi prices based on historical data
    Uses simple, reliable statistical methods - NOT guessing
    """
    
    # Minimum data points required for prediction
    MIN_DATA_POINTS = 5
    
    # Default lookback period for predictions
    DEFAULT_LOOKBACK_DAYS = 30
    
    # Moving average window sizes
    SHORT_MA_WINDOW = 3   # 3-day moving average for short-term
    MEDIUM_MA_WINDOW = 7  # 7-day moving average for medium-term
    LONG_MA_WINDOW = 14   # 14-day moving average for long-term
    
    def __init__(self):
        pass
    
    def _calculate_moving_average(
        self, 
        prices: List[float], 
        window: int
    ) -> Optional[float]:
        """Calculate simple moving average"""
        if len(prices) < window:
            return None
        
        recent_prices = prices[-window:]
        return sum(recent_prices) / len(recent_prices)
    
    def _calculate_weighted_moving_average(
        self, 
        prices: List[float], 
        window: int
    ) -> Optional[float]:
        """
        Calculate weighted moving average
        More recent prices get higher weights
        """
        if len(prices) < window:
            return None
        
        recent_prices = prices[-window:]
        weights = list(range(1, len(recent_prices) + 1))
        weighted_sum = sum(p * w for p, w in zip(recent_prices, weights))
        total_weight = sum(weights)
        
        return weighted_sum / total_weight
    
    def _calculate_trend(
        self, 
        prices: List[float]
    ) -> Tuple[float, str]:
        """
        Calculate price trend using linear regression
        Returns (daily_change, trend_direction)
        """
        if len(prices) < 2:
            return 0.0, "stable"
        
        n = len(prices)
        x_values = list(range(n))
        
        # Calculate linear regression slope
        x_mean = sum(x_values) / n
        y_mean = sum(prices) / n
        
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, prices))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        
        if denominator == 0:
            return 0.0, "stable"
        
        slope = numerator / denominator
        
        # Determine trend direction based on percentage change
        if y_mean != 0:
            percent_change = (slope / y_mean) * 100
        else:
            percent_change = 0
        
        if percent_change > 1:
            trend = "rising"
        elif percent_change < -1:
            trend = "falling"
        else:
            trend = "stable"
        
        return slope, trend
    
    def _calculate_volatility(self, prices: List[float]) -> float:
        """Calculate price volatility (coefficient of variation)"""
        if len(prices) < 2:
            return 0.0
        
        mean_price = statistics.mean(prices)
        if mean_price == 0:
            return 0.0
        
        std_dev = statistics.stdev(prices)
        return std_dev / mean_price
    
    def _calculate_confidence_score(
        self, 
        data_points: int, 
        volatility: float, 
        trend_consistency: float
    ) -> float:
        """
        Calculate confidence score (0-1) for prediction
        Based on data quantity, volatility, and trend consistency
        """
        # Data points factor (more data = higher confidence)
        if data_points >= 30:
            data_score = 1.0
        elif data_points >= 14:
            data_score = 0.8
        elif data_points >= 7:
            data_score = 0.6
        else:
            data_score = 0.4
        
        # Volatility factor (lower volatility = higher confidence)
        if volatility < 0.05:
            volatility_score = 1.0
        elif volatility < 0.1:
            volatility_score = 0.8
        elif volatility < 0.2:
            volatility_score = 0.6
        else:
            volatility_score = 0.4
        
        # Combined confidence
        confidence = (data_score * 0.6) + (volatility_score * 0.4)
        
        return round(min(max(confidence, 0.0), 1.0), 2)
    
    async def get_prediction_data(
        self,
        db: AsyncSession,
        commodity: str,
        state: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get historical price data for prediction
        Returns aggregated daily prices
        """
        start_date = datetime.now() - timedelta(days=days)
        
        query = select(
            func.date(MandiPrice.arrival_date).label('date'),
            func.avg(MandiPrice.modal_price).label('avg_price'),
            func.min(MandiPrice.min_price).label('min_price'),
            func.max(MandiPrice.max_price).label('max_price'),
            func.count(MandiPrice.id).label('data_points')
        ).where(
            and_(
                MandiPrice.commodity.ilike(f"%{commodity}%"),
                MandiPrice.arrival_date >= start_date
            )
        )
        
        if state:
            query = query.where(MandiPrice.state.ilike(f"%{state}%"))
        if district:
            query = query.where(MandiPrice.district.ilike(f"%{district}%"))
        if market:
            query = query.where(MandiPrice.market.ilike(f"%{market}%"))
        
        query = query.group_by(func.date(MandiPrice.arrival_date)).order_by('date')
        
        result = await db.execute(query)
        rows = result.fetchall()
        
        return [
            {
                "date": row.date,
                "avg_price": float(row.avg_price),
                "min_price": float(row.min_price),
                "max_price": float(row.max_price),
                "data_points": row.data_points
            }
            for row in rows
        ]
    
    async def predict_price(
        self,
        db: AsyncSession,
        commodity: str,
        state: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None,
        days_ahead: int = 1,
        lookback_days: int = 30
    ) -> Dict[str, Any]:
        """
        Predict future price for a commodity
        
        Returns prediction only if sufficient historical data exists
        Otherwise returns "Prediction not available"
        """
        # Get historical data
        historical_data = await self.get_prediction_data(
            db=db,
            commodity=commodity,
            state=state,
            district=district,
            market=market,
            days=lookback_days
        )
        
        # Check if we have sufficient data
        if len(historical_data) < self.MIN_DATA_POINTS:
            return {
                "success": False,
                "commodity": commodity,
                "location": {
                    "state": state,
                    "district": district,
                    "market": market
                },
                "message": "Prediction not available",
                "reason": f"Insufficient historical data. Need at least {self.MIN_DATA_POINTS} days, have {len(historical_data)}",
                "data_points": len(historical_data)
            }
        
        # Extract prices for analysis
        prices = [d["avg_price"] for d in historical_data]
        
        # Calculate predictions using multiple methods
        # 1. Simple Moving Average (short-term)
        short_ma = self._calculate_moving_average(prices, self.SHORT_MA_WINDOW)
        
        # 2. Weighted Moving Average (gives more weight to recent prices)
        wma = self._calculate_weighted_moving_average(prices, self.MEDIUM_MA_WINDOW)
        
        # 3. Trend-based prediction
        slope, trend = self._calculate_trend(prices)
        trend_prediction = prices[-1] + (slope * days_ahead)
        
        # 4. Calculate volatility
        volatility = self._calculate_volatility(prices)
        
        # Combine predictions (ensemble approach)
        prediction_components = []
        
        if short_ma and short_ma > 0:
            prediction_components.append(("short_ma", short_ma, 0.3))
        
        if wma and wma > 0:
            prediction_components.append(("wma", wma, 0.4))
        
        if trend_prediction > 0:
            prediction_components.append(("trend", trend_prediction, 0.3))
        
        if not prediction_components:
            return {
                "success": False,
                "commodity": commodity,
                "message": "Prediction not available",
                "reason": "Unable to calculate prediction from available data"
            }
        
        # Weighted average of prediction methods
        total_weight = sum(p[2] for p in prediction_components)
        predicted_price = sum(p[1] * p[2] for p in prediction_components) / total_weight
        
        # Calculate confidence score
        confidence = self._calculate_confidence_score(
            data_points=len(historical_data),
            volatility=volatility,
            trend_consistency=1.0  # Simplified
        )
        
        # Get base price (most recent)
        base_price = prices[-1]
        
        # Calculate expected range based on volatility
        price_range = predicted_price * volatility
        min_expected = round(predicted_price - price_range, 2)
        max_expected = round(predicted_price + price_range, 2)
        
        # Store prediction in database
        prediction_record = PricePrediction(
            prediction_date=datetime.now() + timedelta(days=days_ahead),
            commodity=commodity,
            state=state,
            district=district,
            market=market,
            predicted_price=round(predicted_price, 2),
            prediction_method="ensemble_ma_trend",
            confidence_score=confidence,
            days_ahead=days_ahead,
            base_price=base_price,
            historical_data_points=len(historical_data)
        )
        db.add(prediction_record)
        await db.commit()
        
        return {
            "success": True,
            "commodity": commodity,
            "location": {
                "state": state,
                "district": district,
                "market": market
            },
            "prediction": {
                "date": (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
                "predicted_price": round(predicted_price, 2),
                "min_expected": min_expected,
                "max_expected": max_expected,
                "unit": "Rs/Quintal"
            },
            "analysis": {
                "trend": trend,
                "trend_slope": round(slope, 2),
                "volatility": round(volatility * 100, 2),  # As percentage
                "base_price": round(base_price, 2),
                "short_term_ma": round(short_ma, 2) if short_ma else None,
                "weighted_ma": round(wma, 2) if wma else None
            },
            "confidence": {
                "score": confidence,
                "level": "high" if confidence >= 0.7 else "medium" if confidence >= 0.5 else "low",
                "data_points_used": len(historical_data)
            },
            "methodology": "Ensemble of Moving Average and Trend Analysis",
            "days_ahead": days_ahead
        }
    
    async def predict_multi_day(
        self,
        db: AsyncSession,
        commodity: str,
        state: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None,
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Predict prices for multiple days ahead
        """
        predictions = []
        
        for day in range(1, days + 1):
            result = await self.predict_price(
                db=db,
                commodity=commodity,
                state=state,
                district=district,
                market=market,
                days_ahead=day
            )
            
            if result["success"]:
                predictions.append({
                    "day": day,
                    "date": result["prediction"]["date"],
                    "predicted_price": result["prediction"]["predicted_price"],
                    "min_expected": result["prediction"]["min_expected"],
                    "max_expected": result["prediction"]["max_expected"]
                })
            else:
                break  # Stop if we can't predict
        
        if not predictions:
            return {
                "success": False,
                "commodity": commodity,
                "message": "Prediction not available",
                "reason": "Insufficient historical data for multi-day prediction"
            }
        
        return {
            "success": True,
            "commodity": commodity,
            "location": {
                "state": state,
                "district": district,
                "market": market
            },
            "predictions": predictions,
            "days_predicted": len(predictions)
        }
    
    async def get_price_trend_explanation(
        self,
        db: AsyncSession,
        commodity: str,
        state: Optional[str] = None,
        language: str = "english"
    ) -> str:
        """
        Get a simple explanation of price trends
        This can be enhanced by Gemini AI to provide Tamil explanations
        """
        historical_data = await self.get_prediction_data(
            db=db,
            commodity=commodity,
            state=state,
            days=14
        )
        
        if len(historical_data) < 3:
            if language == "tamil":
                return "போதுமான தரவு இல்லை. சந்தை விலை போக்கை பகுப்பாய்வு செய்ய முடியவில்லை."
            return "Insufficient data. Cannot analyze market price trend."
        
        prices = [d["avg_price"] for d in historical_data]
        slope, trend = self._calculate_trend(prices)
        
        current_price = prices[-1]
        week_ago_price = prices[-7] if len(prices) >= 7 else prices[0]
        change_percent = ((current_price - week_ago_price) / week_ago_price) * 100 if week_ago_price > 0 else 0
        
        if language == "tamil":
            if trend == "rising":
                return (f"{commodity} விலை உயர்ந்து வருகிறது. "
                       f"தற்போதைய விலை: ₹{current_price:.0f}/குவிண்டால். "
                       f"கடந்த வாரம் {abs(change_percent):.1f}% அதிகரித்துள்ளது. "
                       f"விற்பனைக்கு நல்ல நேரம்.")
            elif trend == "falling":
                return (f"{commodity} விலை குறைந்து வருகிறது. "
                       f"தற்போதைய விலை: ₹{current_price:.0f}/குவிண்டால். "
                       f"கடந்த வாரம் {abs(change_percent):.1f}% குறைந்துள்ளது. "
                       f"விலை உயரும் வரை காத்திருக்கலாம்.")
            else:
                return (f"{commodity} விலை நிலையாக உள்ளது. "
                       f"தற்போதைய விலை: ₹{current_price:.0f}/குவிண்டால்.")
        else:
            if trend == "rising":
                return (f"{commodity} prices are rising. "
                       f"Current price: ₹{current_price:.0f}/Quintal. "
                       f"Up {abs(change_percent):.1f}% from last week. "
                       f"Good time to sell.")
            elif trend == "falling":
                return (f"{commodity} prices are falling. "
                       f"Current price: ₹{current_price:.0f}/Quintal. "
                       f"Down {abs(change_percent):.1f}% from last week. "
                       f"Consider waiting for prices to recover.")
            else:
                return (f"{commodity} prices are stable. "
                       f"Current price: ₹{current_price:.0f}/Quintal.")


# Singleton instance
price_predictor = PricePredictionService()
