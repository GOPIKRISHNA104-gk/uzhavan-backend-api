"""
Open-Meteo Weather Router - Free Weather API for India
Includes 3-day early rain alert system for farmers

PERFORMANCE OPTIMIZATIONS:
- Service Layer Architecture (weather_service.py)
- Cached responses (SQLite + Memory)
- Background prefetching support
- 3-second timeout for user requests
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
import logging

from database import get_db
from auth_deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# ============== SCHEMAS ==============

class WeatherCurrent(BaseModel):
    temperature: float
    humidity: int
    windspeed: float
    cloudcover: int
    precipitation: float
    weather_description: str

class WeatherDaily(BaseModel):
    date: str
    temp_max: float
    temp_min: float
    precipitation_sum: float
    rain_sum: float
    weather_description: str

class RainAlert(BaseModel):
    has_alert: bool
    alert_level: str  # "none", "light", "moderate", "heavy"
    alert_message: str
    rain_days: List[Dict]

class WeatherResponse(BaseModel):
    location: str
    latitude: float
    longitude: float
    current: WeatherCurrent
    daily_forecast: List[WeatherDaily]
    rain_alert: RainAlert
    farming_advisory: str
    last_updated: str


# ============== HELPER FUNCTIONS ==============

def get_weather_description(weathercode: int) -> str:
    """Convert WMO weather code to description"""
    weather_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return weather_codes.get(weathercode, "Unknown")


def calculate_rain_alert(daily_data: List[dict]) -> RainAlert:
    """
    Calculate 3-day early rain alert for farmers
    Checks next 3 days for rain/precipitation
    """
    rain_days = []
    total_rain = 0
    
    # Check next 3 days (skip today, check day+1, day+2, day+3)
    for i, day in enumerate(daily_data[1:4], start=1):  # Days 1, 2, 3
        rain_sum = day.get("rain_sum", 0) or 0
        precip_sum = day.get("precipitation_sum", 0) or 0
        
        if rain_sum > 0 or precip_sum > 0:
            rain_days.append({
                "day": i,
                "date": day.get("date", ""),
                "rain_mm": rain_sum,
                "precipitation_mm": precip_sum
            })
            total_rain += max(rain_sum, precip_sum)
    
    # Determine alert level
    if total_rain == 0:
        return RainAlert(
            has_alert=False,
            alert_level="none",
            alert_message="No rain expected in the next 3 days. Good conditions for field work.",
            rain_days=[]
        )
    elif total_rain < 5:
        return RainAlert(
            has_alert=True,
            alert_level="light",
            alert_message=f"Light rain expected ({total_rain:.1f}mm). Plan outdoor activities accordingly.",
            rain_days=rain_days
        )
    elif total_rain < 20:
        return RainAlert(
            has_alert=True,
            alert_level="moderate",
            alert_message=f"Moderate rain expected ({total_rain:.1f}mm). Postpone spraying and harvesting.",
            rain_days=rain_days
        )
    else:
        return RainAlert(
            has_alert=True,
            alert_level="heavy",
            alert_message=f"Heavy rain alert ({total_rain:.1f}mm)! Protect crops and secure equipment.",
            rain_days=rain_days
        )


def generate_farming_advisory(current: dict, rain_alert: RainAlert) -> str:
    """Generate farming advisory based on weather conditions"""
    temp = current.get("temperature", 25)
    humidity = current.get("humidity", 50)
    
    advisories = []
    
    # Temperature advice
    if temp > 35:
        advisories.append("High temperature: Irrigate crops early morning or evening.")
    elif temp < 15:
        advisories.append("Low temperature: Protect sensitive crops from cold.")
    
    # Humidity advice
    if humidity > 80:
        advisories.append("High humidity: Watch for fungal diseases.")
    elif humidity < 30:
        advisories.append("Low humidity: Increase irrigation frequency.")
    
    # Rain alert advice
    if rain_alert.has_alert:
        if rain_alert.alert_level == "light":
            advisories.append("Light rain coming: Good for natural irrigation.")
        elif rain_alert.alert_level == "moderate":
            advisories.append("Rain expected: Delay pesticide spraying.")
        elif rain_alert.alert_level == "heavy":
            advisories.append("Heavy rain warning: Ensure proper drainage.")
    else:
        advisories.append("Clear weather: Good conditions for all farm activities.")
    
    return " ".join(advisories) if advisories else "Normal weather conditions."


# ============== API ENDPOINTS ==============

@router.get("/forecast")
async def get_weather_forecast(
    location: str = "chennai",
    db: AsyncSession = Depends(get_db)
):
    """
    Get weather forecast with 3-day rain alert
    Uses Open-Meteo API via WeatherService
    """
    from services.weather_service import weather_service
    
    # 1. Fetch data from Service (handles Cache + API)
    service_response = await weather_service.get_forecast(location)
    
    if "error" in service_response:
        # Check if we have fallback data despite error
        if not service_response.get("raw_data"):
            raise HTTPException(status_code=502, detail=service_response["error"])
    
    data = service_response.get("raw_data", {})
    if not data:
        raise HTTPException(status_code=500, detail="Weather data empty")
        
    # Get coordinates back from service response
    lat = service_response.get("latitude", 0)
    lon = service_response.get("longitude", 0)

    # 2. Process Data for Response
    current_weather = data.get("current_weather", {})
    hourly = data.get("hourly", {})
    
    # Get current hour index
    current_hour = datetime.now().hour
    
    current = WeatherCurrent(
        temperature=current_weather.get("temperature", 0),
        humidity=hourly.get("relative_humidity_2m", [0]*24)[current_hour] if hourly.get("relative_humidity_2m") else 0,
        windspeed=current_weather.get("windspeed", 0),
        cloudcover=hourly.get("cloudcover", [0])[current_hour] if hourly.get("cloudcover") else 0,
        precipitation=hourly.get("precipitation", [0])[current_hour] if hourly.get("precipitation") else 0,
        weather_description=get_weather_description(current_weather.get("weathercode", 0))
    )
    
    daily = data.get("daily", {})
    daily_forecast = []
    
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precip_sum = daily.get("precipitation_sum", [])
    rain_sum = daily.get("rain_sum", [])
    weather_codes = daily.get("weathercode", [])
    
    for i in range(min(7, len(dates))):
        daily_forecast.append(WeatherDaily(
            date=dates[i] if i < len(dates) else "",
            temp_max=temp_max[i] if i < len(temp_max) else 0,
            temp_min=temp_min[i] if i < len(temp_min) else 0,
            precipitation_sum=precip_sum[i] if i < len(precip_sum) else 0,
            rain_sum=rain_sum[i] if i < len(rain_sum) else 0,
            weather_description=get_weather_description(weather_codes[i]) if i < len(weather_codes) else "Unknown"
        ))
    
    # Calculate 3-day rain alert
    daily_data_for_alert = [
        {
            "date": dates[i] if i < len(dates) else "",
            "rain_sum": rain_sum[i] if i < len(rain_sum) else 0,
            "precipitation_sum": precip_sum[i] if i < len(precip_sum) else 0
        }
        for i in range(min(4, len(dates)))
    ]
    
    rain_alert = calculate_rain_alert(daily_data_for_alert)
    
    # Generate farming advisory
    farming_advisory = generate_farming_advisory(
        {"temperature": current.temperature, "humidity": current.humidity},
        rain_alert
    )
    
    return WeatherResponse(
        location=location.title(),
        latitude=lat,
        longitude=lon,
        current=current,
        daily_forecast=daily_forecast,
        rain_alert=rain_alert,
        farming_advisory=farming_advisory,
        last_updated=datetime.now().isoformat()
    )


@router.get("/rain-alert")
async def get_rain_alert(
    location: str = "chennai",
    db: AsyncSession = Depends(get_db)
):
    """
    Quick endpoint to get just the 3-day rain alert
    """
    from services.weather_service import weather_service
    
    service_response = await weather_service.get_forecast(location)
    data = service_response.get("raw_data", {})
    
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    rain_sum = daily.get("rain_sum", [])
    precip_sum = daily.get("precipitation_sum", [])
    
    daily_data = [
        {
            "date": dates[i] if i < len(dates) else "",
            "rain_sum": rain_sum[i] if i < len(rain_sum) else 0,
            "precipitation_sum": precip_sum[i] if i < len(precip_sum) else 0
        }
        for i in range(min(4, len(dates)))
    ]
    
    return calculate_rain_alert(daily_data)


@router.get("/locations")
async def get_available_locations():
    """Get list of available locations"""
    from services.weather_service import INDIA_LOCATIONS
    return {
        "locations": sorted(list(INDIA_LOCATIONS.keys())),
        "total": len(INDIA_LOCATIONS)
    }
