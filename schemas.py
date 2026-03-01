"""
Pydantic Schemas for Request/Response Validation
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
import re

# ============== Auth Schemas ==============

class UserRegister(BaseModel):
    phone: str = Field(..., min_length=10, max_length=15)
    name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6)
    user_type: str = Field(default="farmer")
    language: str = Field(default="english")
    location: Optional[str] = None
    crop_type: Optional[str] = None
    district: Optional[str] = None
    soil_type: Optional[str] = None
    state: Optional[str] = None
    experience: Optional[str] = None
    farming_type: Optional[str] = None
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        # Remove any spaces or special characters
        cleaned = re.sub(r'[^0-9]', '', v)
        if len(cleaned) != 10:
            raise ValueError('Phone number must be exactly 10 digits')
        return cleaned
    
    @field_validator('user_type')
    @classmethod
    def validate_user_type(cls, v):
        if v not in ['farmer', 'public']:
            raise ValueError('User type must be either farmer or public')
        return v

class UserLogin(BaseModel):
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=1)
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        cleaned = re.sub(r'[^0-9]', '', v)
        if len(cleaned) != 10:
            raise ValueError('Phone number must be exactly 10 digits')
        return cleaned

class UserResponse(BaseModel):
    id: int
    phone: str
    name: str
    user_type: str
    language: str
    location: Optional[str]
    crop_type: Optional[str] = None
    district: Optional[str] = None
    soil_type: Optional[str] = None
    state: Optional[str] = None
    experience: Optional[str] = None
    farming_type: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

# ============== Chat Schemas ==============

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    language: str = Field(default="english")
    image_base64: Optional[str] = None

class ChatResponse(BaseModel):
    id: str
    message: str
    response: str
    language: str
    timestamp: datetime

# ============== Disease Prediction Schemas ==============

class DiseaseRequest(BaseModel):
    image_base64: str = Field(..., description="Base64 encoded image")
    crop_name: Optional[str] = None
    language: str = Field(default="english")

class DiseaseResponse(BaseModel):
    disease_name: str
    confidence: float
    description: str
    symptoms: List[str]
    treatment: List[str]
    prevention: List[str]
    organic_solutions: List[str]

# ============== Weather Schemas ==============

class WeatherRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    language: str = Field(default="english")

class WeatherResponse(BaseModel):
    location: str
    temperature: float
    feels_like: float
    humidity: int
    description: str
    icon: str
    wind_speed: float
    forecast: List[dict]
    farming_advisory: str

# ============== Market Schemas ==============

class MarketRequest(BaseModel):
    crop_name: str = Field(..., min_length=1)
    state: Optional[str] = None
    district: Optional[str] = None
    language: str = Field(default="english")

class MarketPriceItem(BaseModel):
    market: str
    min_price: float
    max_price: float
    modal_price: float
    arrival_date: str

class MarketResponse(BaseModel):
    crop_name: str
    prices: List[MarketPriceItem]
    trend: str
    recommendation: str

# ============== Crop Recommendation Schemas ==============

class CropRecommendationRequest(BaseModel):
    soil_type: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    season: str = Field(..., min_length=1)
    water_availability: Optional[str] = "moderate"
    budget: Optional[str] = "medium"
    language: str = Field(default="english")

class CropItem(BaseModel):
    name: str
    suitability_score: float
    expected_yield: str
    water_requirement: str
    growth_duration: str
    market_demand: str
    tips: List[str]

class CropRecommendationResponse(BaseModel):
    recommended_crops: List[CropItem]
    soil_health_tips: List[str]
    seasonal_advice: str

# ============== Phone Call Schemas ==============

class CallRequest(BaseModel):
    query: str = Field(..., min_length=1)
    language: str = Field(default="english")
    audio_base64: Optional[str] = None

class CallResponse(BaseModel):
    response_text: str
    audio_base64: Optional[str] = None
    language: str
