"""
Database Configuration and Models
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Float
from datetime import datetime
from config import settings


# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG
)

# Create async session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for models
class Base(DeclarativeBase):
    pass

# User Model
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    firebase_uid = Column(String(128), unique=True, index=True, nullable=True)  # Firebase User ID
    phone = Column(String(15), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=True)  # Optional with Firebase
    user_type = Column(String(20), default="farmer")  # farmer, public, buyer
    language = Column(String(20), default="tamil")
    location = Column(String(200), nullable=True)  # இடம் (Place)
    crop_type = Column(String(100), nullable=True)  # பயிர் வகை (Work Type)
    district = Column(String(100), nullable=True)   # நிலப்பரப்பு (District)
    soil_type = Column(String(100), nullable=True)  # மண் வகை (State/Soil)
    state = Column(String(100), nullable=True)      # மாநிலம் (State)
    experience = Column(String(100), nullable=True) # அனுபவம் (Experience)
    farming_type = Column(String(100), nullable=True) # விவசாய வகை (Farming Type)
    aadhaar = Column(String(20), nullable=True)  # Aadhaar number (stored encrypted ideally)
    upi_id = Column(String(100), nullable=True)  # UPI ID for payments
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)  # Phone verified via Firebase OTP

# Chat History Model
class ChatHistory(Base):
    __tablename__ = "chat_history"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    response = Column(Text, nullable=False)
    language = Column(String(20), default="english")
    created_at = Column(DateTime, default=datetime.utcnow)

# Disease Prediction History
class DiseasePrediction(Base):
    __tablename__ = "disease_predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    image_path = Column(String(500), nullable=True)
    crop_name = Column(String(100), nullable=True)
    disease_name = Column(String(200), nullable=True)
    confidence = Column(Float, nullable=True)
    recommendation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Crop Recommendation History
class CropRecommendationHistory(Base):
    __tablename__ = "crop_recommendations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    soil_type = Column(String(100), nullable=True)
    location = Column(String(200), nullable=True)
    season = Column(String(50), nullable=True)
    recommended_crops = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Mandi Price Model - Stores daily mandi prices from data.gov.in
class MandiPrice(Base):
    __tablename__ = "mandi_prices"
    
    id = Column(Integer, primary_key=True, index=True)
    arrival_date = Column(DateTime, nullable=False, index=True)  # Date of price arrival
    state = Column(String(100), nullable=False, index=True)
    district = Column(String(100), nullable=False, index=True)
    market = Column(String(200), nullable=False, index=True)
    commodity = Column(String(200), nullable=False, index=True)
    variety = Column(String(200), nullable=True)
    grade = Column(String(100), nullable=True)
    min_price = Column(Float, nullable=False)  # Minimum price in Rs/Quintal
    max_price = Column(Float, nullable=False)  # Maximum price in Rs/Quintal
    modal_price = Column(Float, nullable=False)  # Most common price
    commodity_code = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Price Prediction Model - Stores predicted prices
class PricePrediction(Base):
    __tablename__ = "price_predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    prediction_date = Column(DateTime, nullable=False, index=True)  # Date for which prediction is made
    commodity = Column(String(200), nullable=False, index=True)
    state = Column(String(100), nullable=True, index=True)
    district = Column(String(100), nullable=True, index=True)
    market = Column(String(200), nullable=True, index=True)
    predicted_price = Column(Float, nullable=False)  # Predicted modal price
    prediction_method = Column(String(100), nullable=False)  # e.g., "moving_average", "trend"
    confidence_score = Column(Float, nullable=True)  # 0-1 confidence score
    days_ahead = Column(Integer, default=1)  # How many days ahead this prediction is for
    base_price = Column(Float, nullable=True)  # The price used as base for prediction
    historical_data_points = Column(Integer, nullable=True)  # Number of data points used
    created_at = Column(DateTime, default=datetime.utcnow)

# Fetch Log Model - Tracks API fetch operations
class PriceFetchLog(Base):
    __tablename__ = "price_fetch_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    fetch_date = Column(DateTime, nullable=False, index=True)
    records_fetched = Column(Integer, default=0)
    records_inserted = Column(Integer, default=0)
    status = Column(String(50), nullable=False)  # success, partial, failed
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Weather Cache Model
class WeatherCache(Base):
    __tablename__ = "weather_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String(200), unique=True, index=True, nullable=False) # Normalized location name
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    data = Column(Text, nullable=False) # JSON stored as text
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

# Call History Model - Stores voice call recordings and transcripts
class CallHistory(Base):
    __tablename__ = "call_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(128), nullable=False, index=True)  # Firebase UID
    language = Column(String(20), default="tamil")
    transcript = Column(Text, nullable=True)
    audio_path = Column(String(500), nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    duration_seconds = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

# Database initialization
async def init_db():
    # Import WhatsApp models here so they register with Base before create_all
    from models.whatsapp_models import WhatsAppAlertLog, WhatsAppJobRun, WhatsAppOptOut  # noqa

    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))

# Dependency for getting database session
async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
