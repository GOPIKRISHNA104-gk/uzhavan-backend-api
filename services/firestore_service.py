"""
Firestore Service for Farmer Profiles
Handles all Firestore operations for farmer data

Collection: farmers
Document ID: firebase_uid (same UID from Firebase Auth)
"""

from firebase_admin import firestore
from datetime import datetime
from typing import Optional, Dict, Any
import asyncio
from functools import lru_cache

# Initialize Firestore client (lazy initialization)
_db = None

def get_firestore_client():
    """Get Firestore client instance (singleton)"""
    global _db
    if _db is None:
        _db = firestore.client()
    return _db


# ============== FARMER PROFILE OPERATIONS ==============

async def create_farmer_profile(
    firebase_uid: str,
    mobile: str,
    name: str,
    location: Optional[str] = None,
    crop_type: Optional[str] = None,
    district: Optional[str] = None,
    soil_type: Optional[str] = None,
    state: Optional[str] = None,
    experience: Optional[str] = None,
    farming_type: Optional[str] = None,
    language: str = "tamil",
    user_type: str = "farmer"
) -> Dict[str, Any]:
    """
    Create a new farmer profile in Firestore.
    Uses firebase_uid as document ID for direct UID mapping.
    
    Single write operation - optimized for speed.
    """
    db = get_firestore_client()
    
    # Prepare profile data
    profile_data = {
        "mobile": mobile,
        "name": name,
        "location": location or "",
        "crop_type": crop_type or "",
        "district": district or "",
        "soil_type": soil_type or "",
        "state": state or "",
        "experience": experience or "",
        "farming_type": farming_type or "",
        "language": language,
        "user_type": user_type,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "is_verified": True
    }
    
    # Use firebase_uid as document ID - Single write operation
    doc_ref = db.collection("farmers").document(firebase_uid)
    
    # Run in thread pool to avoid blocking
    await asyncio.to_thread(doc_ref.set, profile_data)
    
    # Return created profile with uid
    profile_data["firebase_uid"] = firebase_uid
    profile_data["created_at"] = datetime.utcnow().isoformat()
    
    return profile_data


async def get_farmer_profile(firebase_uid: str) -> Optional[Dict[str, Any]]:
    """
    Get farmer profile by Firebase UID.
    Returns None if not found.
    """
    db = get_firestore_client()
    doc_ref = db.collection("farmers").document(firebase_uid)
    
    # Run in thread pool
    doc = await asyncio.to_thread(doc_ref.get)
    
    if not doc.exists:
        return None
    
    profile = doc.to_dict()
    profile["firebase_uid"] = firebase_uid
    
    # Convert Firestore timestamp to ISO string
    if profile.get("created_at"):
        try:
            profile["created_at"] = profile["created_at"].isoformat()
        except:
            profile["created_at"] = datetime.utcnow().isoformat()
    
    if profile.get("updated_at"):
        try:
            profile["updated_at"] = profile["updated_at"].isoformat()
        except:
            profile["updated_at"] = datetime.utcnow().isoformat()
    
    return profile


async def update_farmer_profile(
    firebase_uid: str,
    updates: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Update farmer profile fields.
    Only updates provided fields.
    """
    db = get_firestore_client()
    doc_ref = db.collection("farmers").document(firebase_uid)
    
    # Add update timestamp
    updates["updated_at"] = firestore.SERVER_TIMESTAMP
    
    # Update only provided fields
    await asyncio.to_thread(doc_ref.update, updates)
    
    # Return updated profile
    return await get_farmer_profile(firebase_uid)


async def check_farmer_exists(firebase_uid: str) -> bool:
    """Check if farmer profile exists"""
    db = get_firestore_client()
    doc_ref = db.collection("farmers").document(firebase_uid)
    doc = await asyncio.to_thread(doc_ref.get)
    return doc.exists


async def check_mobile_exists(mobile: str) -> Optional[str]:
    """
    Check if mobile number is already registered.
    Returns firebase_uid if exists, None otherwise.
    """
    db = get_firestore_client()
    
    # Query by mobile number
    query = db.collection("farmers").where("mobile", "==", mobile).limit(1)
    docs = await asyncio.to_thread(lambda: list(query.stream()))
    
    if docs:
        return docs[0].id
    return None


async def delete_farmer_profile(firebase_uid: str) -> bool:
    """Delete farmer profile"""
    db = get_firestore_client()
    doc_ref = db.collection("farmers").document(firebase_uid)
    await asyncio.to_thread(doc_ref.delete)
    return True
