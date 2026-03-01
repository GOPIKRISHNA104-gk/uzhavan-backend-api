"""
Firebase Admin SDK Configuration
This file handles Firebase Admin initialization for backend token verification.
NEVER expose these credentials to the frontend.
"""

import firebase_admin
from firebase_admin import credentials, auth, firestore
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from functools import lru_cache
import os
import json
from typing import Optional
from pydantic import BaseModel

# Security scheme for Firebase Bearer tokens
firebase_security = HTTPBearer(auto_error=False)

class FirebaseUser(BaseModel):
    """Verified Firebase user data extracted from ID token"""
    uid: str
    phone_number: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    email_verified: bool = False
    firebase_token: str

def initialize_firebase():
    """
    Initialize Firebase Admin SDK using service account credentials.
    The service account JSON can be provided via:
    1. FIREBASE_SERVICE_ACCOUNT_JSON env var (JSON string)
    2. FIREBASE_SERVICE_ACCOUNT_PATH env var (path to JSON file)
    """
    if firebase_admin._apps:
        # Already initialized
        return
    
    # Option 1: JSON string in environment variable
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        try:
            service_account_info = json.loads(service_account_json)
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            print("[OK] Firebase Admin initialized from JSON env var")
            return
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
    
    # Option 2: Path to service account JSON file
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "firebase-service-account.json")
    if os.path.exists(service_account_path):
        try:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
            print(f"[OK] Firebase Admin initialized from file: {service_account_path}")
            return
        except Exception as e:
            print(f"[ERROR] Failed to initialize from file: {e}")
    
    # Fallback: Initialize without credentials (limited functionality)
    print("[WARN] Firebase Admin initialized without credentials (limited mode)")
    print("   Set FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT_PATH")
    try:
        firebase_admin.initialize_app()
    except Exception:
        pass

def verify_firebase_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims.
    Raises HTTPException if token is invalid.
    """
    try:
        # Verify the ID token with clock skew tolerance (fixes "Token used too early" errors)
        decoded_token = auth.verify_id_token(id_token, clock_skew_seconds=10)
        return decoded_token
    except auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase token has expired. Please login again.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except auth.RevokedIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase token has been revoked. Please login again.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except auth.InvalidIdTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"}
        )

async def get_current_firebase_user(
    credentials: HTTPAuthorizationCredentials = Depends(firebase_security)
) -> FirebaseUser:
    """
    FastAPI dependency to get the current authenticated Firebase user.
    Use this dependency on any protected endpoint.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Please provide Firebase ID token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = credentials.credentials
    
    # Run synchronous verification in threadpool to avoid blocking event loop
    import asyncio
    try:
        # print(f"🔐 Verifying Firebase Token: {token[:10]}...") 
        decoded_token = await asyncio.to_thread(verify_firebase_token, token)
        # print("✅ Token Verified")
    except Exception as e:
        print(f"[ERROR] Token Verification Failed: {e}")
        raise e
    
    return FirebaseUser(
        uid=decoded_token.get("uid", ""),
        phone_number=decoded_token.get("phone_number"),
        email=decoded_token.get("email"),
        name=decoded_token.get("name"),
        picture=decoded_token.get("picture"),
        email_verified=decoded_token.get("email_verified", False),
        firebase_token=token
    )

async def get_optional_firebase_user(
    credentials: HTTPAuthorizationCredentials = Depends(firebase_security)
) -> Optional[FirebaseUser]:
    """
    Optional Firebase authentication dependency.
    Returns None if no valid token is provided (for public endpoints that optionally support auth).
    """
    if not credentials:
        return None
    
    try:
        return await get_current_firebase_user(credentials)
    except HTTPException:
        return None

def get_user_by_phone(phone_number: str):
    """Get Firebase user by phone number"""
    try:
        user = auth.get_user_by_phone_number(phone_number)
        return user
    except auth.UserNotFoundError:
        return None
    except Exception as e:
        print(f"Error fetching user by phone: {e}")
        return None

def create_custom_token(uid: str, claims: dict = None) -> str:
    """Create a custom Firebase token for a user"""
    try:
        return auth.create_custom_token(uid, claims)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create custom token: {str(e)}"
        )

# Initialize Firebase on module import
initialize_firebase()
