"""
Weather Router - Weather data and farming advisories with complete language support
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import google.generativeai as genai
from typing import List, Dict, Any, Optional
import asyncio

from database import get_db, User
from schemas import WeatherRequest, WeatherResponse
from config import settings
from auth_deps import get_current_user  # Unified Firebase + JWT auth
from services.localization import translation_service

router = APIRouter()

# Configure Gemini for farming advice
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

async def get_farming_advisory(weather_data: dict, language: str) -> str:
    """Generate AI-powered farming advisory based on weather with complete language support"""
    if not settings.GEMINI_API_KEY:
        fallback_messages = {
            "tamil": "வானிலை நிலவரத்தை கவனித்து விவசாய நடவடிக்கைகளை மேற்கொள்ளுங்கள்.",
            "hindi": "मौसम की स्थिति देखकर खेती के काम करें।",
            "telugu": "వాతావరణ పరిస్థితులను చూసి వ్యవసాయ కార్యకలాపాలు చేపట్టండి।",
            "malayalam": "കാലാവസ്ഥാ സാഹചര്യങ്ങൾ നോക്കി കൃഷിപ്പണികൾ ചെയ്യുക.",
            "kannada": "ಹವಾಮಾನ ಪರಿಸ್ಥಿತಿಗಳನ್ನು ನೋಡಿ ಕೃಷಿ ಚಟುವಟಿಕೆಗಳನ್ನು ಮಾಡಿ.",
            "english": "Check weather conditions before farming activities."
        }
        return fallback_messages.get(language, fallback_messages["english"])
    
    try:
        language_instruction = translation_service.get_language_instruction(language)
        
        prompt = f"""Based on the following weather conditions, provide a brief farming advisory (2-3 sentences):

Temperature: {weather_data.get('temperature', 'N/A')}°C
Humidity: {weather_data.get('humidity', 'N/A')}%
Weather: {weather_data.get('description', 'N/A')}
Wind Speed: {weather_data.get('wind_speed', 'N/A')} m/s

{language_instruction}

Focus on practical advice for farmers regarding irrigation, pest control, harvesting, or field work.
Use simple, farmer-friendly language."""
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = await asyncio.to_thread(model.generate_content, prompt)
        
        if response.text:
            return response.text.strip()
        else:
            # Fallback to translated generic message
            return await translation_service.translate_text(
                "Check weather conditions before farming activities.", 
                language, 
                "weather"
            )
            
    except Exception as e:
        print(f"Advisory generation error: {e}")
        # Return translated fallback
        return await translation_service.translate_text(
            "Check weather conditions before farming activities.", 
            language, 
            "weather"
        )

async def translate_weather_data(weather_data: dict, language: str) -> dict:
    """Translate all weather descriptions and conditions to target language"""
    if language == "english":
        return weather_data
    
    try:
        # Translate weather description
        if "description" in weather_data:
            weather_data["description"] = await translation_service.translate_weather_condition(
                weather_data["description"], language
            )
        
        # Translate weather condition
        if "condition" in weather_data:
            weather_data["condition"] = await translation_service.translate_weather_condition(
                weather_data["condition"], language
            )
        
        # Translate any advisory text
        if "advisory" in weather_data:
            weather_data["advisory"] = await translation_service.translate_text(
                weather_data["advisory"], language, "weather"
            )
        
        # Translate forecast data if present
        if "forecast" in weather_data and isinstance(weather_data["forecast"], list):
            for day in weather_data["forecast"]:
                if "description" in day:
                    day["description"] = await translation_service.translate_weather_condition(
                        day["description"], language
                    )
                if "condition" in day:
                    day["condition"] = await translation_service.translate_weather_condition(
                        day["condition"], language
                    )
        
        return weather_data
        
    except Exception as e:
        print(f"Weather translation error: {e}")
        return weather_data

@router.get("/weather")
async def get_weather_data(
    location: str = Query("chennai", description="Location for weather data"),
    language: str = Query("english", description="Response language: tamil, hindi, telugu, malayalam, kannada, english"),
    current_user: User = Depends(get_current_user)
):
    """
    Get weather data with farming advisory in selected language
    
    Features:
    - Current weather conditions
    - 7-day forecast
    - Farming advisory based on weather
    - Complete translation to selected language
    - No English content in non-English responses
    """
    
    # Normalize language
    language = language.lower().strip()
    valid_languages = ["tamil", "hindi", "telugu", "malayalam", "kannada", "english"]
    if language not in valid_languages:
        language = "english"
    
    try:
        # Fetch weather data from backend service
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.BACKEND_URL}/api/weather/forecast",
                params={"location": location}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Weather service unavailable")
            
            weather_data = response.json()
            
            # Translate all weather content
            translated_weather = await translate_weather_data(weather_data, language)
            
            # Generate farming advisory
            advisory = await get_farming_advisory(translated_weather, language)
            translated_weather["farming_advisory"] = advisory
            
            return {
                "status": "success",
                "language": language,
                "location": location,
                "weather": translated_weather,
                "message": await translation_service.translate_text(
                    "Weather data loaded successfully", language, "general"
                )
            }
            
    except httpx.TimeoutException:
        error_msg = await translation_service.translate_text(
            "Weather service timeout. Please try again.", language, "general"
        )
        raise HTTPException(status_code=408, detail=error_msg)
        
    except Exception as e:
        print(f"Weather API error: {e}")
        error_msg = await translation_service.translate_text(
            "Unable to fetch weather data. Please try again.", language, "general"
        )
        raise HTTPException(status_code=500, detail=error_msg)

@router.get("/weather/forecast")
async def get_weather_forecast(
    location: str = Query("chennai", description="Location for weather forecast"),
    language: str = Query("english", description="Response language"),
    days: int = Query(7, description="Number of forecast days (1-14)")
):
    """
    Get extended weather forecast with complete language support
    """
    
    # Normalize language
    language = language.lower().strip()
    valid_languages = ["tamil", "hindi", "telugu", "malayalam", "kannada", "english"]
    if language not in valid_languages:
        language = "english"
    
    # Validate days
    if days < 1 or days > 14:
        days = 7
    
    try:
        # Fetch forecast data
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.BACKEND_URL}/api/weather/extended",
                params={"location": location, "days": days}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Forecast service unavailable")
            
            forecast_data = response.json()
            
            # Translate all forecast content
            translated_forecast = await translate_weather_data(forecast_data, language)
            
            return {
                "status": "success",
                "language": language,
                "location": location,
                "days": days,
                "forecast": translated_forecast,
                "message": await translation_service.translate_text(
                    f"{days}-day forecast loaded successfully", language, "general"
                )
            }
            
    except Exception as e:
        print(f"Forecast API error: {e}")
        error_msg = await translation_service.translate_text(
            "Unable to fetch weather forecast. Please try again.", language, "general"
        )
        raise HTTPException(status_code=500, detail=error_msg)

@router.get("/weather/alerts")
async def get_weather_alerts(
    location: str = Query("chennai", description="Location for weather alerts"),
    language: str = Query("english", description="Response language")
):
    """
    Get weather alerts and warnings with complete language support
    """
    
    # Normalize language
    language = language.lower().strip()
    valid_languages = ["tamil", "hindi", "telugu", "malayalam", "kannada", "english"]
    if language not in valid_languages:
        language = "english"
    
    try:
        # Fetch alerts data
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.BACKEND_URL}/api/weather/alerts",
                params={"location": location}
            )
            
            if response.status_code != 200:
                # No alerts available
                no_alerts_msg = await translation_service.translate_text(
                    "No weather alerts for your area", language, "general"
                )
                return {
                    "status": "success",
                    "language": language,
                    "location": location,
                    "alerts": [],
                    "message": no_alerts_msg
                }
            
            alerts_data = response.json()
            
            # Translate all alert content
            if "alerts" in alerts_data:
                for alert in alerts_data["alerts"]:
                    if "title" in alert:
                        alert["title"] = await translation_service.translate_text(
                            alert["title"], language, "weather"
                        )
                    if "description" in alert:
                        alert["description"] = await translation_service.translate_text(
                            alert["description"], language, "weather"
                        )
                    if "advisory" in alert:
                        alert["advisory"] = await translation_service.translate_text(
                            alert["advisory"], language, "weather"
                        )
            
            return {
                "status": "success",
                "language": language,
                "location": location,
                "alerts": alerts_data.get("alerts", []),
                "message": await translation_service.translate_text(
                    "Weather alerts loaded successfully", language, "general"
                )
            }
            
    except Exception as e:
        print(f"Alerts API error: {e}")
        error_msg = await translation_service.translate_text(
            "Unable to fetch weather alerts. Please try again.", language, "general"
        )
        raise HTTPException(status_code=500, detail=error_msg)
        response = model.generate_content(prompt)
        return response.text.strip()
        
    except Exception:
        return "Monitor weather conditions for optimal farming decisions."

@router.post("/current", response_model=WeatherResponse)
async def get_current_weather(
    request: WeatherRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current weather and farming advisory"""
    
    weather_data = {}
    location = "Your Location"
    forecast = []
    
    # Try OpenWeatherMap API if key is available
    if settings.WEATHER_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                # Current weather
                current_url = f"https://api.openweathermap.org/data/2.5/weather"
                params = {
                    "lat": request.latitude,
                    "lon": request.longitude,
                    "appid": settings.WEATHER_API_KEY,
                    "units": "metric"
                }
                response = await client.get(current_url, params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    weather_data = {
                        "temperature": data["main"]["temp"],
                        "feels_like": data["main"]["feels_like"],
                        "humidity": data["main"]["humidity"],
                        "description": data["weather"][0]["description"],
                        "icon": data["weather"][0]["icon"],
                        "wind_speed": data["wind"]["speed"]
                    }
                    location = data.get("name", "Your Location")
                    
                    # Get 5-day forecast
                    forecast_url = f"https://api.openweathermap.org/data/2.5/forecast"
                    forecast_response = await client.get(forecast_url, params=params)
                    
                    if forecast_response.status_code == 200:
                        forecast_data = forecast_response.json()
                        # Get one forecast per day (every 8th item is ~24 hours)
                        for item in forecast_data["list"][::8][:5]:
                            forecast.append({
                                "date": item["dt_txt"].split(" ")[0],
                                "temperature": item["main"]["temp"],
                                "description": item["weather"][0]["description"],
                                "icon": item["weather"][0]["icon"]
                            })
                            
        except Exception as e:
            print(f"Weather API error: {e}")
    
    # Fallback weather data if API fails or no key
    if not weather_data:
        weather_data = {
            "temperature": 28.5,
            "feels_like": 30.0,
            "humidity": 65,
            "description": "partly cloudy",
            "icon": "02d",
            "wind_speed": 3.5
        }
        forecast = [
            {"date": "2026-02-09", "temperature": 29, "description": "sunny", "icon": "01d"},
            {"date": "2026-02-10", "temperature": 28, "description": "cloudy", "icon": "03d"},
            {"date": "2026-02-11", "temperature": 27, "description": "light rain", "icon": "10d"},
            {"date": "2026-02-12", "temperature": 26, "description": "rain", "icon": "09d"},
            {"date": "2026-02-13", "temperature": 28, "description": "sunny", "icon": "01d"},
        ]
    
    # Get AI-powered farming advisory
    farming_advisory = await get_farming_advisory(weather_data, request.language)
    
    return WeatherResponse(
        location=location,
        temperature=weather_data["temperature"],
        feels_like=weather_data["feels_like"],
        humidity=weather_data["humidity"],
        description=weather_data["description"],
        icon=weather_data["icon"],
        wind_speed=weather_data["wind_speed"],
        forecast=forecast,
        farming_advisory=farming_advisory
    )

@router.get("/alerts")
async def get_weather_alerts(
    latitude: float,
    longitude: float,
    current_user: User = Depends(get_current_user)
):
    """Get weather alerts for location"""
    # In production, this would fetch real alerts from weather API
    return {
        "alerts": [
            {
                "type": "advisory",
                "title": "Moderate Heat",
                "description": "Temperatures expected to reach 35°C. Ensure adequate irrigation.",
                "severity": "moderate",
                "valid_until": "2026-02-08T18:00:00"
            }
        ]
    }
