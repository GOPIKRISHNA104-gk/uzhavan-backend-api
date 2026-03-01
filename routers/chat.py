"""
AI Chat Router - Uzhavan AI Farming Assistant with Complete Language Consistency
Integrated with Real-time Market Prices, Weather, and Agriculture News.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import google.generativeai as genai
from datetime import datetime
import uuid
import base64
from PIL import Image
import io
import json
import logging

from database import get_db, ChatHistory, User
from schemas import ChatRequest, ChatResponse
from config import settings
from auth_deps import get_current_user
from services.localization import translation_service

# Import Services for Context Injection
from services.mandi_service import mandi_service
from services.weather_service import weather_service
from routers.agriculture_news import fetch_agriculture_news, translate_news_batch

router = APIRouter()
logger = logging.getLogger(__name__)

# Configure Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

# ================= PERSONA & PROMPT =================

UZHAVAN_PERSONA = """You are UZHAVAN AI, a multilingual, voice-enabled, farmer-first AI assistant built for Indian farmers.

ROLES:
- Agricultural expert (Crop advice, organic farming)
- Market analyst (Price trends, selling advice)
- Weather advisor (Rain alerts, sowing decisions)
- Plant disease doctor (Diagnosis, remedies)
- Local-language voice assistant (Friendly, simple village tone)

CRITICAL LANGUAGE RULES:
1. ALWAYS respond in the user's selected language ONLY
2. NEVER mix languages in your response
3. NEVER use English words when responding in local languages
4. Use simple, farmer-friendly vocabulary
5. Speak like a knowledgeable village elder

RESPONSE RULES:
1. ALWAYS be confident and helpful. NEVER say "data not available" or "I don't know". If data is missing, give general advice.
2. USE LOCAL SLANG & SIMPLE WORDS. Avoid technical jargon. Speak like a friendly village expert.
3. CONTEXT: You have access to REAL-TIME data (Prices, Weather, News) below. USE IT.
   - If user asks about prices, quote the specific numbers from context.
   - If user asks about rain, use the weather forecast provided.
4. TONE: Warm, respectful, encouraging. Use appropriate greetings for the language.

DATA SOURCES:
- Market Prices: Agmark (data.gov.in)
- Weather: Open-Meteo
- News: Agriculture News

If the user asks "What is the price of tomato?", LOOK at the [CONTEXT] section.
If [CONTEXT] has tomato prices, say: "In [Market Name], Tomato is selling between ₹[Min] and ₹[Max] today."
If [CONTEXT] is empty, say: "Today's market prices are updating. Generally, tomato prices are around ₹20-30/kg this season."

"""

# ================= CONTEXT INJECTION =================

async def get_context_data(message: str, user: User, db: AsyncSession, language: str) -> str:
    """
    Analyze user message and fetch relevant context from backend services.
    Returns a formatted string to append to the system prompt.
    All context data is translated to the user's language.
    """
    context_parts = []
    message_lower = message.lower()
    
    # User location fallback
    location = user.location if user.location else "All India"
    # Extract state from location if possible (simple heuristic)
    state = "tamil_nadu" # Default
    if user.location:
        # This is a simplification; ideally use a location service mapping
        if "tamil" in user.location.lower(): state = "tamil_nadu"
        elif "karnataka" in user.location.lower(): state = "karnataka"
        elif "kerala" in user.location.lower(): state = "kerala"
        elif "andhra" in user.location.lower(): state = "andhra_pradesh"
    
    # 1. Market Price Intent
    price_keywords = ["price", "cost", "rate", "rupee", "₹", "விலை", "भाव", "ధర", "വില", "ಬೆಲೆ"]
    if any(k in message_lower for k in price_keywords):
        try:
            # Check for commodity names
            commodities = ["tomato", "onion", "potato", "rice", "wheat", "cotton", "paddy", "coconut"]
            target_commodity = next((c for c in commodities if c in message_lower), None)
            
            prices = await mandi_service.get_today_prices(
                db, 
                limit=10, 
                state=user.location if user.location else None,
                commodity=target_commodity
            )
            
            if prices:
                price_header = await translation_service.translate_text(
                    "Recent Market Prices:", language, "market"
                )
                price_str = f"{price_header}\n"
                
                for p in prices[:5]: # Top 5
                    # Translate commodity name
                    commodity_name = await translation_service.translate_crop_name(
                        p.get('commodity', ''), language
                    )
                    
                    # Translate location names if needed
                    market_name = p.get('market', '')
                    district_name = p.get('district', '')
                    
                    price_str += f"- {commodity_name} {await translation_service.translate_text('in', language, 'general')} {market_name}, {district_name}: "
                    price_str += f"{await translation_service.translate_text('Min', language, 'general')} ₹{p.get('min_price')}, "
                    price_str += f"{await translation_service.translate_text('Max', language, 'general')} ₹{p.get('max_price')}, "
                    price_str += f"{await translation_service.translate_text('Modal', language, 'general')} ₹{p.get('modal_price')}\n"
                    
                context_parts.append(price_str)
            else:
                no_price_msg = await translation_service.translate_text(
                    f"No specific live prices found for {location}. Use general knowledge.", 
                    language, "market"
                )
                context_parts.append(no_price_msg)
        except Exception as e:
            logger.error(f"Context error (prices): {e}")

    # 2. Weather Intent
    weather_keywords = ["weather", "rain", "sun", "cloud", "forecast", "climate", "மழை", "வெயில்", "வானிலை", "मौसम", "వాతావరణం", "കാലാവസ്ഥ", "ಹವಾಮಾನ"]
    if any(k in message_lower for k in weather_keywords):
        try:
            # Use user location or default to Chennai
            search_loc = user.location if user.location else "Chennai"
            weather_data = await weather_service.get_forecast(search_loc)
            
            if weather_data and "current" in weather_data:
                curr = weather_data["current"]
                adv = weather_data.get("farming_advisory", "")
                rain = weather_data.get("rain_alert", {})
                
                # Translate weather information
                weather_header = await translation_service.translate_text(
                    f"Weather in {search_loc}:", language, "weather"
                )
                temp_label = await translation_service.translate_text("Temperature", language, "weather")
                condition_label = await translation_service.translate_text("Condition", language, "weather")
                rain_alert_label = await translation_service.translate_text("Rain Alert", language, "weather")
                advisory_label = await translation_service.translate_text("Advisory", language, "weather")
                
                # Translate weather condition
                weather_condition = await translation_service.translate_weather_condition(
                    curr.get('weather_description', ''), language
                )
                
                # Translate rain alert message
                rain_message = rain.get('alert_message', 'No alert')
                if rain_message != 'No alert':
                    rain_message = await translation_service.translate_text(rain_message, language, "weather")
                else:
                    rain_message = await translation_service.translate_text("No alert", language, "weather")
                
                # Translate advisory
                if adv:
                    adv = await translation_service.translate_text(adv, language, "weather")
                
                weather_str = f"{weather_header}\n"
                weather_str += f"- {temp_label}: {curr.get('temperature')}°C\n"
                weather_str += f"- {condition_label}: {weather_condition}\n"
                weather_str += f"- {rain_alert_label}: {rain_message}\n"
                weather_str += f"- {advisory_label}: {adv}\n"
                context_parts.append(weather_str)
        except Exception as e:
            logger.error(f"Context error (weather): {e}")

    # 3. News Intent
    news_keywords = ["news", "scheme", "subsidy", "gov", "government", "செய்தி", "திட்டம்", "समाचार", "వార్తలు", "വാർത്തകൾ", "ಸುದ್ದಿ"]
    if any(k in message_lower for k in news_keywords):
        try:
            # Fetch news in English first
            news_items = await fetch_agriculture_news(state=state)
            if news_items:
                # Translate news to user's language
                translated_news = await translate_news_batch(news_items, language)
                
                news_header = await translation_service.translate_text(
                    "Latest Agriculture News:", language, "news"
                )
                news_str = f"{news_header}\n"
                
                for item in translated_news[:3]:
                    title = item.get('title', '')
                    date = item.get('date', '')
                    news_str += f"- {title} ({date})\n"
                context_parts.append(news_str)
        except Exception as e:
            logger.error(f"Context error (news): {e}")

    if not context_parts:
        return ""
        
    context_header = await translation_service.translate_text(
        "[REAL-TIME CONTEXT FROM DATABASE]", language, "general"
    )
    context_footer = await translation_service.translate_text(
        "[END CONTEXT]", language, "general"
    )
    
    return f"\n{context_header}\n" + "\n".join(context_parts) + f"\n{context_footer}\n"


@router.post("/send", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send a message to the AI farming assistant with Context Injection and Complete Language Support"""
    
    if not settings.GEMINI_API_KEY:
        error_msg = await translation_service.translate_text(
            "AI service not configured", request.language, "general"
        )
        raise HTTPException(status_code=500, detail=error_msg)
    
    # Normalize language
    language = request.language.lower().strip()
    valid_languages = ["tamil", "hindi", "telugu", "malayalam", "kannada", "english"]
    if language not in valid_languages:
        language = "english"
    
    try:
        # 1. Fetch Context (already translated)
        context_data = await get_context_data(request.message, current_user, db, language)
        
        # 2. Build Prompt with strict language instruction
        language_instruction = translation_service.get_language_instruction(language)
        
        full_prompt = f"{UZHAVAN_PERSONA}\n\n{context_data}\n\n{language_instruction}\n\nUser: {request.message}"
        
        # 3. Handle image if provided
        if request.image_base64:
            try:
                if "base64," in request.image_base64:
                    image_data = base64.b64decode(request.image_base64.split("base64,")[1])
                else:
                    image_data = base64.b64decode(request.image_base64)
                image = Image.open(io.BytesIO(image_data))
                
                model = genai.GenerativeModel('gemini-1.5-flash')
                response = model.generate_content([full_prompt, image])
            except Exception as e:
                error_msg = await translation_service.translate_text(
                    f"Invalid image: {str(e)}", language, "general"
                )
                raise HTTPException(status_code=400, detail=error_msg)
        else:
            # Text-only request
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(full_prompt)
        
        ai_response = response.text
        
        # 4. Validate response language (basic check)
        if language != "english" and ai_response:
            # If response contains too much English, try to translate it
            english_words = ["the", "and", "or", "but", "is", "are", "was", "were", "have", "has", "had"]
            response_words = ai_response.lower().split()
            english_count = sum(1 for word in response_words if word in english_words)
            
            # If more than 20% English words, translate the response
            if len(response_words) > 0 and (english_count / len(response_words)) > 0.2:
                logger.warning(f"AI response contains too much English, translating to {language}")
                ai_response = await translation_service.translate_text(
                    ai_response, language, "general"
                )
        
        # 5. Save to database
        chat_record = ChatHistory(
            user_id=current_user.id,
            message=request.message,
            response=ai_response,
            language=language
        )
        db.add(chat_record)
        await db.commit()
        
        return ChatResponse(
            id=str(uuid.uuid4()),
            message=request.message,
            response=ai_response,
            language=language,
            timestamp=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"AI Chat Error: {e}")
        # Return error message in user's language
        error_msg = await translation_service.translate_text(
            "AI service temporarily unavailable. Please try again.", language, "general"
        )
        raise HTTPException(status_code=500, detail=error_msg)

@router.get("/history")
async def get_chat_history(
    limit: int = 50,
    language: str = "english",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get user's chat history with language-specific labels"""
    from sqlalchemy import select, desc
    
    # Normalize language
    language = language.lower().strip()
    valid_languages = ["tamil", "hindi", "telugu", "malayalam", "kannada", "english"]
    if language not in valid_languages:
        language = "english"
    
    result = await db.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == current_user.id)
        .order_by(desc(ChatHistory.created_at))
        .limit(limit)
    )
    
    history = result.scalars().all()
    
    # Translate labels
    no_history_msg = await translation_service.translate_text(
        "No chat history found", language, "general"
    )
    
    if not history:
        return {
            "message": no_history_msg,
            "history": []
        }
    
    return {
        "message": await translation_service.translate_text(
            "Chat history loaded successfully", language, "general"
        ),
        "history": [
            {
                "id": str(chat.id),
                "message": chat.message,
                "response": chat.response,
                "language": chat.language,
                "timestamp": chat.created_at.isoformat()
            }
            for chat in history
        ]
    }
