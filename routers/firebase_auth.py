"""
Firebase Authentication Router
Handles user authentication with Firebase tokens
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
import re

from database import get_db, User
from firebase_admin_config import (
    get_current_firebase_user, 
    FirebaseUser,
    get_user_by_phone
)

router = APIRouter()

# ============== Schemas ==============

class UserProfileUpdate(BaseModel):
    """Schema for updating user profile"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    user_type: Optional[str] = None
    language: Optional[str] = None
    location: Optional[str] = None
    
    class Config:
        extra = "forbid"

class UserProfileResponse(BaseModel):
    """Response schema for user profile"""
    id: int
    firebase_uid: str
    phone: str
    name: str
    user_type: str
    language: str
    location: Optional[str] = None
    crop_type: Optional[str] = None   # பயிர் வகை
    district: Optional[str] = None    # நிலப்பரப்பு
    soil_type: Optional[str] = None   # மண் வகை
    state: Optional[str] = None
    experience: Optional[str] = None
    farming_type: Optional[str] = None
    aadhaar: Optional[str] = None
    upi_id: Optional[str] = None
    created_at: datetime
    is_verified: bool
    
    class Config:
        from_attributes = True

class RegisterRequest(BaseModel):
    """Request schema for completing registration after Firebase OTP verification"""
    name: str = Field(..., min_length=2, max_length=100, description="பயனர் பெயர்")
    user_type: str = Field(default="farmer")
    language: str = Field(default="tamil")
    location: Optional[str] = Field(None, description="இடம் (Place)")
    crop_type: Optional[str] = Field(None, description="பயிர் வகை (Work Type)")
    district: Optional[str] = Field(None, description="நிலப்பரப்பு (District)")
    soil_type: Optional[str] = Field(None, description="மண் வகை (State/Soil)")
    state: Optional[str] = Field(None)
    experience: Optional[str] = Field(None)
    farming_type: Optional[str] = Field(None)
    password: str = Field(..., min_length=6, description="கடவுச்சொல்")
    confirm_password: str = Field(..., min_length=6, description="கடவுச்சொல்லை உறுதிப்படுத்தவும்")
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('கடவுச்சொல் குறைந்தது 6 எழுத்துக்கள் இருக்க வேண்டும்')  # Password must be at least 6 characters
        return v
    
    @model_validator(mode='after')
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('கடவுச்சொற்கள் பொருந்தவில்லை')  # Passwords do not match
        return self
    
    class Config:
        extra = "forbid"

# ============== Endpoints ==============

@router.post("/register", response_model=UserProfileResponse)
async def complete_registration(
    request: RegisterRequest,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Complete user registration after Firebase OTP verification.
    
    Flow:
    1. User verifies phone via Firebase OTP (frontend)
    2. Frontend gets Firebase ID token
    3. Frontend calls this endpoint with token + profile data
    4. Backend verifies token and creates user record
    """
    
    # Extract phone number from Firebase token
    if not firebase_user.phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number not found in Firebase token. Please verify with OTP first."
        )
    
    # Clean phone number (remove +91 or any country code for storage)
    phone = firebase_user.phone_number
    if phone.startswith("+91"):
        phone = phone[3:]
    phone = re.sub(r'[^0-9]', '', phone)
    
    # Check if user already exists
    result = await db.execute(
        select(User).where(User.firebase_uid == firebase_user.uid)
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already registered. Please login instead."
        )
    
    # Also check by phone number
    result = await db.execute(
        select(User).where(User.phone == phone)
    )
    existing_phone = result.scalar_one_or_none()
    
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="இந்த தொலைபேசி எண் ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது."  # Phone already registered
        )
    
    # Hash password for future login (import at top for performance)
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed_password = pwd_context.hash(request.password)
    
    # Create new user with ALL profile fields - Single DB Write
    new_user = User(
        firebase_uid=firebase_user.uid,
        phone=phone,
        name=request.name,
        password_hash=hashed_password,
        user_type=request.user_type,
        language=request.language,
        location=request.location,
        crop_type=request.crop_type,      # பயிர் வகை
        district=request.district,        # நிலப்பரப்பு
        soil_type=request.soil_type,      # மண் வகை
        state=request.state,
        experience=request.experience,
        farming_type=request.farming_type,
        is_verified=True  # Verified via Firebase OTP
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Return complete profile
    return UserProfileResponse(
        id=new_user.id,
        firebase_uid=new_user.firebase_uid,
        phone=new_user.phone,
        name=new_user.name,
        user_type=new_user.user_type,
        language=new_user.language,
        location=new_user.location,
        crop_type=new_user.crop_type,
        district=new_user.district,
        soil_type=new_user.soil_type,
        state=new_user.state,
        experience=new_user.experience,
        farming_type=new_user.farming_type,
        created_at=new_user.created_at,
        is_verified=new_user.is_verified
    )

@router.post("/login", response_model=UserProfileResponse)
async def login(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Login with Firebase token.
    
    Flow:
    1. User enters phone + password in frontend
    2. Frontend authenticates with Firebase
    3. Frontend gets Firebase ID token
    4. Frontend calls this endpoint with token
    5. Backend verifies token and returns user data
    """
    
    # Find user by Firebase UID
    result = await db.execute(
        select(User).where(User.firebase_uid == firebase_user.uid)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # Try to find by phone number (migration case)
        if firebase_user.phone_number:
            phone = firebase_user.phone_number
            if phone.startswith("+91"):
                phone = phone[3:]
            phone = re.sub(r'[^0-9]', '', phone)
            
            result = await db.execute(
                select(User).where(User.phone == phone)
            )
            user = result.scalar_one_or_none()
            
            if user:
                # Update Firebase UID for existing user
                user.firebase_uid = firebase_user.uid
                user.is_verified = True
                await db.commit()
                await db.refresh(user)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please register first."
        )
    
    return UserProfileResponse(
        id=user.id,
        firebase_uid=user.firebase_uid,
        phone=user.phone,
        name=user.name,
        user_type=user.user_type,
        language=user.language,
        location=user.location,
        crop_type=user.crop_type,
        district=user.district,
        soil_type=user.soil_type,
        state=user.state,
        experience=user.experience,
        farming_type=user.farming_type,
        created_at=user.created_at,
        is_verified=user.is_verified
    )

@router.get("/me", response_model=UserProfileResponse)
async def get_current_user(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user profile"""
    
    result = await db.execute(
        select(User).where(User.firebase_uid == firebase_user.uid)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found. Please complete registration."
        )
    
    return UserProfileResponse(
        id=user.id,
        firebase_uid=user.firebase_uid,
        phone=user.phone,
        name=user.name,
        user_type=user.user_type,
        language=user.language,
        location=user.location,
        crop_type=user.crop_type,
        district=user.district,
        soil_type=user.soil_type,
        state=user.state,
        experience=user.experience,
        farming_type=user.farming_type,
        aadhaar=user.aadhaar,
        upi_id=user.upi_id,
        created_at=user.created_at,
        is_verified=user.is_verified
    )

@router.put("/me", response_model=UserProfileResponse)
async def update_profile(
    updates: UserProfileUpdate,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db)
):
    """Update current user profile"""
    
    result = await db.execute(
        select(User).where(User.firebase_uid == firebase_user.uid)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found."
        )
    
    # Update fields if provided
    if updates.name is not None:
        user.name = updates.name
    if updates.user_type is not None:
        if updates.user_type not in ['farmer', 'public', 'buyer']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user type. Must be 'farmer', 'public', or 'buyer'."
            )
        user.user_type = updates.user_type
    if updates.language is not None:
        user.language = updates.language
    if updates.location is not None:
        user.location = updates.location
    
    await db.commit()
    await db.refresh(user)
    
    return UserProfileResponse(
        id=user.id,
        firebase_uid=user.firebase_uid,
        phone=user.phone,
        name=user.name,
        user_type=user.user_type,
        language=user.language,
        location=user.location,
        crop_type=user.crop_type,
        district=user.district,
        soil_type=user.soil_type,
        state=user.state,
        experience=user.experience,
        farming_type=user.farming_type,
        created_at=user.created_at,
        is_verified=user.is_verified
    )

@router.post("/verify-token")
async def verify_token(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """
    Verify Firebase token and return user info.
    Used for checking if token is valid.
    """
    return {
        "valid": True,
        "uid": firebase_user.uid,
        "phone_number": firebase_user.phone_number,
        "email": firebase_user.email
    }

@router.post("/logout")
async def logout():
    """
    Logout endpoint.
    Note: Firebase token invalidation happens on the client side.
    This endpoint is for any server-side cleanup if needed.
    """
    return {
        "message": "Logged out successfully. Please clear the token on client side."
    }
