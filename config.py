"""
Application Configuration
"""

from pydantic_settings import BaseSettings
from functools import lru_cache
import os

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "Uzhavan AI"
    DEBUG: bool = True
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./uzhavan.db"
    
    # JWT Settings
    SECRET_KEY: str = "your-super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Google Gemini API
    GEMINI_API_KEY: str = ""
    
    # Weather API (OpenWeatherMap - free tier)
    WEATHER_API_KEY: str = ""
    
    # Market Data API
    MARKET_API_URL: str = "https://api.data.gov.in/resource"
    MARKET_API_KEY: str = ""
    
    # Data.gov.in Mandi Prices API (Agmarknet)
    DATA_GOV_API_KEY: str = os.getenv("DATA_GOV_API_KEY", "579b464db66ec23bdd0000016df62ee19e7742936caec0eec2b2cab3")
    DATA_GOV_RESOURCE_ID: str = "9ef84268-d588-465a-a308-a864a43d0070"
    DATA_GOV_BASE_URL: str = "https://api.data.gov.in/resource"
    
    # Prices Database
    PRICES_DATABASE_URL: str = "sqlite+aiosqlite:///./prices.db"
    
    class Config:
        env_file = ".env"
        extra = "allow"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
