"""
Agriculture News Intelligence Service for Indian Farmers

Features:
- Fetches agriculture news from NewsData.io API
- State-based filtering using intelligent keyword matching
- Translation to local languages (Tamil, Telugu, Malayalam, Kannada, Hindi)
- Caching for performance (30 minutes)
- Farmer-friendly content filtering

Data Source: NewsData.io API
Translation: Google Gemini AI (for accurate regional translations)
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
import httpx
import asyncio
import os
import json

router = APIRouter()

# ============== CONFIGURATION ==============

NEWS_API_KEY = "pub_22339a8bc4d54b4bae006ee101329ec1"
NEWS_API_BASE = "https://newsdata.io/api/1/news"

# Cache configuration
CACHE_TTL_MINUTES = 30
_news_cache: Dict[str, dict] = {}

# ============== ENUMS ==============

class SupportedLanguage(str, Enum):
    TAMIL = "tamil"
    TELUGU = "telugu"
    MALAYALAM = "malayalam"
    KANNADA = "kannada"
    HINDI = "hindi"
    ENGLISH = "english"  # For testing

class IndianState(str, Enum):
    TAMIL_NADU = "tamil_nadu"
    KARNATAKA = "karnataka"
    KERALA = "kerala"
    ANDHRA_PRADESH = "andhra_pradesh"
    TELANGANA = "telangana"
    MAHARASHTRA = "maharashtra"
    GUJARAT = "gujarat"
    RAJASTHAN = "rajasthan"
    MADHYA_PRADESH = "madhya_pradesh"
    UTTAR_PRADESH = "uttar_pradesh"
    BIHAR = "bihar"
    WEST_BENGAL = "west_bengal"
    PUNJAB = "punjab"
    HARYANA = "haryana"
    ODISHA = "odisha"
    ALL_INDIA = "all_india"

# ============== STATE KEYWORDS ==============

STATE_KEYWORDS: Dict[str, List[str]] = {
    "tamil_nadu": [
        "Tamil Nadu", "TN farmers", "Cauvery", "Chennai agriculture",
        "Thanjavur", "Delta farmers", "Madurai farming", "Coimbatore agriculture",
        "Tamil Nadu MSP", "TN crop", "Tamil Nadu irrigation"
    ],
    "karnataka": [
        "Karnataka", "Mandya farmers", "Bengaluru agriculture",
        "Karnataka MSP", "Mysuru farming", "Karnataka irrigation",
        "Bellary farming", "Hubli agriculture"
    ],
    "kerala": [
        "Kerala", "Kerala farming", "Kerala agriculture",
        "Kochi farmers", "Kerala spices", "Kerala rubber",
        "Wayanad farming", "Kerala coconut"
    ],
    "andhra_pradesh": [
        "Andhra Pradesh", "AP farmers", "Vijayawada agriculture",
        "Guntur farming", "Krishna district", "Andhra MSP",
        "Rayalaseema farmers", "AP irrigation"
    ],
    "telangana": [
        "Telangana", "Hyderabad agriculture", "Telangana farmers",
        "Warangal farming", "Telangana MSP", "Telangana irrigation",
        "Nizamabad agriculture"
    ],
    "maharashtra": [
        "Maharashtra", "Pune farmers", "Nashik agriculture",
        "Vidarbha farmers", "Maharashtra MSP", "Marathwada farming",
        "Solapur agriculture", "Maharashtra sugarcane"
    ],
    "gujarat": [
        "Gujarat", "Gujarat farmers", "Ahmedabad agriculture",
        "Gujarat cotton", "Saurashtra farming", "Gujarat groundnut"
    ],
    "rajasthan": [
        "Rajasthan", "Jaipur agriculture", "Rajasthan farmers",
        "Rajasthan mustard", "Jodhpur farming"
    ],
    "madhya_pradesh": [
        "Madhya Pradesh", "MP farmers", "Bhopal agriculture",
        "MP wheat", "Indore farming", "MP soybean"
    ],
    "uttar_pradesh": [
        "Uttar Pradesh", "UP farmers", "Lucknow agriculture",
        "UP sugarcane", "Varanasi farming", "UP wheat"
    ],
    "bihar": [
        "Bihar", "Patna agriculture", "Bihar farmers",
        "Bihar rice", "Muzaffarpur farming"
    ],
    "west_bengal": [
        "West Bengal", "Kolkata agriculture", "Bengal farmers",
        "Bengal rice", "Darjeeling tea"
    ],
    "punjab": [
        "Punjab", "Ludhiana agriculture", "Punjab farmers",
        "Punjab wheat", "Amritsar farming", "Punjab MSP"
    ],
    "haryana": [
        "Haryana", "Karnal agriculture", "Haryana farmers",
        "Haryana wheat", "Rohtak farming"
    ],
    "odisha": [
        "Odisha", "Bhubaneswar agriculture", "Odisha farmers",
        "Odisha rice", "Cuttack farming"
    ],
    "all_india": [
        "India agriculture", "Indian farmers", "MSP",
        "crop insurance", "PM Kisan", "agricultural scheme",
        "farming India", "harvest India"
    ]
}

# ============== LANGUAGE MAPPING ==============

# For Gemini translation prompts
LANGUAGE_NAMES = {
    "tamil": "Tamil",
    "telugu": "Telugu",
    "malayalam": "Malayalam",
    "kannada": "Kannada",
    "hindi": "Hindi",
    "english": "English"
}

# ============== SCHEMAS ==============

class NewsItem(BaseModel):
    title: str
    title_english: Optional[str] = None
    summary: str
    summary_english: Optional[str] = None
    source: str
    date: str
    url: Optional[str] = None
    image_url: Optional[str] = None

class AgricultureNewsResponse(BaseModel):
    language: str
    state: str
    news_count: int
    news: List[NewsItem]
    cached: bool
    last_updated: str

# ============== CACHE HELPERS ==============

def get_cache_key(state: str, language: str) -> str:
    return f"agri_news_{state}_{language}"

def get_cached_news(state: str, language: str) -> Optional[dict]:
    key = get_cache_key(state, language)
    if key in _news_cache:
        cached = _news_cache[key]
        cached_time = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_time < timedelta(minutes=CACHE_TTL_MINUTES):
            return cached
    return None

def set_news_cache(state: str, language: str, data: dict):
    key = get_cache_key(state, language)
    data["_cached_at"] = datetime.now().isoformat()
    _news_cache[key] = data

# ============== NEWS FETCHING ==============

async def fetch_agriculture_news(state: str = "all_india") -> List[dict]:
    """
    Fetch agriculture news from NewsData.io API
    Always fetches in English for best coverage
    Filters by state using keywords
    """
    
    # Build query with agriculture keywords
    base_keywords = [
        "agriculture", "farming", "farmer", "crops", "irrigation",
        "fertilizer", "MSP", "crop insurance", "weather farming",
        "subsidy", "agricultural scheme", "harvest", "yield"
    ]
    
    # Add state-specific keywords
    state_specific = STATE_KEYWORDS.get(state, STATE_KEYWORDS["all_india"])
    
    # Combine keywords for query (use first few to stay within API limits)
    query_keywords = base_keywords[:5] + state_specific[:3]
    query = " OR ".join(query_keywords[:6])  # API has query length limits
    
    params = {
        "apikey": NEWS_API_KEY,
        "q": query,
        "country": "in",
        "language": "en",
        "category": "business,environment,world",  # Relevant categories
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(NEWS_API_BASE, params=params)
            
            if response.status_code != 200:
                print(f"News API error: {response.status_code}")
                return []
            
            data = response.json()
            articles = data.get("results", [])
            
            # Filter for agriculture relevance
            filtered_articles = []
            agriculture_terms = ["agri", "farm", "crop", "harvest", "msp", "subsidy", 
                                "irrigation", "fertilizer", "kisan", "yield", "seed"]
            
            for article in articles:
                title = (article.get("title") or "").lower()
                desc = (article.get("description") or "").lower()
                content = title + " " + desc
                
                # Check if article is agriculture-related
                if any(term in content for term in agriculture_terms):
                    # Additional state filtering
                    state_match = state == "all_india"
                    if not state_match:
                        for keyword in state_specific:
                            if keyword.lower() in content:
                                state_match = True
                                break
                    
                    if state_match or state == "all_india":
                        filtered_articles.append({
                            "title": article.get("title", ""),
                            "description": article.get("description") or article.get("content", "")[:200],
                            "source": article.get("source_id", "Unknown"),
                            "date": article.get("pubDate", ""),
                            "url": article.get("link", ""),
                            "image_url": article.get("image_url", "")
                        })
            
            return filtered_articles[:10]  # Return top 10 relevant articles
            
    except Exception as e:
        print(f"News fetch error: {e}")
        return []

# ============== TRANSLATION ==============

async def translate_text(text: str, target_language: str) -> str:
    """
    Translate text to target language using Gemini AI + Groq fallback
    Ensures farmer-friendly, natural translations ALWAYS work
    """
    if not text or target_language == "english":
        return text
    
    lang_name = LANGUAGE_NAMES.get(target_language, "Tamil")
    
    prompt = f"""Translate the following English text to {lang_name}.

RULES:
1. Use simple, everyday language suitable for farmers
2. Keep it natural and easy to understand
3. Preserve important terms like crop names, scheme names, prices
4. Return ONLY the translated text, nothing else

Text to translate:
{text}

{lang_name} translation:"""
    
    # Layer 1: Try Gemini
    try:
        import google.generativeai as genai
        
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash')
            
            response = await asyncio.to_thread(
                model.generate_content,
                prompt
            )
            
            translated = response.text.strip()
            if translated:
                return translated
    except Exception as e:
        print(f"[Translation] Gemini failed: {e}")
    
    # Layer 2: Groq fallback
    try:
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            import httpx as httpx_client
            async with httpx_client.AsyncClient(timeout=15.0) as client:
                groq_res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": f"You are a translator. Translate English text to {lang_name}. Return ONLY the translated text. Use simple language suitable for farmers."},
                            {"role": "user", "content": text}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 512,
                    }
                )
                
                if groq_res.status_code == 200:
                    data = groq_res.json()
                    translated = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if translated:
                        print(f"[Translation] Groq OK for {lang_name}")
                        return translated
    except Exception as e:
        print(f"[Translation] Groq fallback failed: {e}")
    
    # Both failed — return original English
    return text

async def translate_news_batch(articles: List[dict], target_language: str) -> List[dict]:
    """
    Translate multiple articles efficiently
    """
    if target_language == "english":
        return articles
    
    # Create tasks for all translations
    tasks = []
    for article in articles:
        tasks.append(_translate_single_article(article, target_language))
        
    # Run all translations in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    translated_articles = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print(f"Article translation error: {res}")
            translated_articles.append(articles[i]) # Fallback to original
        else:
            translated_articles.append(res)
            
    return translated_articles

async def _translate_single_article(article: dict, target_language: str) -> dict:
    """Helper to translate one article"""
    # Translate title and description concurrently
    t_title, t_desc = await asyncio.gather(
        translate_text(article["title"], target_language),
        translate_text(article["description"], target_language)
    )
    
    return {
        **article,
        "title_english": article["title"],
        "summary_english": article["description"],
        "title": t_title,
        "description": t_desc
    }

# ============== FALLBACK NEWS ==============

def get_fallback_news(state: str, language: str) -> List[NewsItem]:
    """
    Provide fallback news when API fails
    General agriculture updates
    """
    fallback_data = {
        "tamil": [
            NewsItem(
                title="விவசாயிகளுக்கான அரசு திட்டங்கள் புதுப்பிப்பு",
                summary="மத்திய மற்றும் மாநில அரசுகள் விவசாயிகளுக்கான பல்வேறு திட்டங்களை அறிவித்துள்ளன. PM-KISAN, பயிர் காப்பீடு போன்ற திட்டங்கள் தொடர்கின்றன.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            ),
            NewsItem(
                title="மழை முன்னறிவிப்பு - விவசாயிகள் கவனத்திற்கு",
                summary="வரும் வாரங்களில் பல்வேறு மாவட்டங்களில் மழை எதிர்பார்க்கப்படுகிறது. விதைப்பு மற்றும் அறுவடை திட்டமிடலை கவனமாக செய்யுங்கள்.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ],
        "hindi": [
            NewsItem(
                title="किसानों के लिए सरकारी योजनाओं का अपडेट",
                summary="केंद्र और राज्य सरकारें किसानों के लिए विभिन्न योजनाएं लागू कर रही हैं। PM-KISAN, फसल बीमा जैसी योजनाएं जारी हैं।",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ],
        "telugu": [
            NewsItem(
                title="రైతులకు ప్రభుత్వ పథకాల నవీకరణ",
                summary="కేంద్ర మరియు రాష్ట్ర ప్రభుత్వాలు రైతులకు వివిధ పథకాలను అమలు చేస్తున్నాయి. PM-KISAN, పంట బీమా వంటి పథకాలు కొనసాగుతున్నాయి.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ],
        "kannada": [
            NewsItem(
                title="ರೈತರಿಗೆ ಸರ್ಕಾರಿ ಯೋಜನೆಗಳ ನವೀಕರಣ",
                summary="ಕೇಂದ್ರ ಮತ್ತು ರಾಜ್ಯ ಸರ್ಕಾರಗಳು ರೈತರಿಗೆ ವಿವಿಧ ಯೋಜನೆಗಳನ್ನು ಜಾರಿಗೆ ತರುತ್ತಿವೆ. PM-KISAN, ಬೆಳೆ ವಿಮೆ ಮುಂತಾದ ಯೋಜನೆಗಳು ಮುಂದುವರಿಯುತ್ತಿವೆ.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ],
        "malayalam": [
            NewsItem(
                title="കർഷകർക്കുള്ള സർക്കാർ പദ്ധതികളുടെ അപ്‌ഡേറ്റ്",
                summary="കേന്ദ്ര-സംസ്ഥാന സർക്കാരുകൾ കർഷകർക്കായി വിവിധ പദ്ധതികൾ നടപ്പിലാക്കുന്നു. PM-KISAN, വിള ഇൻഷുറൻസ് തുടങ്ങിയ പദ്ധതികൾ തുടരുന്നു.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ],
        "english": [
            NewsItem(
                title="Government Schemes Update for Farmers",
                summary="Central and State governments are implementing various schemes for farmers. PM-KISAN, Crop Insurance and other schemes continue.",
                source="Uzhavan AI",
                date=datetime.now().strftime("%Y-%m-%d")
            )
        ]
    }
    
    return fallback_data.get(language, fallback_data["english"])

# ============== API ENDPOINTS ==============

@router.get("/news", response_model=AgricultureNewsResponse)
async def get_agriculture_news(
    language: str = Query("tamil", description="Target language: tamil, telugu, malayalam, kannada, hindi, english"),
    state: str = Query("tamil_nadu", description="Indian state for localized news")
):
    """
    Get agriculture news for farmers
    
    - Fetches relevant news from News API
    - Filters by state using intelligent keyword matching
    - Translates to selected language
    - Returns farmer-friendly, localized content
    
    Supported Languages: Tamil, Telugu, Malayalam, Kannada, Hindi, English
    """
    
    # Normalize inputs
    language = language.lower().strip()
    state = state.lower().strip().replace(" ", "_")
    
    # Validate language
    valid_languages = ["tamil", "telugu", "malayalam", "kannada", "hindi", "english"]
    if language not in valid_languages:
        language = "tamil"
    
    # Check cache first
    cached = get_cached_news(state, language)
    if cached:
        return AgricultureNewsResponse(
            language=language,
            state=state,
            news_count=len(cached.get("news", [])),
            news=cached.get("news", []),
            cached=True,
            last_updated=cached.get("_cached_at", "")
        )
    
    # Fetch news from API
    articles = await fetch_agriculture_news(state)
    
    if not articles:
        # Return fallback news
        fallback = get_fallback_news(state, language)
        return AgricultureNewsResponse(
            language=language,
            state=state,
            news_count=len(fallback),
            news=fallback,
            cached=False,
            last_updated=datetime.now().isoformat()
        )
    
    # Translate to target language
    translated = await translate_news_batch(articles, language)
    
    # Format response
    news_items = []
    for article in translated:
        news_items.append(NewsItem(
            title=article.get("title", ""),
            title_english=article.get("title_english"),
            summary=article.get("description", "")[:300],
            summary_english=article.get("summary_english"),
            source=article.get("source", "Unknown"),
            date=article.get("date", datetime.now().strftime("%Y-%m-%d")),
            url=article.get("url"),
            image_url=article.get("image_url")
        ))
    
    response_data = {
        "news": [item.model_dump() for item in news_items]
    }
    
    # Cache the response
    set_news_cache(state, language, response_data)
    
    return AgricultureNewsResponse(
        language=language,
        state=state,
        news_count=len(news_items),
        news=news_items,
        cached=False,
        last_updated=datetime.now().isoformat()
    )


# ============== CARD-STYLE NEWS SCHEMAS ==============

class NewsCard(BaseModel):
    title: str
    summary: str
    tag: str  # scheme | MSP | weather | crop | subsidy | alert
    source: str
    date: str  # Today | Yesterday | date
    voice_available: bool = True
    image_url: Optional[str] = None

class NewsCardsResponse(BaseModel):
    screen: str = "news"
    view: str = "card"
    language: str
    state: str
    daily_update: bool = True
    voice_enabled: bool = True
    notification_enabled: bool = True
    cards: List[NewsCard]
    empty_message: Optional[str] = None
    last_updated: str

# ============== TAG DETECTION ==============

def detect_news_tag(title: str, description: str) -> str:
    """
    Detect the category tag for a news article
    Returns: scheme | MSP | weather | crop | subsidy | alert
    """
    content = (title + " " + description).lower()
    
    # Priority order for tag detection
    if any(word in content for word in ["msp", "minimum support price", "support price"]):
        return "MSP"
    if any(word in content for word in ["scheme", "pm kisan", "pm-kisan", "yojana", "pradhan mantri"]):
        return "scheme"
    if any(word in content for word in ["subsidy", "grant", "relief", "compensation", "loan waiver"]):
        return "subsidy"
    if any(word in content for word in ["weather", "rain", "drought", "flood", "cyclone", "monsoon", "storm"]):
        return "weather"
    if any(word in content for word in ["alert", "warning", "emergency", "pest", "disease outbreak"]):
        return "alert"
    # Default to crop
    return "crop"

def format_news_date(date_str: str) -> str:
    """
    Format date as 'Today', 'Yesterday', or date string
    """
    try:
        if not date_str:
            return "Today"
        
        # Parse various date formats
        news_date = None
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
            try:
                news_date = datetime.strptime(date_str[:19], fmt)
                break
            except:
                continue
        
        if not news_date:
            return "Today"
        
        today = datetime.now().date()
        news_day = news_date.date()
        
        if news_day == today:
            return "Today"
        elif news_day == today - timedelta(days=1):
            return "Yesterday"
        else:
            return news_date.strftime("%d %b")
    except:
        return "Today"

# ============== EMPTY STATE MESSAGES ==============

EMPTY_MESSAGES = {
    "tamil": "இன்று புதிய விவசாய செய்திகள் இல்லை",
    "telugu": "ఈ రోజు కొత్త వ్యవసాయ వార్తలు లేవు",
    "malayalam": "ഇന്ന് പുതിയ കാർഷിക വാർത്തകൾ ഇല്ല",
    "kannada": "ಇಂದು ಹೊಸ ಕृषಿ ಸುದ್ದಿಗಳು ಇಲ್ಲ",
    "hindi": "आज कोई नई कृषि समाचार नहीं है",
    "english": "No new agriculture news today"
}


@router.get("/cards", response_model=NewsCardsResponse)
async def get_news_cards(
    language: str = Query("tamil", description="Target language: tamil, telugu, malayalam, kannada, hindi"),
    state: str = Query("tamil_nadu", description="Indian state for localized news")
):
    """
    Get agriculture news in CARD format for mobile display
    
    Features:
    - Card-style layout with title, summary, tag
    - Voice button support (text-to-speech ready)
    - Daily updates with Today/Yesterday labels
    - Farmer-friendly, translated content
    
    Tags: scheme | MSP | weather | crop | subsidy | alert
    """
    
    # Normalize inputs
    language = language.lower().strip()
    state = state.lower().strip().replace(" ", "_")
    
    # Validate language
    valid_languages = ["tamil", "telugu", "malayalam", "kannada", "hindi", "english"]
    if language not in valid_languages:
        language = "tamil"
    
    # Check cache first
    cache_key = f"cards_{state}_{language}"
    if cache_key in _news_cache:
        cached = _news_cache[cache_key]
        cached_time = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_time < timedelta(minutes=CACHE_TTL_MINUTES):
            return NewsCardsResponse(**cached["response"])
    
    # Fetch news from API
    articles = await fetch_agriculture_news(state)
    
    if not articles:
        # Return empty state with message
        return NewsCardsResponse(
            language=language,
            state=state,
            cards=[],
            empty_message=EMPTY_MESSAGES.get(language, EMPTY_MESSAGES["english"]),
            last_updated=datetime.now().isoformat()
        )
    
    # Translate to target language
    translated = await translate_news_batch(articles, language)
    
    # Format as cards
    cards = []
    for article in translated[:8]:  # Max 8 cards for mobile
        title = article.get("title", "")
        summary = article.get("description", "")
        
        # Shorten title if too long (1 line max)
        if len(title) > 60:
            title = title[:57] + "..."
        
        # Shorten summary (2-3 lines max)
        if len(summary) > 150:
            summary = summary[:147] + "..."
        
        cards.append(NewsCard(
            title=title,
            summary=summary,
            tag=detect_news_tag(
                article.get("title_english", article.get("title", "")),
                article.get("summary_english", article.get("description", ""))
            ),
            source=article.get("source", "Unknown"),
            date=format_news_date(article.get("date", "")),
            voice_available=True,
            image_url=article.get("image_url")
        ))
    
    response = NewsCardsResponse(
        language=language,
        state=state,
        cards=cards,
        empty_message=None if cards else EMPTY_MESSAGES.get(language, EMPTY_MESSAGES["english"]),
        last_updated=datetime.now().isoformat()
    )
    
    # Cache the response
    _news_cache[cache_key] = {
        "_cached_at": datetime.now().isoformat(),
        "response": response.model_dump()
    }
    
    return response


@router.get("/notification")
async def get_daily_notification(
    language: str = Query("tamil", description="Target language"),
    state: str = Query("tamil_nadu", description="Indian state")
):
    """
    Get the most important news for daily notification
    
    Returns the top priority news card for push notification
    """
    
    # Normalize inputs
    language = language.lower().strip()
    state = state.lower().strip().replace(" ", "_")
    
    # Validate language
    valid_languages = ["tamil", "telugu", "malayalam", "kannada", "hindi", "english"]
    if language not in valid_languages:
        language = "tamil"
    
    # Fetch news
    articles = await fetch_agriculture_news(state)
    
    if not articles:
        # No news notification messages
        notification_messages = {
            "tamil": "இன்றைய விவசாய செய்திகளை பார்க்க கிளிக் செய்யவும்",
            "telugu": "ఈ రోజు వ్యవసాయ వార్తల కోసం క్లిక్ చేయండి",
            "malayalam": "ഇന്നത്തെ കാർഷിക വാർത്തകൾ കാണാൻ ക്ലിക്ക് ചെയ്യുക",
            "kannada": "ಇಂದಿನ ಕृషಿ ಸುದ್ದಿಗಳನ್ನು ನೋಡಲು ಕ್ಲಿಕ್ ಮಾಡಿ",
            "hindi": "आज की कृषि समाचार देखने के लिए क्लिक करें",
            "english": "Click to view today's agriculture news"
        }
        
        return {
            "has_news": False,
            "notification_title": notification_messages.get(language, notification_messages["english"]),
            "notification_body": None,
            "language": language,
            "state": state
        }
    
    # Get top article and translate
    top_article = articles[0]
    translated = await translate_news_batch([top_article], language)
    
    if translated:
        top = translated[0]
        title = top.get("title", "")
        if len(title) > 50:
            title = title[:47] + "..."
        
        return {
            "has_news": True,
            "notification_title": title,
            "notification_body": top.get("description", "")[:100],
            "tag": detect_news_tag(
                top.get("title_english", ""),
                top.get("summary_english", "")
            ),
            "source": top.get("source", ""),
            "language": language,
            "state": state,
            "timestamp": datetime.now().isoformat()
        }
    
    return {
        "has_news": False,
        "notification_title": EMPTY_MESSAGES.get(language, EMPTY_MESSAGES["english"]),
        "notification_body": None,
        "language": language,
        "state": state
    }


@router.get("/states")
async def get_available_states():
    """Get list of available Indian states for news filtering"""
    states = [
        {"id": "all_india", "name": "All India", "name_tamil": "அகில இந்தியா"},
        {"id": "tamil_nadu", "name": "Tamil Nadu", "name_tamil": "தமிழ்நாடு"},
        {"id": "karnataka", "name": "Karnataka", "name_tamil": "கர்நாடகா"},
        {"id": "kerala", "name": "Kerala", "name_tamil": "கேரளா"},
        {"id": "andhra_pradesh", "name": "Andhra Pradesh", "name_tamil": "ஆந்திரப் பிரதேசம்"},
        {"id": "telangana", "name": "Telangana", "name_tamil": "தெலங்கானா"},
        {"id": "maharashtra", "name": "Maharashtra", "name_tamil": "மகாராஷ்டிரா"},
        {"id": "gujarat", "name": "Gujarat", "name_tamil": "குஜராத்"},
        {"id": "rajasthan", "name": "Rajasthan", "name_tamil": "ராஜஸ்தான்"},
        {"id": "madhya_pradesh", "name": "Madhya Pradesh", "name_tamil": "மத்தியப் பிரதேசம்"},
        {"id": "uttar_pradesh", "name": "Uttar Pradesh", "name_tamil": "உத்தரப் பிரதேசம்"},
        {"id": "bihar", "name": "Bihar", "name_tamil": "பீகார்"},
        {"id": "west_bengal", "name": "West Bengal", "name_tamil": "மேற்கு வங்காளம்"},
        {"id": "punjab", "name": "Punjab", "name_tamil": "பஞ்சாப்"},
        {"id": "haryana", "name": "Haryana", "name_tamil": "ஹரியானா"},
        {"id": "odisha", "name": "Odisha", "name_tamil": "ஒடிசா"}
    ]
    return {"states": states, "total": len(states)}


@router.get("/languages")
async def get_supported_languages():
    """Get list of supported languages for news translation"""
    languages = [
        {"id": "tamil", "name": "Tamil", "native_name": "தமிழ்"},
        {"id": "telugu", "name": "Telugu", "native_name": "తెలుగు"},
        {"id": "malayalam", "name": "Malayalam", "native_name": "മലയാളം"},
        {"id": "kannada", "name": "Kannada", "native_name": "ಕನ್ನಡ"},
        {"id": "hindi", "name": "Hindi", "native_name": "हिंदी"},
        {"id": "english", "name": "English", "native_name": "English"}
    ]
    return {"languages": languages, "total": len(languages)}


@router.get("/health")
async def news_health_check():
    """Health check for news service"""
    return {
        "status": "healthy",
        "service": "agriculture-news-intelligence",
        "api_configured": bool(NEWS_API_KEY),
        "cache_size": len(_news_cache)
    }
