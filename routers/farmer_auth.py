"""
Firebase Auth Router with Firestore Integration
Handles user registration and profile management using Firebase Firestore

Optimized for:
- Fast registration (single Firestore write)
- Proper Firebase UID mapping
- Tamil-friendly error messages
- Timeout handling
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
from datetime import datetime
import re
import asyncio

from firebase_admin_config import (
    get_current_firebase_user,
    FirebaseUser
)
from services.firestore_service import (
    create_farmer_profile,
    get_farmer_profile,
    update_farmer_profile,
    check_farmer_exists,
    check_mobile_exists
)

router = APIRouter()

# ============== TIMEOUT CONFIG ==============
FIRESTORE_TIMEOUT = 10  # seconds - prevent infinite loading

# ============== SCHEMAS ==============

class RegisterRequest(BaseModel):
    """
    Registration request schema
    All fields match the UI form (matching second image design)
    """
    name: str = Field(..., min_length=2, max_length=100, description="User Name")
    location: Optional[str] = Field(None, max_length=200, description="Location")
    crop_type: Optional[str] = Field(None, max_length=100, description="Crop Type")
    district: Optional[str] = Field(None, max_length=100, description="Land Area (stored as district)")
    soil_type: Optional[str] = Field(None, max_length=100, description="Soil Type")
    state: Optional[str] = Field(None, max_length=100, description="State")
    experience: Optional[str] = Field(None, max_length=100, description="Experience")
    farming_type: Optional[str] = Field(None, max_length=100, description="Farming Type")
    password: str = Field(..., min_length=6, description="Password")
    confirm_password: str = Field(..., min_length=6, description="Confirm Password")
    user_type: str = Field(default="farmer")
    language: str = Field(default="tamil")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('பெயர் தேவை')  # Name is required
        return v.strip()
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('கடவுச்சொல் குறைந்தது 6 எழுத்துக்கள் இருக்க வேண்டும்')
        return v
    
    @model_validator(mode='after')
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('கடவுச்சொற்கள் பொருந்தவில்லை')  # Passwords do not match
        return self
    
    class Config:
        extra = "forbid"


class ProfileResponse(BaseModel):
    """Profile response schema"""
    firebase_uid: str
    mobile: str
    name: str
    location: Optional[str] = ""
    crop_type: Optional[str] = ""
    district: Optional[str] = ""
    soil_type: Optional[str] = ""
    state: Optional[str] = ""
    experience: Optional[str] = ""
    farming_type: Optional[str] = ""
    language: str = "tamil"
    user_type: str = "farmer"
    created_at: str
    is_verified: bool = True


class ProfileUpdateRequest(BaseModel):
    """Profile update request"""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    location: Optional[str] = None
    crop_type: Optional[str] = None
    district: Optional[str] = None
    soil_type: Optional[str] = None
    state: Optional[str] = None
    experience: Optional[str] = None
    farming_type: Optional[str] = None
    language: Optional[str] = None
    
    class Config:
        extra = "forbid"


# ============== HELPER FUNCTIONS ==============

def extract_mobile_from_firebase(firebase_user: FirebaseUser) -> str:
    """Extract and clean mobile number from Firebase user"""
    # Try phone_number first (for OTP auth)
    if firebase_user.phone_number:
        phone = firebase_user.phone_number
        if phone.startswith("+91"):
            phone = phone[3:]
        return re.sub(r'[^0-9]', '', phone)
    
    # Try email (for email auth with phone@domain format)
    if firebase_user.email and "@uzhavan.local" in firebase_user.email:
        phone = firebase_user.email.split("@")[0]
        return re.sub(r'[^0-9]', '', phone)
    
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="மொபைல் எண் காணப்படவில்லை"  # Mobile number not found
    )


async def with_timeout(coro, timeout_seconds: int = FIRESTORE_TIMEOUT, error_msg: str = ""):
    """Execute coroutine with timeout to prevent infinite loading"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=error_msg or "சர்வர் மெதுவாக உள்ளது. மீண்டும் முயற்சிக்கவும்"  # Server slow, try again
        )


# ============== ENDPOINTS ==============

@router.post("/register", response_model=ProfileResponse)
async def register_farmer(
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """
    Complete farmer registration after Firebase Auth.
    
    Flow:
    1. User signs up with Firebase (frontend)
    2. Frontend gets Firebase ID token
    3. Frontend calls this endpoint with token + profile data
    4. Backend creates profile in Firestore (single write)
    
    Optimized: Single Firestore write for fast registration
    """
    
    # Extract mobile number
    mobile = extract_mobile_from_firebase(firebase_user)
    
    # Check if already registered (with timeout)
    existing = await with_timeout(
        check_farmer_exists(firebase_user.uid),
        error_msg="பதிவேற்றம் சரிபார்க்க முடியவில்லை"
    )
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது. உள்நுழைக"  # Already registered, please login
        )
    
    # Check if mobile already used
    existing_mobile = await with_timeout(
        check_mobile_exists(mobile),
        error_msg="மொபைல் எண் சரிபார்க்க முடியவில்லை"
    )
    
    if existing_mobile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="இந்த மொபைல் எண் ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது"  # Mobile already registered
        )
    
    # Create profile in Firestore (single write - fast!)
    profile = await with_timeout(
        create_farmer_profile(
            firebase_uid=firebase_user.uid,
            mobile=mobile,
            name=request.name,
            location=request.location,
            crop_type=request.crop_type,
            district=request.district,
            soil_type=request.soil_type,
            state=request.state,
            experience=request.experience,
            farming_type=request.farming_type,
            language=request.language,
            user_type=request.user_type
        ),
        error_msg="பதிவு தோல்வியடைந்தது. மீண்டும் முயற்சிக்கவும்"  # Registration failed
    )
    
    # ─── Send instant WhatsApp welcome message (background) ─────────────
    try:
        from services.whatsapp_welcome import send_welcome_whatsapp
        background_tasks.add_task(
            send_welcome_whatsapp,
            phone=mobile,
            name=request.name,
            crop=request.crop_type or "General",
            district=request.district or "Tamil Nadu",
            language=request.language or "tamil",
        )
    except Exception as e:
        # Don't fail registration if WhatsApp fails
        import logging
        logging.getLogger(__name__).error(f"WhatsApp welcome task failed to schedule: {e}")
    
    return ProfileResponse(
        firebase_uid=profile["firebase_uid"],
        mobile=profile["mobile"],
        name=profile["name"],
        location=profile.get("location", ""),
        crop_type=profile.get("crop_type", ""),
        district=profile.get("district", ""),
        soil_type=profile.get("soil_type", ""),
        state=profile.get("state", ""),
        experience=profile.get("experience", ""),
        farming_type=profile.get("farming_type", ""),
        language=profile.get("language", "tamil"),
        user_type=profile.get("user_type", "farmer"),
        created_at=profile.get("created_at", datetime.utcnow().isoformat()),
        is_verified=True
    )


@router.post("/login", response_model=ProfileResponse)
async def login_farmer(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """
    Login and fetch farmer profile.
    Firebase Auth is already verified via token.
    If profile doesn't exist in Firestore, auto-create from Firebase data.
    """
    
    # Fetch profile from Firestore (with timeout)
    profile = await with_timeout(
        get_farmer_profile(firebase_user.uid),
        error_msg="உள்நுழைவு தோல்வியடைந்தது"  # Login failed
    )
    
    # Auto-create profile if not found (handles interrupted registrations)
    if not profile:
        mobile = ""
        try:
            mobile = extract_mobile_from_firebase(firebase_user)
        except:
            mobile = firebase_user.email.split("@")[0] if firebase_user.email else ""
        
        profile = await with_timeout(
            create_farmer_profile(
                firebase_uid=firebase_user.uid,
                mobile=mobile,
                name=firebase_user.email.split("@")[0] if firebase_user.email else "Farmer",
                location=None,
                crop_type=None,
                district=None,
                soil_type=None,
                state=None,
                experience=None,
                farming_type=None,
                language="tamil",
                user_type="farmer"
            ),
            error_msg="சுயவிவரம் உருவாக்க முடியவில்லை"
        )
    
    return ProfileResponse(
        firebase_uid=profile["firebase_uid"],
        mobile=profile.get("mobile", ""),
        name=profile.get("name", ""),
        location=profile.get("location", ""),
        crop_type=profile.get("crop_type", ""),
        district=profile.get("district", ""),
        soil_type=profile.get("soil_type", ""),
        state=profile.get("state", ""),
        experience=profile.get("experience", ""),
        farming_type=profile.get("farming_type", ""),
        language=profile.get("language", "tamil"),
        user_type=profile.get("user_type", "farmer"),
        created_at=profile.get("created_at", ""),
        is_verified=profile.get("is_verified", True)
    )


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """
    Get current user's profile.
    If profile doesn't exist in Firestore, auto-create from Firebase Auth data.
    This handles the case where registration was interrupted.
    """
    
    profile = await with_timeout(
        get_farmer_profile(firebase_user.uid),
        error_msg="சுயவிவரம் ஏற்றுவதில் தோல்வி"  # Profile load failed
    )
    
    # Auto-create profile if not found
    if not profile:
        mobile = ""
        try:
            mobile = extract_mobile_from_firebase(firebase_user)
        except:
            mobile = firebase_user.email.split("@")[0] if firebase_user.email else ""
        
        profile = await with_timeout(
            create_farmer_profile(
                firebase_uid=firebase_user.uid,
                mobile=mobile,
                name=firebase_user.email.split("@")[0] if firebase_user.email else "Farmer",
                location=None,
                crop_type=None,
                district=None,
                soil_type=None,
                state=None,
                experience=None,
                farming_type=None,
                language="tamil",
                user_type="farmer"
            ),
            error_msg="சுயவிவரம் உருவாக்க முடியவில்லை"
        )
    
    return ProfileResponse(
        firebase_uid=profile["firebase_uid"],
        mobile=profile.get("mobile", ""),
        name=profile.get("name", ""),
        location=profile.get("location", ""),
        crop_type=profile.get("crop_type", ""),
        district=profile.get("district", ""),
        soil_type=profile.get("soil_type", ""),
        state=profile.get("state", ""),
        experience=profile.get("experience", ""),
        farming_type=profile.get("farming_type", ""),
        language=profile.get("language", "tamil"),
        user_type=profile.get("user_type", "farmer"),
        created_at=profile.get("created_at", ""),
        is_verified=profile.get("is_verified", True)
    )


@router.put("/me", response_model=ProfileResponse)
async def update_my_profile(
    updates: ProfileUpdateRequest,
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """Update current user's profile"""
    
    # Build update dict with only provided fields
    update_data = {}
    if updates.name is not None:
        update_data["name"] = updates.name
    if updates.location is not None:
        update_data["location"] = updates.location
    if updates.crop_type is not None:
        update_data["crop_type"] = updates.crop_type
    if updates.district is not None:
        update_data["district"] = updates.district
    if updates.soil_type is not None:
        update_data["soil_type"] = updates.soil_type
    if updates.state is not None:
        update_data["state"] = updates.state
    if updates.experience is not None:
        update_data["experience"] = updates.experience
    if updates.farming_type is not None:
        update_data["farming_type"] = updates.farming_type
    if updates.language is not None:
        update_data["language"] = updates.language
    
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="புதுப்பிக்க தரவு இல்லை"  # No data to update
        )
    
    profile = await with_timeout(
        update_farmer_profile(firebase_user.uid, update_data),
        error_msg="புதுப்பிப்பு தோல்வியடைந்தது"  # Update failed
    )
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="சுயவிவரம் காணப்படவில்லை"
        )
    
    return ProfileResponse(
        firebase_uid=profile["firebase_uid"],
        mobile=profile.get("mobile", ""),
        name=profile.get("name", ""),
        location=profile.get("location", ""),
        crop_type=profile.get("crop_type", ""),
        district=profile.get("district", ""),
        soil_type=profile.get("soil_type", ""),
        state=profile.get("state", ""),
        experience=profile.get("experience", ""),
        farming_type=profile.get("farming_type", ""),
        language=profile.get("language", "tamil"),
        user_type=profile.get("user_type", "farmer"),
        created_at=profile.get("created_at", ""),
        is_verified=profile.get("is_verified", True)
    )


@router.post("/verify-token")
async def verify_token(
    firebase_user: FirebaseUser = Depends(get_current_firebase_user)
):
    """Verify Firebase token is valid"""
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
    Note: Firebase token invalidation happens on client side.
    """
    return {
        "message": "வெற்றிகரமாக வெளியேறியது"  # Logged out successfully
    }
