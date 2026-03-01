"""
Crop Recommendation Router - AI-powered crop suggestions
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import google.generativeai as genai
import json

from database import get_db, CropRecommendationHistory, User
from schemas import CropRecommendationRequest, CropRecommendationResponse, CropItem
from config import settings
from auth_deps import get_current_user  # Unified Firebase + JWT auth

router = APIRouter()

# Configure Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

def get_language_instruction(language: str) -> str:
    """Get language instruction for AI response"""
    language_map = {
        "tamil": "Provide all text content in Tamil (தமிழ்).",
        "hindi": "Provide all text content in Hindi (हिंदी).",
        "telugu": "Provide all text content in Telugu (తెలుగు).",
        "english": "Provide all text content in English."
    }
    return language_map.get(language.lower(), "Provide all text content in English.")

CROP_RECOMMENDATION_PROMPT = """You are an expert agronomist. Based on the following conditions, recommend the best crops to grow.

Conditions:
- Soil Type: {soil_type}
- Location: {location}
- Season: {season}
- Water Availability: {water_availability}
- Budget: {budget}

Provide your recommendations in the following JSON format only (no other text):
{{
    "recommended_crops": [
        {{
            "name": "Crop name",
            "suitability_score": 0.9,
            "expected_yield": "Expected yield per acre",
            "water_requirement": "Low/Medium/High",
            "growth_duration": "X-Y months",
            "market_demand": "High/Medium/Low",
            "tips": ["Tip 1", "Tip 2"]
        }}
    ],
    "soil_health_tips": ["Soil tip 1", "Soil tip 2"],
    "seasonal_advice": "Seasonal advice for the farmer"
}}

Recommend 3-5 crops sorted by suitability. Focus on crops commonly grown in India.
{language_instruction}
"""

@router.post("/recommend", response_model=CropRecommendationResponse)
async def get_crop_recommendations(
    request: CropRecommendationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get AI-powered crop recommendations"""
    
    if not settings.GEMINI_API_KEY:
        # Return fallback recommendations
        return CropRecommendationResponse(
            recommended_crops=[
                CropItem(
                    name="Rice",
                    suitability_score=0.85,
                    expected_yield="4-5 tons per hectare",
                    water_requirement="High",
                    growth_duration="4-5 months",
                    market_demand="High",
                    tips=["Use certified seeds", "Maintain proper water levels"]
                ),
                CropItem(
                    name="Vegetables",
                    suitability_score=0.80,
                    expected_yield="15-20 tons per hectare",
                    water_requirement="Medium",
                    growth_duration="2-3 months",
                    market_demand="High",
                    tips=["Start with tomatoes or brinjal", "Use organic fertilizers"]
                )
            ],
            soil_health_tips=["Test soil pH regularly", "Add organic matter"],
            seasonal_advice="Plan crops according to monsoon patterns."
        )
    
    try:
        language_instruction = get_language_instruction(request.language)
        
        prompt = CROP_RECOMMENDATION_PROMPT.format(
            soil_type=request.soil_type,
            location=request.location,
            season=request.season,
            water_availability=request.water_availability,
            budget=request.budget,
            language_instruction=language_instruction
        )
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        # Parse JSON response
        response_text = response.text
        
        # Extract JSON from response
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        result = json.loads(response_text.strip())
        
        # Save to database
        recommendation_record = CropRecommendationHistory(
            user_id=current_user.id,
            soil_type=request.soil_type,
            location=request.location,
            season=request.season,
            recommended_crops=json.dumps([c["name"] for c in result.get("recommended_crops", [])])
        )
        db.add(recommendation_record)
        await db.commit()
        
        # Convert to response format
        crops = [
            CropItem(
                name=c.get("name", "Unknown"),
                suitability_score=c.get("suitability_score", 0.0),
                expected_yield=c.get("expected_yield", "Varies"),
                water_requirement=c.get("water_requirement", "Medium"),
                growth_duration=c.get("growth_duration", "3-4 months"),
                market_demand=c.get("market_demand", "Medium"),
                tips=c.get("tips", [])
            )
            for c in result.get("recommended_crops", [])
        ]
        
        return CropRecommendationResponse(
            recommended_crops=crops,
            soil_health_tips=result.get("soil_health_tips", []),
            seasonal_advice=result.get("seasonal_advice", "")
        )
        
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse AI response"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation error: {str(e)}"
        )

@router.get("/soil-types")
async def get_soil_types():
    """Get list of common soil types"""
    return {
        "soil_types": [
            {"id": "alluvial", "name": "Alluvial Soil", "description": "Found in river plains, very fertile"},
            {"id": "black", "name": "Black Soil (Regur)", "description": "Good for cotton, rich in minerals"},
            {"id": "red", "name": "Red Soil", "description": "Found in dry areas, good for millets"},
            {"id": "laterite", "name": "Laterite Soil", "description": "Found in heavy rainfall areas"},
            {"id": "desert", "name": "Desert/Arid Soil", "description": "Sandy, low moisture retention"},
            {"id": "mountain", "name": "Mountain Soil", "description": "Found in hilly regions"},
            {"id": "clay", "name": "Clay Soil", "description": "Heavy, retains water well"},
            {"id": "loamy", "name": "Loamy Soil", "description": "Ideal for most crops, balanced texture"}
        ]
    }

@router.get("/seasons")
async def get_seasons():
    """Get list of farming seasons in India"""
    return {
        "seasons": [
            {"id": "kharif", "name": "Kharif (Monsoon)", "months": "June - October", "crops": "Rice, Cotton, Jowar"},
            {"id": "rabi", "name": "Rabi (Winter)", "months": "October - March", "crops": "Wheat, Mustard, Gram"},
            {"id": "zaid", "name": "Zaid (Summer)", "months": "March - June", "crops": "Watermelon, Cucumber, Vegetables"}
        ]
    }

@router.get("/history")
async def get_recommendation_history(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's crop recommendation history"""
    from sqlalchemy import select, desc
    
    result = await db.execute(
        select(CropRecommendationHistory)
        .where(CropRecommendationHistory.user_id == current_user.id)
        .order_by(desc(CropRecommendationHistory.created_at))
        .limit(limit)
    )
    
    recommendations = result.scalars().all()
    
    return [
        {
            "id": rec.id,
            "soil_type": rec.soil_type,
            "location": rec.location,
            "season": rec.season,
            "recommended_crops": json.loads(rec.recommended_crops) if rec.recommended_crops else [],
            "created_at": rec.created_at.isoformat()
        }
        for rec in recommendations
    ]
