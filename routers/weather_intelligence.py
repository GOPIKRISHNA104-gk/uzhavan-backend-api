"""
Weather Intelligence Service for Farmers
Complete weather intelligence system with:
- Day/Night detection using sunrise/sunset from API
- Weather code to icon mapping
- Tamil farmer advice engine
- 3-day/7-day rain alerts
- Caching for performance

Uses Open-Meteo API (free, no API key required)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
import httpx
import aiohttp
import asyncio

router = APIRouter()

# ============== CACHING ==============
# In-memory cache with TTL
_weather_cache: Dict[str, dict] = {}
CACHE_TTL_MINUTES = 15  # Cache for 15 minutes

def _get_cache_key(lat: float, lon: float) -> str:
    return f"{lat:.2f}_{lon:.2f}"

def _get_cached_data(lat: float, lon: float) -> Optional[dict]:
    key = _get_cache_key(lat, lon)
    if key in _weather_cache:
        cached = _weather_cache[key]
        cached_time = cached.get("_cached_at")
        if cached_time:
            age = datetime.now() - datetime.fromisoformat(cached_time)
            if age < timedelta(minutes=CACHE_TTL_MINUTES):
                return cached
    return None

def _set_cache(lat: float, lon: float, data: dict):
    key = _get_cache_key(lat, lon)
    data["_cached_at"] = datetime.now().isoformat()
    _weather_cache[key] = data


# ============== LOCATION DATABASE ==============
INDIA_LOCATIONS: Dict[str, Dict[str, float]] = {
    # Tamil Nadu
    "chennai": {"lat": 13.0827, "lon": 80.2707},
    "coimbatore": {"lat": 11.0168, "lon": 76.9558},
    "madurai": {"lat": 9.9252, "lon": 78.1198},
    "salem": {"lat": 11.6643, "lon": 78.1460},
    "trichy": {"lat": 10.7905, "lon": 78.7047},
    "tirunelveli": {"lat": 8.7139, "lon": 77.7567},
    "erode": {"lat": 11.3410, "lon": 77.7172},
    "vellore": {"lat": 12.9165, "lon": 79.1325},
    "thanjavur": {"lat": 10.7870, "lon": 79.1378},
    "dindigul": {"lat": 10.3673, "lon": 77.9803},
    "tirupur": {"lat": 11.1085, "lon": 77.3411},
    "karur": {"lat": 10.9601, "lon": 78.0766},
    "namakkal": {"lat": 11.2189, "lon": 78.1677},
    "villupuram": {"lat": 11.9401, "lon": 79.4861},
    "cuddalore": {"lat": 11.7480, "lon": 79.7714},
    # Karnataka
    "bangalore": {"lat": 12.9716, "lon": 77.5946},
    "bengaluru": {"lat": 12.9716, "lon": 77.5946},
    "mysore": {"lat": 12.2958, "lon": 76.6394},
    "hubli": {"lat": 15.3647, "lon": 75.1240},
    "mangalore": {"lat": 12.9141, "lon": 74.8560},
    # Andhra Pradesh & Telangana
    "hyderabad": {"lat": 17.3850, "lon": 78.4867},
    "vijayawada": {"lat": 16.5062, "lon": 80.6480},
    "visakhapatnam": {"lat": 17.6868, "lon": 83.2185},
    "guntur": {"lat": 16.3067, "lon": 80.4365},
    "tirupati": {"lat": 13.6288, "lon": 79.4192},
    # Kerala
    "kochi": {"lat": 9.9312, "lon": 76.2673},
    "trivandrum": {"lat": 8.5241, "lon": 76.9366},
    "kozhikode": {"lat": 11.2588, "lon": 75.7804},
    "thrissur": {"lat": 10.5276, "lon": 76.2144},
    # Maharashtra
    "mumbai": {"lat": 19.0760, "lon": 72.8777},
    "pune": {"lat": 18.5204, "lon": 73.8567},
    "nagpur": {"lat": 21.1458, "lon": 79.0882},
    "nashik": {"lat": 19.9975, "lon": 73.7898},
    # Gujarat
    "ahmedabad": {"lat": 23.0225, "lon": 72.5714},
    "surat": {"lat": 21.1702, "lon": 72.8311},
    "vadodara": {"lat": 22.3072, "lon": 73.1812},
    # North India
    "delhi": {"lat": 28.7041, "lon": 77.1025},
    "new delhi": {"lat": 28.6139, "lon": 77.2090},
    "jaipur": {"lat": 26.9124, "lon": 75.7873},
    "lucknow": {"lat": 26.8467, "lon": 80.9462},
    "patna": {"lat": 25.5941, "lon": 85.1376},
    "chandigarh": {"lat": 30.7333, "lon": 76.7794},
    "amritsar": {"lat": 31.6340, "lon": 74.8723},
    # East India
    "kolkata": {"lat": 22.5726, "lon": 88.3639},
    "bhubaneswar": {"lat": 20.2961, "lon": 85.8245},
    "guwahati": {"lat": 26.1445, "lon": 91.7362},
    # Central India
    "bhopal": {"lat": 23.2599, "lon": 77.4126},
    "indore": {"lat": 22.7196, "lon": 75.8577},
    "raipur": {"lat": 21.2514, "lon": 81.6296},
    # Default
    "india": {"lat": 20.5937, "lon": 78.9629},
}


# ============== ENUMS ==============

class TimeOfDay(str, Enum):
    DAY = "day"
    NIGHT = "night"

class WeatherCondition(str, Enum):
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    FOG = "fog"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    HEAVY_RAIN = "heavy_rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"
    UNKNOWN = "unknown"


# ============== SCHEMAS ==============

class SunTimes(BaseModel):
    sunrise: str
    sunset: str
    is_day: bool
    time_of_day: str  # "day" or "night"

class WeatherIcon(BaseModel):
    icon_name: str       # For Lucide icons: "sun", "moon", "cloud", etc.
    emoji: str           # Fallback emoji
    description: str     # Weather description

class CurrentWeather(BaseModel):
    temperature: float
    feels_like: float
    humidity: int
    wind_speed: float
    cloud_cover: int
    precipitation: float
    weather_code: int
    weather_condition: str
    weather_description: str
    is_day: bool
    time_of_day: str
    icon: WeatherIcon
    sun_times: SunTimes

class DailyForecast(BaseModel):
    date: str
    day_name: str
    temp_max: float
    temp_min: float
    precipitation_sum: float
    rain_sum: float
    weather_code: int
    weather_condition: str
    weather_description: str
    icon: WeatherIcon

class RainAlertDay(BaseModel):
    day_number: int
    date: str
    day_name: str
    rain_mm: float

class RainAlert(BaseModel):
    has_alert: bool
    alert_level: str  # "none", "light", "moderate", "heavy"
    alert_message: str
    alert_message_tamil: str
    rain_days: List[RainAlertDay]
    total_rain_mm: float

class FarmerAdvice(BaseModel):
    advice_tamil: str
    advice_english: str
    weather_based: bool
    alert_based: bool

class WeatherIntelligenceResponse(BaseModel):
    location: str
    latitude: float
    longitude: float
    timezone: str
    current: CurrentWeather
    daily_forecast: List[DailyForecast]
    rain_alert: RainAlert
    farmer_advice: FarmerAdvice
    last_updated: str
    cached: bool


# ============== WEATHER CODE MAPPING ==============

def get_weather_condition(code: int) -> WeatherCondition:
    """Map WMO weather code to condition enum"""
    if code == 0:
        return WeatherCondition.CLEAR
    elif code in [1, 2]:
        return WeatherCondition.PARTLY_CLOUDY
    elif code == 3:
        return WeatherCondition.CLOUDY
    elif code in [45, 48]:
        return WeatherCondition.FOG
    elif code in [51, 53, 55, 56, 57]:
        return WeatherCondition.DRIZZLE
    elif code in [61, 63, 65, 66, 67]:
        return WeatherCondition.RAIN
    elif code in [80, 81, 82]:
        return WeatherCondition.HEAVY_RAIN
    elif code in [95, 96, 99]:
        return WeatherCondition.THUNDERSTORM
    elif code in [71, 73, 75, 77, 85, 86]:
        return WeatherCondition.SNOW
    else:
        return WeatherCondition.UNKNOWN


def get_weather_description(code: int) -> str:
    """Get human-readable weather description"""
    descriptions = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
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
    return descriptions.get(code, "Unknown")


def get_weather_icon(condition: WeatherCondition, is_day: bool) -> WeatherIcon:
    """
    Get weather icon based on condition and time of day
    Returns icon_name for Lucide React icons
    """
    icon_mapping = {
        # Clear Sky (code 0)
        WeatherCondition.CLEAR: {
            "day": WeatherIcon(icon_name="sun", emoji="☀️", description="Clear sky"),
            "night": WeatherIcon(icon_name="moon", emoji="🌙", description="Clear night"),
        },
        # Partly Cloudy (codes 1, 2)
        WeatherCondition.PARTLY_CLOUDY: {
            "day": WeatherIcon(icon_name="cloud-sun", emoji="⛅", description="Partly cloudy"),
            "night": WeatherIcon(icon_name="cloud-moon", emoji="☁️🌙", description="Partly cloudy night"),
        },
        # Cloudy (code 3)
        WeatherCondition.CLOUDY: {
            "day": WeatherIcon(icon_name="cloud", emoji="☁️", description="Cloudy"),
            "night": WeatherIcon(icon_name="cloud", emoji="☁️", description="Cloudy"),
        },
        # Fog (codes 45, 48)
        WeatherCondition.FOG: {
            "day": WeatherIcon(icon_name="cloud-fog", emoji="🌫️", description="Foggy"),
            "night": WeatherIcon(icon_name="cloud-fog", emoji="🌫️", description="Foggy"),
        },
        # Drizzle (codes 51-57)
        WeatherCondition.DRIZZLE: {
            "day": WeatherIcon(icon_name="cloud-drizzle", emoji="🌦️", description="Drizzle"),
            "night": WeatherIcon(icon_name="cloud-drizzle", emoji="🌦️", description="Drizzle"),
        },
        # Rain (codes 61-67)
        WeatherCondition.RAIN: {
            "day": WeatherIcon(icon_name="cloud-rain", emoji="🌧️", description="Rain"),
            "night": WeatherIcon(icon_name="cloud-rain", emoji="🌧️", description="Rain"),
        },
        # Heavy Rain (codes 80-82)
        WeatherCondition.HEAVY_RAIN: {
            "day": WeatherIcon(icon_name="cloud-rain-wind", emoji="🌧️💨", description="Heavy rain"),
            "night": WeatherIcon(icon_name="cloud-rain-wind", emoji="🌧️💨", description="Heavy rain"),
        },
        # Thunderstorm (codes 95-99)
        WeatherCondition.THUNDERSTORM: {
            "day": WeatherIcon(icon_name="cloud-lightning", emoji="⛈️", description="Thunderstorm"),
            "night": WeatherIcon(icon_name="cloud-lightning", emoji="⛈️", description="Thunderstorm"),
        },
        # Snow
        WeatherCondition.SNOW: {
            "day": WeatherIcon(icon_name="snowflake", emoji="❄️", description="Snow"),
            "night": WeatherIcon(icon_name="snowflake", emoji="❄️", description="Snow"),
        },
        # Unknown
        WeatherCondition.UNKNOWN: {
            "day": WeatherIcon(icon_name="cloud", emoji="🌤️", description="Unknown"),
            "night": WeatherIcon(icon_name="cloud-moon", emoji="☁️", description="Unknown"),
        },
    }
    
    time_key = "day" if is_day else "night"
    return icon_mapping.get(condition, icon_mapping[WeatherCondition.UNKNOWN])[time_key]


# ============== DAY/NIGHT DETECTION ==============

def is_daytime(current_time_str: str, sunrise_str: str, sunset_str: str) -> bool:
    """
    Determine if it's day or night using API-provided sunrise/sunset times
    
    RULE: IF current_time >= sunrise AND current_time < sunset → DAY, ELSE → NIGHT
    
    Uses API data only, NOT hardcoded hours or device time
    """
    try:
        # Parse ISO format times from API
        # Format: "2024-02-08T06:30" or "2024-02-08T18:15"
        current = datetime.fromisoformat(current_time_str.replace("Z", "+00:00"))
        sunrise = datetime.fromisoformat(sunrise_str.replace("Z", "+00:00"))
        sunset = datetime.fromisoformat(sunset_str.replace("Z", "+00:00"))
        
        # Apply the rule: current >= sunrise AND current < sunset
        return sunrise <= current < sunset
    except Exception:
        # Fallback: assume day between 6 AM and 6 PM
        try:
            hour = datetime.fromisoformat(current_time_str).hour
            return 6 <= hour < 18
        except:
            return True  # Default to day


# ============== FARMER ADVICE ENGINE ==============

def generate_farmer_advice(
    weather_code: int,
    temperature: float,
    humidity: int,
    rain_alert: RainAlert
) -> FarmerAdvice:
    """
    Generate farmer-friendly advice based on weather conditions
    Primary language: Tamil
    """
    condition = get_weather_condition(weather_code)
    
    # Weather-based advice (Tamil primary)
    weather_advice_tamil = ""
    weather_advice_english = ""
    
    if condition == WeatherCondition.CLEAR:
        weather_advice_tamil = "வானிலை தெளிவு. விவசாய பணிகளுக்கு ஏற்ற நாள்."
        weather_advice_english = "Clear weather. Good day for farming activities."
    
    elif condition == WeatherCondition.PARTLY_CLOUDY:
        weather_advice_tamil = "ஓரளவு மேகமூட்டம். வழக்கமான பணிகளை தொடரலாம்."
        weather_advice_english = "Partly cloudy. Continue regular activities."
    
    elif condition == WeatherCondition.CLOUDY:
        weather_advice_tamil = "மேகமூட்டமான வானிலை. மழை வாய்ப்பு குறைவு."
        weather_advice_english = "Cloudy weather. Low chance of rain."
    
    elif condition == WeatherCondition.FOG:
        weather_advice_tamil = "மூடுபனி. காலை நேர பணிகளை தாமதிக்கவும்."
        weather_advice_english = "Fog present. Delay morning field work."
    
    elif condition == WeatherCondition.DRIZZLE:
        weather_advice_tamil = "தூறல் மழை. தெளிப்பு பணிகளை தவிர்க்கவும்."
        weather_advice_english = "Light drizzle. Avoid spraying activities."
    
    elif condition in [WeatherCondition.RAIN, WeatherCondition.HEAVY_RAIN]:
        weather_advice_tamil = "மழை வாய்ப்பு உள்ளது. தெளிப்பு மற்றும் அறுவடையை தவிர்க்கவும்."
        weather_advice_english = "Rain expected. Avoid spraying and harvesting."
    
    elif condition == WeatherCondition.THUNDERSTORM:
        weather_advice_tamil = "இடி மின்னல் அபாயம். வெளிப்பணிகளை தவிர்க்கவும்."
        weather_advice_english = "Thunderstorm danger. Avoid outdoor work."
    
    else:
        weather_advice_tamil = "சாதாரண வானிலை. பணிகளை தொடரலாம்."
        weather_advice_english = "Normal weather. Continue activities."
    
    # Temperature-based additions
    if temperature > 38:
        weather_advice_tamil += " வெப்பம் அதிகம். நீர்ப்பாசனம் அவசியம்."
        weather_advice_english += " High heat. Irrigation essential."
    elif temperature < 15:
        weather_advice_tamil += " குளிர் அதிகம். பயிர்களை பாதுகாக்கவும்."
        weather_advice_english += " Cold weather. Protect crops."
    
    # Humidity-based additions
    if humidity > 85:
        weather_advice_tamil += " ஈரப்பதம் அதிகம். பூஞ்சான் நோய் கவனம்."
        weather_advice_english += " High humidity. Watch for fungal diseases."
    
    # Rain alert-based advice
    alert_advice_tamil = ""
    alert_advice_english = ""
    
    if rain_alert.has_alert:
        if rain_alert.alert_level == "heavy":
            alert_advice_tamil = "⚠️ கனமழை எதிர்பார்ப்பு. வயலில் நீர் தேங்காமல் கவனிக்கவும்."
            alert_advice_english = "⚠️ Heavy rain expected. Ensure proper drainage."
        elif rain_alert.alert_level == "moderate":
            alert_advice_tamil = "⚠️ 3 நாட்களில் மழை எதிர்பார்க்கப்படுகிறது. விதைப்பு மற்றும் அறுவடையை திட்டமிடவும்."
            alert_advice_english = "⚠️ Rain expected in 3 days. Plan sowing and harvesting."
        else:
            alert_advice_tamil = "சிறு மழை வாய்ப்பு. இயற்கை நீர்ப்பாசனத்திற்கு நல்லது."
            alert_advice_english = "Light rain possible. Good for natural irrigation."
    
    # Combine advice
    final_tamil = weather_advice_tamil
    final_english = weather_advice_english
    
    if alert_advice_tamil:
        final_tamil = alert_advice_tamil + " " + weather_advice_tamil
        final_english = alert_advice_english + " " + weather_advice_english
    
    return FarmerAdvice(
        advice_tamil=final_tamil,
        advice_english=final_english,
        weather_based=True,
        alert_based=rain_alert.has_alert
    )


# ============== RAIN ALERT ENGINE ==============

def calculate_rain_alert(daily_data: List[dict]) -> RainAlert:
    """
    Calculate 3-day advance rain alert for farmers
    
    RULE: IF any of the next 3 days has rain_mm > 5 → Trigger advance alert
    """
    rain_days = []
    total_rain = 0.0
    
    # Check next 3 days (skip today, check days 1, 2, 3)
    for i, day in enumerate(daily_data[1:4], start=1):
        rain_mm = day.get("rain_sum", 0) or 0
        precip_mm = day.get("precipitation_sum", 0) or 0
        actual_rain = max(rain_mm, precip_mm)
        
        if actual_rain > 0:
            date_str = day.get("date", "")
            try:
                date_obj = datetime.fromisoformat(date_str)
                day_name = date_obj.strftime("%A")
            except:
                day_name = f"Day {i}"
            
            rain_days.append(RainAlertDay(
                day_number=i,
                date=date_str,
                day_name=day_name,
                rain_mm=actual_rain
            ))
            total_rain += actual_rain
    
    # Determine alert level based on total rain in 3 days
    if total_rain == 0:
        return RainAlert(
            has_alert=False,
            alert_level="none",
            alert_message="No rain expected in the next 3 days. Good conditions for field work.",
            alert_message_tamil="அடுத்த 3 நாட்களில் மழை எதிர்பார்க்கப்படவில்லை. வயல் பணிகளுக்கு நல்ல நிலை.",
            rain_days=[],
            total_rain_mm=0
        )
    elif total_rain < 5:
        return RainAlert(
            has_alert=True,
            alert_level="light",
            alert_message=f"Light rain expected ({total_rain:.1f}mm). Plan outdoor activities accordingly.",
            alert_message_tamil=f"சிறு மழை எதிர்பார்க்கப்படுகிறது ({total_rain:.1f}mm). வெளிப்புற பணிகளை திட்டமிடவும்.",
            rain_days=rain_days,
            total_rain_mm=total_rain
        )
    elif total_rain < 20:
        return RainAlert(
            has_alert=True,
            alert_level="moderate",
            alert_message=f"Moderate rain expected ({total_rain:.1f}mm). Postpone spraying and harvesting.",
            alert_message_tamil=f"⚠️ 3 நாட்களில் மழை எதிர்பார்க்கப்படுகிறது ({total_rain:.1f}mm). விதைப்பு மற்றும் அறுவடையை திட்டமிடவும்.",
            rain_days=rain_days,
            total_rain_mm=total_rain
        )
    else:
        return RainAlert(
            has_alert=True,
            alert_level="heavy",
            alert_message=f"Heavy rain alert ({total_rain:.1f}mm)! Protect crops and secure equipment.",
            alert_message_tamil=f"⚠️ கனமழை எச்சரிக்கை ({total_rain:.1f}mm)! பயிர்களை பாதுகாக்கவும், உபகரணங்களை சேமிக்கவும்.",
            rain_days=rain_days,
            total_rain_mm=total_rain
        )


# ============== HELPER FUNCTIONS ==============

def get_coordinates(location: str) -> tuple:
    """Convert location name to lat/lon coordinates"""
    location_lower = location.lower().strip()
    
    # Try exact match
    if location_lower in INDIA_LOCATIONS:
        coords = INDIA_LOCATIONS[location_lower]
        return coords["lat"], coords["lon"]
    
    # Try partial match
    for city, coords in INDIA_LOCATIONS.items():
        if city in location_lower or location_lower in city:
            return coords["lat"], coords["lon"]
    
    # Default to center of India
    return 20.5937, 78.9629


def get_day_name(date_str: str) -> str:
    """Get day name from date string"""
    try:
        date_obj = datetime.fromisoformat(date_str)
        return date_obj.strftime("%A")
    except:
        return ""


# ============== API ENDPOINTS ==============

@router.get("/intelligence", response_model=WeatherIntelligenceResponse)
async def get_weather_intelligence(
    location: str = "chennai",
    lat: Optional[float] = None,
    lon: Optional[float] = None
):
    """
    Get comprehensive weather intelligence for farmers
    
    Features:
    - Day/Night detection using sunrise/sunset
    - Weather icons based on condition + time of day
    - Tamil farmer advice
    - 3-day rain alerts
    - 7-day forecast
    - Caching for performance
    """
    
    # Get coordinates
    if lat is not None and lon is not None:
        latitude, longitude = lat, lon
    else:
        latitude, longitude = get_coordinates(location)
    
    # Check cache first
    cached_data = _get_cached_data(latitude, longitude)
    if cached_data and "_response" in cached_data:
        cached_response = cached_data["_response"]
        cached_response["cached"] = True
        return cached_response
    
    # Build Open-Meteo API URL with all required data
    api_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,cloud_cover,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,rain,weather_code",
        "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,weather_code",
        "timezone": "auto",  # Auto-detect based on coordinates
        "forecast_days": 7
    }
    
    data = None
    # Try aiohttp first (more reliable on Render), then httpx as fallback
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            full_url = f"{api_url}?latitude={latitude}&longitude={longitude}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,cloud_cover,precipitation&hourly=temperature_2m,relative_humidity_2m,precipitation,rain,weather_code&daily=sunrise,sunset,temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,weather_code&timezone=auto&forecast_days=7"
            async with session.get(full_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                else:
                    text = await resp.text()
                    print(f"[WEATHER] aiohttp got status {resp.status}: {text[:200]}")
    except Exception as e1:
        print(f"[WEATHER] aiohttp failed: {type(e1).__name__}: {e1}")
    
    # Fallback to httpx if aiohttp failed
    if data is None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(api_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                else:
                    print(f"[WEATHER] httpx got status {response.status_code}: {response.text[:200]}")
        except Exception as e2:
            print(f"[WEATHER] httpx failed: {type(e2).__name__}: {e2}")
    
    # Fallback to synchronous requests library (last resort)
    if data is None:
        try:
            import requests as sync_requests
            full_url = f"{api_url}?latitude={latitude}&longitude={longitude}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,cloud_cover,precipitation&daily=sunrise,sunset,temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,weather_code&timezone=auto&forecast_days=7"
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: sync_requests.get(full_url, timeout=15))
            if resp.status_code == 200:
                data = resp.json()
                print(f"[WEATHER] SUCCESS via sync requests!")
            else:
                print(f"[WEATHER] sync requests got status {resp.status_code}")
        except Exception as e3:
            print(f"[WEATHER] sync requests also failed: {type(e3).__name__}: {e3}")
    
    # If ALL methods failed, use cache or error
    if data is None:
        if cached_data:
            cached_response = cached_data.get("_response", {})
            cached_response["cached"] = True
            return cached_response
        raise HTTPException(
            status_code=500,
            detail="வானிலை தரவை பெற முடியவில்லை. மீண்டும் முயற்சிக்கவும்."
        )
    
    # Parse API response - wrapped in try/except for debugging
    try:
        timezone = data.get("timezone", "Asia/Kolkata")
        current_data = data.get("current", {})
        daily_data = data.get("daily", {})
        
        # Get current time and sun times from API
        current_time = current_data.get("time", datetime.now().isoformat())
        
        # Get today's sunrise/sunset
        sunrise_times = daily_data.get("sunrise", [])
        sunset_times = daily_data.get("sunset", [])
        
        today_sunrise = sunrise_times[0] if sunrise_times else "06:00"
        today_sunset = sunset_times[0] if sunset_times else "18:00"
        
        # Determine day/night using API data
        is_day = is_daytime(current_time, today_sunrise, today_sunset)
        time_of_day = TimeOfDay.DAY if is_day else TimeOfDay.NIGHT
        
        # Get weather condition and icon
        weather_code = current_data.get("weather_code", 0)
        condition = get_weather_condition(weather_code)
        icon = get_weather_icon(condition, is_day)
        
        # Build current weather
        current_weather = CurrentWeather(
            temperature=current_data.get("temperature_2m", 0),
            feels_like=current_data.get("apparent_temperature", 0),
            humidity=current_data.get("relative_humidity_2m", 50),
            wind_speed=current_data.get("wind_speed_10m", 0),
            cloud_cover=current_data.get("cloud_cover", 0),
            precipitation=current_data.get("precipitation", 0),
            weather_code=weather_code,
            weather_condition=condition.value,
            weather_description=get_weather_description(weather_code),
            is_day=is_day,
            time_of_day=time_of_day.value,
            icon=icon,
            sun_times=SunTimes(
                sunrise=today_sunrise,
                sunset=today_sunset,
                is_day=is_day,
                time_of_day=time_of_day.value
            )
        )
        
        # Build 7-day forecast
        dates = daily_data.get("time", [])
        temp_max = daily_data.get("temperature_2m_max", [])
        temp_min = daily_data.get("temperature_2m_min", [])
        precip_sum = daily_data.get("precipitation_sum", [])
        rain_sum = daily_data.get("rain_sum", [])
        weather_codes = daily_data.get("weather_code", daily_data.get("weathercode", []))
        
        daily_forecast = []
        for i in range(min(7, len(dates))):
            day_code = weather_codes[i] if i < len(weather_codes) else 0
            day_condition = get_weather_condition(day_code)
            day_icon = get_weather_icon(day_condition, True)  # Use day icon for forecast
            
            daily_forecast.append(DailyForecast(
                date=dates[i] if i < len(dates) else "",
                day_name=get_day_name(dates[i]) if i < len(dates) else "",
                temp_max=temp_max[i] if i < len(temp_max) else 0,
                temp_min=temp_min[i] if i < len(temp_min) else 0,
                precipitation_sum=precip_sum[i] if i < len(precip_sum) else 0,
                rain_sum=rain_sum[i] if i < len(rain_sum) else 0,
                weather_code=day_code,
                weather_condition=day_condition.value,
                weather_description=get_weather_description(day_code),
                icon=day_icon
            ))
        
        # Calculate rain alert
        daily_data_for_alert = [
            {
                "date": dates[i] if i < len(dates) else "",
                "rain_sum": rain_sum[i] if i < len(rain_sum) else 0,
                "precipitation_sum": precip_sum[i] if i < len(precip_sum) else 0
            }
            for i in range(min(4, len(dates)))
        ]
        rain_alert = calculate_rain_alert(daily_data_for_alert)
        
        # Generate farmer advice
        farmer_advice = generate_farmer_advice(
            weather_code=weather_code,
            temperature=current_weather.temperature,
            humidity=current_weather.humidity,
            rain_alert=rain_alert
        )
        
        # Build response
        result = WeatherIntelligenceResponse(
            location=location.title(),
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
            current=current_weather,
            daily_forecast=daily_forecast,
            rain_alert=rain_alert,
            farmer_advice=farmer_advice,
            last_updated=datetime.now().isoformat(),
            cached=False
        )
        
        # Cache the response
        cache_data = {"_response": result.model_dump()}
        _set_cache(latitude, longitude, cache_data)
        
        return result
    except Exception as parse_error:
        print(f"[WEATHER ERROR] Data parsing failed: {parse_error}")
        print(f"[WEATHER DEBUG] API keys: {list(data.keys()) if data else 'NO DATA'}")
        raise HTTPException(
            status_code=500,
            detail=f"Weather processing error: {str(parse_error)}"
        )


@router.get("/rain-alert")
async def get_rain_alert_only(
    location: str = "chennai",
    lat: Optional[float] = None,
    lon: Optional[float] = None
) -> RainAlert:
    """
    Get just the 3-day rain alert (lightweight endpoint)
    Useful for dashboard notifications
    """
    
    if lat is not None and lon is not None:
        latitude, longitude = lat, lon
    else:
        latitude, longitude = get_coordinates(location)
    
    api_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "precipitation_sum,rain_sum",
        "timezone": "auto",
        "forecast_days": 4
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url, params=params)
            data = response.json()
    except Exception:
        return RainAlert(
            has_alert=False,
            alert_level="none",
            alert_message="Unable to fetch rain data",
            alert_message_tamil="மழை தரவை பெற முடியவில்லை",
            rain_days=[],
            total_rain_mm=0
        )
    
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
    """Get list of available locations for weather data"""
    return {
        "locations": sorted(list(INDIA_LOCATIONS.keys())),
        "total": len(INDIA_LOCATIONS)
    }


@router.get("/icon-test")
async def test_weather_icons():
    """Test endpoint to see all icon mappings (development only)"""
    icons = []
    for condition in WeatherCondition:
        icons.append({
            "condition": condition.value,
            "day_icon": get_weather_icon(condition, True).model_dump(),
            "night_icon": get_weather_icon(condition, False).model_dump()
        })
    return {"icons": icons}
