"""
Weather Service - Handles fetching and caching weather data from Open-Meteo
Implements Service Layer pattern for clean separation of concerns.

Features:
- Fetches weather from Open-Meteo API
- Caches responses using CacheService (SQLite + Memory)
- Background prefetch logic for scheduler
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio

from services.http_client import http_client, TimeoutConfig, HTTPClientError
from services.cache_service import cache_manager, CacheConfig

logger = logging.getLogger(__name__)

# Major Indian cities/districts for prefetching
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
    
    # Karnataka
    "bangalore": {"lat": 12.9716, "lon": 77.5946},
    "mysore": {"lat": 12.2958, "lon": 76.6394},
    "hubli": {"lat": 15.3647, "lon": 75.1240},
    
    # Andhra Pradesh & Telangana
    "hyderabad": {"lat": 17.3850, "lon": 78.4867},
    "vijayawada": {"lat": 16.5062, "lon": 80.6480},
    "visakhapatnam": {"lat": 17.6868, "lon": 83.2185},
    
    # Kerala
    "kochi": {"lat": 9.9312, "lon": 76.2673},
    "trivandrum": {"lat": 8.5241, "lon": 76.9366},
    
    # Maharashtra
    "mumbai": {"lat": 19.0760, "lon": 72.8777},
    "pune": {"lat": 18.5204, "lon": 73.8567},
    
    # North India
    "delhi": {"lat": 28.7041, "lon": 77.1025},
    
    # Default
    "india": {"lat": 20.5937, "lon": 78.9629},
}

class WeatherService:
    def __init__(self):
        self.base_url = "https://api.open-meteo.com/v1/forecast"

    def get_coordinates(self, location: str) -> tuple[float, float]:
        """Get coordinates for a location"""
        location_lower = location.lower().strip()
        
        # Exact match
        if location_lower in INDIA_LOCATIONS:
            coords = INDIA_LOCATIONS[location_lower]
            return coords["lat"], coords["lon"]
        
        # Partial match
        for city, coords in INDIA_LOCATIONS.items():
            if city in location_lower or location_lower in city:
                return coords["lat"], coords["lon"]
        
        # Default
        return 20.5937, 78.9629

    async def fetch_weather_data(self, lat: float, lon: float) -> Dict[str, Any]:
        """Fetch raw weather data from Open-Meteo"""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,rain,windspeed_10m,cloudcover",
            "daily": "precipitation_sum,rain_sum,temperature_2m_max,temperature_2m_min,weathercode",
            "current_weather": True,
            "timezone": "Asia/Kolkata",
            "forecast_days": 7
        }
        
        try:
            response = await http_client.get(
                self.base_url,
                params=params,
                service_name="open_meteo",
                timeout=TimeoutConfig.FAST,
                max_retries=2
            )
            return response.json()
        except HTTPClientError as e:
            logger.error(f"Failed to fetch weather data: {e}")
            raise

    async def get_forecast(self, location: str) -> Dict[str, Any]:
        """
        Get weather forecast for a location.
        Uses cache first, then API.
        """
        location_key = location.lower().strip()
        cache_key = f"weather:forecast:{location_key}"
        
        # 1. Try Cache
        cached_data, is_stale = await cache_manager.get(cache_key)
        if cached_data:
            if not is_stale:
                return cached_data
            
        # 2. Fetch from API
        try:
            lat, lon = self.get_coordinates(location)
            data = await self.fetch_weather_data(lat, lon)
            
            result = {
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "raw_data": data,
                "fetched_at": datetime.now().isoformat()
            }
            
            # 3. Update Cache
            await cache_manager.set(
                cache_key,
                result,
                CacheConfig.WEATHER_FORECAST,
                category="weather"
            )
            
            return result
        except Exception:
            # 4. Fallback to Stale Cache
            if cached_data:
                logger.warning(f"Using stale weather data for {location}")
                return cached_data
            else:
                # No data available
                return {
                    "location": location,
                    "error": "Weather data unavailable",
                    "raw_data": {} 
                }

    async def get_forecast_by_coords(self, lat: float, lon: float, lang: str = 'en') -> Dict[str, Any]:
        """
        Fetch weather by coordinates (for Dashboard).
        Uses cache first, then API.
        Returns PROCESSED data (not raw).
        """
        # Round coordinates for better cache hit rate
        lat_r = round(lat, 2)
        lon_r = round(lon, 2)
        cache_key = f"weather:coords:{lat_r}:{lon_r}:{lang}"
        
        # 1. Try Cache
        cached_data, is_stale = await cache_manager.get(cache_key)
        if cached_data:
            if not is_stale:
                return cached_data
            
        try:
            # 2. Fetch from API
            raw_data = await self.fetch_weather_data(lat, lon)
            
            # 3. Process Data
            processed_data = self._process_weather_data(lat, lon, raw_data)
            
            # 3.1 Translate if language is not English
            if lang != 'en':
                try:
                    from routers.agriculture_news import translate_text
                    tasks = [
                        translate_text(processed_data['current']['weather_description'], lang),
                        translate_text(processed_data['farming_advisory'], lang),
                        translate_text(processed_data['rain_alert'].get('alert_message', ''), lang)
                    ]
                    for day in processed_data['daily_forecast']:
                        tasks.append(translate_text(day['weather_description'], lang))
                        
                    import asyncio
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    if not isinstance(results[0], Exception):
                        processed_data['current']['weather_description'] = results[0]
                    if not isinstance(results[1], Exception):
                        processed_data['farming_advisory'] = results[1]
                    if not isinstance(results[2], Exception) and results[2]:
                        processed_data['rain_alert']['alert_message'] = results[2]
                        
                    for i, day in enumerate(processed_data['daily_forecast']):
                        if not isinstance(results[i+3], Exception):
                            day['weather_description'] = results[i+3]
                except Exception as ex:
                    logger.error(f"Weather translation failed: {ex}")
            
            # 4. Update Cache
            await cache_manager.set(
                cache_key,
                processed_data,
                CacheConfig.WEATHER_FORECAST,
                category="weather"
            )
            
            return processed_data
            
        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            # 5. Fallback to Stale
            if cached_data:
                logger.warning(f"Using stale weather data for {lat},{lon}")
                return cached_data
            raise

    def _process_weather_data(self, lat: float, lon: float, data: dict) -> dict:
        """Process raw Open-Meteo data into frontend-friendly format"""
        # Get current hour index
        current_hour = datetime.now().hour
        
        current_weather = data.get("current_weather", {})
        hourly = data.get("hourly", {})
        daily = data.get("daily", {})
        
        # Current Weather
        current = {
            "temperature": current_weather.get("temperature", 0),
            "humidity": hourly.get("relative_humidity_2m", [0]*24)[current_hour] if hourly.get("relative_humidity_2m") else 0,
            "windspeed": current_weather.get("windspeed", 0),
            "cloudcover": hourly.get("cloudcover", [0])[current_hour] if hourly.get("cloudcover") else 0,
            "precipitation": hourly.get("precipitation", [0])[current_hour] if hourly.get("precipitation") else 0,
            "weather_description": self._get_weather_description(current_weather.get("weathercode", 0))
        }
        
        # Daily Forecast
        daily_forecast = []
        dates = daily.get("time", [])
        temp_max = daily.get("temperature_2m_max", [])
        temp_min = daily.get("temperature_2m_min", [])
        precip_sum = daily.get("precipitation_sum", [])
        rain_sum = daily.get("rain_sum", [])
        weather_codes = daily.get("weathercode", [])
        
        for i in range(min(7, len(dates))):
            daily_forecast.append({
                "date": dates[i],
                "temp_max": temp_max[i] if i < len(temp_max) else 0,
                "temp_min": temp_min[i] if i < len(temp_min) else 0,
                "precipitation_sum": precip_sum[i] if i < len(precip_sum) else 0,
                "rain_sum": rain_sum[i] if i < len(rain_sum) else 0,
                "weather_description": self._get_weather_description(weather_codes[i]) if i < len(weather_codes) else "Unknown"
            })
            
        # Rain Alert
        rain_alert = self._calculate_rain_alert(daily_forecast)
        
        # Farming Advisory
        advisory = self._generate_farming_advisory(current, rain_alert)
        
        return {
            "location": f"{lat},{lon}",
            "latitude": lat,
            "longitude": lon,
            "current": current,
            "daily_forecast": daily_forecast,
            "rain_alert": rain_alert,
            "farming_advisory": advisory,
            "last_updated": datetime.now().isoformat(),
            "raw_data": data # Keep raw data just in case
        }

    def _get_weather_description(self, code: int) -> str:
        """Convert WMO weather code to description"""
        codes = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
        }
        return codes.get(code, "Unknown")

    def _calculate_rain_alert(self, daily_forecast: List[dict]) -> dict:
        """Calculate 3-day rain alert"""
        rain_days = []
        total_rain = 0
        
        # Check days 1, 2, 3 (skip today index 0)
        for i, day in enumerate(daily_forecast[1:4], start=1):
            rain = day.get("rain_sum", 0) or day.get("precipitation_sum", 0) or 0
            if rain > 0:
                rain_days.append({
                    "day": i,
                    "date": day["date"],
                    "rain_mm": rain
                })
                total_rain += rain
        
        if total_rain == 0:
            return {"has_alert": False, "alert_level": "none", "alert_message": "No rain expected.", "rain_days": []}
        elif total_rain < 5:
            return {"has_alert": True, "alert_level": "light", "alert_message": f"Light rain ({total_rain:.1f}mm).", "rain_days": rain_days}
        elif total_rain < 20:
            return {"has_alert": True, "alert_level": "moderate", "alert_message": f"Moderate rain ({total_rain:.1f}mm).", "rain_days": rain_days}
        else:
            return {"has_alert": True, "alert_level": "heavy", "alert_message": f"Heavy rain alert ({total_rain:.1f}mm)!", "rain_days": rain_days}

    def _generate_farming_advisory(self, current: dict, alert: dict) -> str:
        """Generate simple advisory"""
        advisories = []
        if alert["has_alert"]:
            if alert["alert_level"] == "heavy":
                advisories.append("Heavy rain alert: Secure crops and ensure drainage.")
            elif alert["alert_level"] == "moderate":
                advisories.append("Rain expected: Delay spraying.")
        else:
            advisories.append("Good weather for field activities.")
            
        temp = current.get("temperature", 0)
        if temp > 35:
            advisories.append("High heat: Irrigate frequently.")
            
        return " ".join(advisories)

    async def prefetch_all_locations(self):
        """
        Background task to fetch weather for all configured locations.
        Called by scheduler.
        """
        logger.info("🔁 Starting background weather prefetch...")
        count = 0
        
        # Process in chunks to avoid rate limits
        locations = list(INDIA_LOCATIONS.keys())
        chunk_size = 5
        
        for i in range(0, len(locations), chunk_size):
            chunk = locations[i:i+chunk_size]
            tasks = []
            
            for loc in chunk:
                tasks.append(self.get_forecast(loc))
            
            # Run tasks in parallel, ignore exceptions to not stop the loop
            await asyncio.gather(*tasks, return_exceptions=True)
            count += len(chunk)
            await asyncio.sleep(1) # Polite delay
            
        logger.info(f"✅ Prefetched weather for {count} locations")

weather_service = WeatherService()
