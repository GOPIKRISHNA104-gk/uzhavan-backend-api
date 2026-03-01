
"""
Complete Localization Service for Uzhavan AI
Handles translation of all dynamic content including news, weather, market prices, and AI responses
"""

import os
import logging
from typing import Dict, Optional, List, Any
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Language mapping for Gemini AI
LANGUAGE_NAMES = {
    "tamil": "Tamil",
    "telugu": "Telugu", 
    "malayalam": "Malayalam",
    "kannada": "Kannada",
    "hindi": "Hindi",
    "english": "English"
}

# Language codes for API consistency
LANGUAGE_CODES = {
    "tamil": "ta",
    "telugu": "te",
    "malayalam": "ml", 
    "kannada": "kn",
    "hindi": "hi",
    "english": "en"
}

# Safe fallback messages for "Data Unavailable"
SAFE_ERROR_MESSAGES: Dict[str, str] = {
    "en": "Data temporarily unavailable. Showing last saved data.",
    "ta": "தரவு தற்காலிகமாக கிடைக்கவில்லை. கடைசியாக சேமிக்கப்பட்ட தரவு காட்டப்படுகிறது.",
    "hi": "डेटा अस्थायी रूप से अनुपलब्ध है। अंतिम सहेजा गया डेटा दिखाया जा रहा है।",
    "te": "డేటా తాత్కాలికంగా అందుబాటులో లేదు. చివరిగా సేవ్ చేసిన డేటా చూపబడుతోంది.",
    "ml": "ഡാറ്റ താൽക്കാലികമായി ലഭ്യമല്ല. അവസാനം സേവ് ചെയ്ത ഡാറ്റ കാണിക്കുന്നു.",
    "kn": "ದತ್ತಾಂಶ ತಾತ್ಕಾಲಿಕವಾಗಿ ಲಭ್ಯವಿಲ್ಲ. ಕೊನೆಯದಾಗಿ ಉಳಿಸಿದ ದತ್ತಾಂಶವನ್ನು ತೋರಿಸಲಾಗುತ್ತಿದೆ.",
}

# Weather condition translations
WEATHER_TRANSLATIONS = {
    "clear sky": {
        "ta": "தெளிவான வானம்",
        "hi": "साफ आसमान", 
        "te": "స్పష్టమైన ఆకాశం",
        "ml": "തെളിഞ്ഞ ആകാശം",
        "kn": "ಸ್ಪಷ್ಟ ಆಕಾಶ",
        "en": "clear sky"
    },
    "partly cloudy": {
        "ta": "பகுதி மேகமூட்டம்",
        "hi": "आंशिक बादल",
        "te": "పాక్షిక మేఘావృతం", 
        "ml": "ഭാഗികമായി മേഘാവൃതം",
        "kn": "ಭಾಗಶಃ ಮೇಘಾವೃತ",
        "en": "partly cloudy"
    },
    "cloudy": {
        "ta": "மேகமூட்டம்",
        "hi": "बादल छाए रहेंगे",
        "te": "మేఘావృతం",
        "ml": "മേഘാവൃതം", 
        "kn": "ಮೇಘಾವೃತ",
        "en": "cloudy"
    },
    "rain": {
        "ta": "மழை",
        "hi": "बारिश",
        "te": "వర్షం",
        "ml": "മഴ",
        "kn": "ಮಳೆ", 
        "en": "rain"
    },
    "heavy rain": {
        "ta": "கனமழை",
        "hi": "भारी बारिश",
        "te": "భారీ వర్షం",
        "ml": "കനത്ത മഴ",
        "kn": "ಭಾರೀ ಮಳೆ",
        "en": "heavy rain"
    }
}

# Common crop/commodity translations
CROP_TRANSLATIONS = {
    "tomato": {"ta": "தக்காளி", "hi": "टमाटर", "te": "టమోటా", "ml": "തക്കാളി", "kn": "ಟೊಮೆಟೊ", "en": "tomato"},
    "onion": {"ta": "வெங்காயம்", "hi": "प्याज", "te": "ఉల్లిపాయ", "ml": "ഉള്ളി", "kn": "ಈರುಳ್ಳಿ", "en": "onion"},
    "potato": {"ta": "உருளைக்கிழங்கு", "hi": "आलू", "te": "బంగాళాదుంప", "ml": "ഉരുളക്കിഴങ്ങ്", "kn": "ಆಲೂಗಡ್ಡೆ", "en": "potato"},
    "rice": {"ta": "அரிசி", "hi": "चावल", "te": "బియ్యం", "ml": "അരി", "kn": "ಅಕ್ಕಿ", "en": "rice"},
    "wheat": {"ta": "கோதுமை", "hi": "गेहूं", "te": "గోధుమ", "ml": "ഗോതമ്പ്", "kn": "ಗೋಧಿ", "en": "wheat"}
}

class TranslationService:
    """
    Centralized translation service using Gemini AI
    Handles all dynamic content translation with fallbacks
    """
    
    def __init__(self):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self._translation_cache = {}
        
    async def translate_text(self, text: str, target_language: str, content_type: str = "general") -> str:
        """
        Translate text to target language using Gemini AI
        
        Args:
            text: Text to translate
            target_language: Target language (tamil, hindi, etc.)
            content_type: Type of content (news, weather, market, etc.)
        """
        if not text or target_language == "english":
            return text
            
        # Check cache first
        cache_key = f"{text[:50]}_{target_language}_{content_type}"
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]
            
        try:
            import google.generativeai as genai
            
            if not self.gemini_api_key:
                logger.warning("Gemini API key not found, returning original text")
                return text
                
            genai.configure(api_key=self.gemini_api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            lang_name = LANGUAGE_NAMES.get(target_language, "Tamil")
            
            # Content-specific prompts for better translation
            if content_type == "news":
                prompt = f"""Translate this agriculture news to {lang_name} for Indian farmers.
                
RULES:
1. Use simple, everyday language suitable for farmers
2. Keep technical terms in local language where possible
3. Preserve important details like prices, dates, scheme names
4. Make it natural and easy to understand
5. Return ONLY the translated text, nothing else

Text: {text}"""
            
            elif content_type == "weather":
                prompt = f"""Translate this weather description to {lang_name} for farmers.
                
RULES:
1. Use simple weather terms farmers understand
2. Keep it concise and clear
3. Return ONLY the translated text, nothing else

Text: {text}"""
            
            elif content_type == "market":
                prompt = f"""Translate this market/price information to {lang_name} for farmers.
                
RULES:
1. Keep crop names in local language
2. Preserve all numbers and prices exactly
3. Use simple market terminology
4. Return ONLY the translated text, nothing else

Text: {text}"""
            
            else:
                prompt = f"""Translate to {lang_name} using simple language for farmers.
                
RULES:
1. Use everyday language
2. Keep it natural and clear
3. Return ONLY the translated text, nothing else

Text: {text}"""
            
            response = model.generate_content(prompt)
            translated_text = response.text.strip() if response.text else text
            
            # Cache the translation
            self._translation_cache[cache_key] = translated_text
            
            return translated_text
            
        except Exception as e:
            logger.error(f"Translation failed for {target_language}: {e}")
            return text
    
    async def translate_weather_condition(self, condition: str, target_language: str) -> str:
        """Translate weather condition with fallback to predefined translations"""
        lang_code = LANGUAGE_CODES.get(target_language, "en")
        
        # Try predefined translations first
        condition_lower = condition.lower()
        for key, translations in WEATHER_TRANSLATIONS.items():
            if key in condition_lower:
                return translations.get(lang_code, condition)
        
        # Fallback to AI translation
        return await self.translate_text(condition, target_language, "weather")
    
    async def translate_crop_name(self, crop: str, target_language: str) -> str:
        """Translate crop/commodity name with fallback to predefined translations"""
        lang_code = LANGUAGE_CODES.get(target_language, "en")
        
        # Try predefined translations first
        crop_lower = crop.lower()
        for key, translations in CROP_TRANSLATIONS.items():
            if key in crop_lower:
                return translations.get(lang_code, crop)
        
        # Fallback to AI translation
        return await self.translate_text(crop, target_language, "market")
    
    def get_language_instruction(self, language: str) -> str:
        """Get language instruction for AI responses"""
        language_map = {
            "tamil": "Respond strictly in Tamil (தமிழ்). Use simple, spoken Tamil suitable for farmers. Do not use any English words.",
            "hindi": "Respond strictly in Hindi (हिंदी). Use simple, spoken Hindi suitable for farmers. Do not use any English words.",
            "telugu": "Respond strictly in Telugu (తెలుగు). Use simple, spoken Telugu suitable for farmers. Do not use any English words.",
            "kannada": "Respond strictly in Kannada (ಕನ್ನಡ). Use simple, spoken Kannada suitable for farmers. Do not use any English words.",
            "malayalam": "Respond strictly in Malayalam (മലയാളം). Use simple, spoken Malayalam suitable for farmers. Do not use any English words.",
            "english": "Respond in English using simple language suitable for farmers."
        }
        return language_map.get(language.lower(), "Respond in English using simple language suitable for farmers.")

# Global translation service instance
translation_service = TranslationService()

def get_safe_error_message(lang: str = "en") -> str:
    """
    Get localized error message for data unavailability.
    Gracefully handles unknown language codes.
    """
    if not lang:
        return SAFE_ERROR_MESSAGES["en"]
    
    # Normalize language code (e.g., "en-US" -> "en")
    lang_code = lang.split("-")[0].lower()
    
    # Map full language names to codes
    if lang_code in LANGUAGE_NAMES:
        lang_code = LANGUAGE_CODES.get(lang_code, "en")
    
    return SAFE_ERROR_MESSAGES.get(lang_code, SAFE_ERROR_MESSAGES["en"])
