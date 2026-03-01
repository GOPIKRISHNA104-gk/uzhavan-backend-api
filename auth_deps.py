"""
Authentication Dependencies
Provides unified authentication for all protected endpoints.
Supports both Firebase and legacy JWT authentication.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Union, Optional

from database import get_db, User
from firebase_admin_config import (
    get_current_firebase_user, 
    FirebaseUser,
    verify_firebase_token
)
from config import settings

# Security scheme
security = HTTPBearer(auto_error=False)

async def get_user_from_firebase(
    firebase_user: FirebaseUser,
    db: AsyncSession
) -> Optional[User]:
    """Get database user from Firebase user"""
    result = await db.execute(
        select(User).where(User.firebase_uid == firebase_user.uid)
    )
    user = result.scalar_one_or_none()
    
    if not user and firebase_user.phone_number:
        # Try to find by phone number
        phone = firebase_user.phone_number
        if phone.startswith("+91"):
            phone = phone[3:]
        phone = ''.join(filter(str.isdigit, phone))
        
        result = await db.execute(
            select(User).where(User.phone == phone)
        )
        user = result.scalar_one_or_none()
    
    return user

async def get_current_user_firebase(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get current authenticated user using Firebase token.
    This is the primary authentication method.
    
    Usage:
        @router.get("/protected")
        async def protected_route(user: User = Depends(get_current_user_firebase)):
            return {"user_id": user.id}
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide Firebase ID token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = credentials.credentials
    
    # Verify Firebase token
    decoded_token = verify_firebase_token(token)
    
    # Get user from database
    firebase_user = FirebaseUser(
        uid=decoded_token.get("uid", ""),
        phone_number=decoded_token.get("phone_number"),
        email=decoded_token.get("email"),
        name=decoded_token.get("name"),
        picture=decoded_token.get("picture"),
        email_verified=decoded_token.get("email_verified", False),
        firebase_token=token
    )
    
    user = await get_user_from_firebase(firebase_user, db)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please complete registration first."
        )
    
    return user

async def get_optional_user_firebase(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Optional Firebase authentication.
    Returns None if no valid token is provided.
    Useful for endpoints that work with or without authentication.
    """
    if not credentials:
        return None
    
    try:
        return await get_current_user_firebase(credentials, db)
    except HTTPException:
        return None

# ============== Legacy JWT Support ==============
# Keep for backward compatibility during migration

from jose import JWTError, jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def get_current_user_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Legacy JWT authentication.
    Used for backward compatibility.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    return user

# ============== Unified Auth Dependency ==============

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Unified authentication that tries Firebase first, then falls back to JWT.
    Use this for endpoints that should support both auth methods.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = credentials.credentials
    
    # Try Firebase first
    try:
        decoded_token = verify_firebase_token(token)
        firebase_user = FirebaseUser(
            uid=decoded_token.get("uid", ""),
            phone_number=decoded_token.get("phone_number"),
            email=decoded_token.get("email"),
            name=decoded_token.get("name"),
            picture=decoded_token.get("picture"),
            email_verified=decoded_token.get("email_verified", False),
            firebase_token=token
        )
        user = await get_user_from_firebase(firebase_user, db)
        if user:
            return user
    except HTTPException:
        pass
    
    # Fall back to JWT
    try:
        return await get_current_user_jwt(credentials, db)
    except HTTPException:
        pass
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token"
    )
