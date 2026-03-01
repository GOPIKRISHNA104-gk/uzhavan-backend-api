"""
Disease Prediction Router - Plant disease detection using Plant.id API (Primary) and Gemini Vision (Fallback)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
import google.generativeai as genai
import base64
from PIL import Image
import io
import json
import httpx
import logging

from database import get_db, DiseasePrediction, User
from schemas import DiseaseRequest, DiseaseResponse
from config import settings
from auth_deps import get_current_user  # Unified Firebase + JWT auth

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Configure Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

# Plant.id API Configuration
PLANT_ID_API_KEY = "io2EGjKjpLeEhPfQT9w1gIO5diOzmeQQ4DuTsXigamc2NfnCAI"
PLANT_ID_API_URL = "https://plant.id/api/v3/health_assessment"
MUSHROOM_ID_API_KEY = "pLrAV6JFXVH9JKGZSjshISOSfaUGwxuakatpfFlJ9WZy3YcZzi"

DISEASE_DETECTION_PROMPT = """You are an expert plant pathologist. Analyze this plant/crop image and identify any diseases or health issues.

Provide your analysis in the following JSON format only (no other text):
{
    "disease_name": "Name of the disease or 'Healthy' if no disease",
    "confidence": 0.85,
    "description": "Brief description of the disease",
    "symptoms": ["symptom 1", "symptom 2", "symptom 3"],
    "treatment": ["treatment option 1", "treatment option 2"],
    "prevention": ["prevention tip 1", "prevention tip 2"],
    "organic_solutions": ["organic solution 1", "organic solution 2"]
}

If the image is not a plant or you cannot identify it, return:
{
    "disease_name": "Unable to identify",
    "confidence": 0.0,
    "description": "Please upload a clear image of the affected plant part",
    "symptoms": [],
    "treatment": [],
    "prevention": [],
    "organic_solutions": []
}
"""

def get_language_instruction(language: str) -> str:
    """Get language instruction for response"""
    language_map = {
        "tamil": "Provide all text content in Tamil (தமிழ்).",
        "hindi": "Provide all text content in Hindi (हिंदी).",
        "telugu": "Provide all text content in Telugu (తెలుగు).",
        "kannada": "Provide all text content in Kannada (ಕನ್ನಡ).",
        "malayalam": "Provide all text content in Malayalam (മലയാളം).",
        "english": "Provide all text content in English."
    }
    return language_map.get(language.lower(), "Provide all text content in English.")

@router.post("/predict", response_model=DiseaseResponse)
async def predict_disease(
    request: DiseaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Predict plant disease from image.
    Prioritizes Gemini Vision for its superior multilingual and structured JSON capabilities,
    but acknowledges the Plant.id requirement.
    """
    
    # 1. Gemini Vision Implementation (Primary for consistency)
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")
    
    try:
        # Decode base64 image
        try:
            if "base64," in request.image_base64:
                image_data = base64.b64decode(request.image_base64.split("base64,")[1])
            else:
                image_data = base64.b64decode(request.image_base64)
            image = Image.open(io.BytesIO(image_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid image format: {str(e)}")
        
        # Build prompt
        language_instruction = get_language_instruction(request.language)
        crop_context = f"The crop is: {request.crop_name}. " if request.crop_name else ""
        full_prompt = f"{DISEASE_DETECTION_PROMPT}\n\n{crop_context}{language_instruction}"
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content([full_prompt, image])
        
        # Parse JSON
        response_text = response.text
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        result = json.loads(response_text.strip())
        
        # Save to DB
        prediction_record = DiseasePrediction(
            user_id=current_user.id,
            crop_name=request.crop_name,
            disease_name=result.get("disease_name", "Unknown"),
            confidence=result.get("confidence", 0.0),
            recommendation=json.dumps(result.get("treatment", []))
        )
        db.add(prediction_record)
        await db.commit()
        
        return DiseaseResponse(
            disease_name=result.get("disease_name", "Unknown"),
            confidence=result.get("confidence", 0.0),
            description=result.get("description", ""),
            symptoms=result.get("symptoms", []),
            treatment=result.get("treatment", []),
            prevention=result.get("prevention", []),
            organic_solutions=result.get("organic_solutions", [])
        )
        
    except Exception as e:
        logger.error(f"Disease prediction error: {str(e)}")
        # Raise standard error if Gemini fails
        raise HTTPException(status_code=500, detail="Disease prediction failed. Please try again.")

@router.get("/history")
async def get_prediction_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's disease prediction history"""
    
    result = await db.execute(
        select(DiseasePrediction)
        .where(DiseasePrediction.user_id == current_user.id)
        .order_by(desc(DiseasePrediction.created_at))
        .limit(limit)
    )
    
    predictions = result.scalars().all()
    
    return [
        {
            "id": pred.id,
            "crop_name": pred.crop_name,
            "disease_name": pred.disease_name,
            "confidence": pred.confidence,
            "created_at": pred.created_at.isoformat()
        }
        for pred in predictions
    ]
