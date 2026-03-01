"""
Production-Grade Intent Classifier
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Classifies farmer speech into one of 5 intents.

Strategy:
  1. Gemini-based classification (primary — highest accuracy)
  2. Rule-based keyword fallback (secondary — fast, offline-safe)
  3. Confidence gating — if < 0.7, defaults to general_query

Supported Intents:
  - market_price
  - weather
  - disease
  - news
  - general_query

Supports Tamil, Hindi, Telugu, Kannada, Malayalam, English.
"""

import os
import re
import json
import asyncio
import logging
from typing import Dict, Any, Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)


# ─── Intent Definitions ──────────────────────────────────────────────────────

INTENT_MARKET_PRICE  = "market_price"
INTENT_WEATHER       = "weather"
INTENT_DISEASE       = "disease"
INTENT_NEWS          = "news"
INTENT_GENERAL_QUERY = "general_query"

ALL_INTENTS = [INTENT_MARKET_PRICE, INTENT_WEATHER, INTENT_DISEASE, INTENT_NEWS, INTENT_GENERAL_QUERY]

CONFIDENCE_THRESHOLD = 0.70


# ─── Multilingual Keyword Banks ──────────────────────────────────────────────

_KEYWORD_RULES: Dict[str, list] = {
    INTENT_MARKET_PRICE: [
        # English
        "price", "market", "rate", "mandi", "sell", "selling", "cost", "rupee", "rs",
        "profit", "loss", "buy", "purchase", "how much", "bargain", "value",
        # Tamil
        "விலை", "சந்தை", "கட்டணம்", "விற்க", "ரேட்", "எவ்வளவு", "காசு", "ரூபாய்", "மார்ஜின்", "லாபம்", "நஷ்டம்", "வாங்க", "விக்கிரது", "என் சரக்கு", "மூட்டை", "மார்க்கெட்",
        # Hindi
        "दाम", "बाजार", "मंडी", "कीमत", "भाव", "बेचना", "खरीदना", "क्या रेट", "रुपाया", "पैसा", "कितना मिलेगा", "बिक्री", "मुनाफा",
        # Telugu
        "ధర", "మార్కెట్", "అమ్మకం", "రూపాయలు", "రేటు", "ఎంత", "కొనుగోలు", "లాభం", "నష్టం", "డబ్బులు", "మండి",
        # Kannada
        "ಬೆಲೆ", "ಮಾರ್ಕೆಟ್", "ಮಾರಾಟ", "ದರ", "ಎಷ್ಟು", "ರೂಪಾಯಿ", "ಖರೀದಿ", "ಲಾಭ", "ನಷ್ಟ", "ಹಣ", "ಮಂಡಿ",
        # Malayalam
        "വില", "മാർക്കറ്റ്", "ചന്ത", "റേറ്റ്", "എത്ര", "രൂപ", "വിൽക്കുക", "വാങ്ങുക", "ലാഭം", "പണം",
    ],
    INTENT_WEATHER: [
        # English
        "weather", "rain", "temperature", "forecast", "wind", "humidity",
        "cloud", "sunny", "storm", "flood", "drought", "monsoon", "season", "climate", "hot", "cold",
        # Tamil
        "வானிலை", "மழை", "வெப்பம்", "காற்று", "வெயில்", "புயல்", "பனி", "தட்பவெப்ப நிலை", "கிளைமேட்", "தண்ணி வருமா", "காத்து", "மழ", "வெயில் அடிக்குமா",
        # Hindi
        "मौसम", "बारिश", "तापमान", "आँधी", "बाढ़", "वर्षा", "गरमी", "ठंड", "कड़क धुप", "धूप", "तूफान", "क्लाइमेट",
        # Telugu
        "వాతావరణం", "వర్షం", "ఉష్ణోగ్రత", "గాలి", "ఎండ", "తుఫాను", "చలి", "వెచ్చదనం", "వర్షపాతం", "క్లైమేట్",
        # Kannada
        "ಹವಾಮಾನ", "ಮಳೆ", "ತಾಪಮಾನ", "ಗಾಳಿ", "ಬಿಸಿಲು", "ಚಳಿ", "ಚಂಡಮಾರುತ", "ವಾತಾವರಣ", "ಕ್ಲೈಮೇಟ್",
        # Malayalam
        "കാലാവസ്ഥ", "മഴ", "ഊഷ്മാവ്", "കാറ്റ്", "വെയിൽ", "തണുപ്പ്", "ചൂട്", "ക്ലൈമറ്റ്",
    ],
    INTENT_DISEASE: [
        # English
        "disease", "pest", "fungus", "bacteria", "virus", "insect", "worm",
        "crop damage", "leaf", "yellow", "wilt", "blight", "rot", "infection",
        "spray", "pesticide", "fertilizer", "cure", "treatment", "sick", "dying", "bugs", "medicine",
        # Tamil
        "நோய்", "பூச்சி", "பூஞ்சை", "பாக்டீரியா", "இலை", "மருந்து", "உரம்", "புழு", "வாடல்", "அழுகுது", "காய்கறி கெட்டுப்போச்சு", "என்ன அடிக்கலாம்", "ஸ்ப்ரே", "கருகல்",
        # Hindi
        "रोग", "कीट", "फंगस", "बीमारी", "कीटनाशक", "उपचार", "दवाई", "सुंडी", "मुरझाना", "सड़न", "खाद", "स्प्रे", "पत्ता पीला", "कीड़े",
        # Telugu
        "వ్యాధి", "పురుగు", "శిలీంధ్రం", "మందు", "తెగులు", "ఎరువులు", "ఆకు పచ్చ", "కుళ్ళు", "పురుగులు", "స్ప్రే", "వైద్యం",
        # Kannada
        "ರೋಗ", "ಕೀಟ", "ಫಂಗಸ್", "ಔಷಧಿ", "ಗೊಬ್ಬರ", "ಹುಳು", "ಕೊಳೆ ಕೊಳೆ", "ಸಿಂಪಡಣೆ", "ಎಲೆಗಳು", "ಹುಳುಗಳು",
        # Malayalam
        "രോഗം", "കീടം", "ഫംഗസ്", "മരുന്ന്", "വളം", "പുഴു", "ചീയൽ", "തളിക്കുക",
    ],
    INTENT_NEWS: [
        # English
        "news", "update", "government", "scheme", "subsidy", "loan", "pm kisan",
        "agriculture policy", "new policy", "announcement", "latest", "today news", "gov",
        # Tamil
        "செய்தி", "அரசு", "திட்டம்", "மானியம்", "கடன்", "நியூஸ்", "புதுசு", "அரசாங்கம்", "லோன்", "ஸ்கீம்", "மானிய தொகை", "பிஎம் கிசான்",
        # Hindi
        "समाचार", "सरकार", "योजना", "सब्सिडी", "ऋण", "किसान योजना", "खबर", "न्यूज़", "लोन", "सरकारी",
        # Telugu
        "వార్తలు", "ప్రభుత్వం", "పథకం", "సబ్సిడీ", "రుణం", "న్యూస్", "సమాచారం", "లోన్", "కిసాన్",
        # Kannada
        "ಸುದ್ದಿ", "ಸರ್ಕಾರ", "ಯೋಜನೆ", "ಸಬ್ಸಿಡಿ", "ಸಾಲ", "ಮಾಹಿತಿ", "ನ್ಯೂಸ್", "ರೈತರಿಗೆ",
        # Malayalam
        "വാർത്ത", "സർക്കാർ", "പദ്ധതി", "സബ്സിഡി", "ലോൺ", "ന്യൂസ്", "വിവരങ്ങൾ",
    ],
}


# ─── Classifier ─────────────────────────────────────────────────────────────

class IntentClassifier:
    """
    Two-stage intent classifier:
    Stage 1: Gemini (semantic, high accuracy)
    Stage 2: Rule-based keyword fallback (fast, offline-safe)
    """

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel("gemini-2.0-flash")
        else:
            self._model = None
            logger.warning("GEMINI_API_KEY missing — intent classifier using rules only")

    async def classify(
        self,
        transcript: str,
        language: str = "english",
    ) -> Dict[str, Any]:
        """
        Classify the farmer's intent from transcript text.

        Returns:
            {
                "intent": str,
                "confidence": float,
                "crop": Optional[str],
                "location": str,
                "method": "gemini" | "rule_based",
                "raw_transcript": str
            }
        """
        if not transcript or not transcript.strip():
            return self._default_result(transcript)

        # Stage 1: Gemini classification (primary)
        if self._model:
            try:
                result = await self._classify_gemini(transcript, language)
                if result["confidence"] >= CONFIDENCE_THRESHOLD:
                    result["method"] = "gemini"
                    result["raw_transcript"] = transcript
                    logger.info(
                        f"[IntentClassifier] Gemini → {result['intent']} ({result['confidence']:.2f})"
                    )
                    return result
                else:
                    logger.info(
                        f"[IntentClassifier] Gemini confidence too low ({result['confidence']:.2f}), "
                        f"trying rule-based"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[IntentClassifier] Gemini failed: {e} — falling back to rules")

        # Stage 2: Rule-based fallback
        result = self._classify_rules(transcript)
        result["method"] = "rule_based"
        result["raw_transcript"] = transcript
        logger.info(
            f"[IntentClassifier] Rules → {result['intent']} ({result['confidence']:.2f})"
        )
        return result

    # ── Gemini Classification ────────────────────────────────────────────────

    async def _classify_gemini(self, transcript: str, language: str) -> Dict[str, Any]:
        """Use Gemini for high-precision intent classification with rate-limit retry."""
        prompt = f"""You are classifying a farmer's spoken question in {language}.

Farmer said: "{transcript}"

Classify into EXACTLY ONE of these intents:
- market_price: asking about crop prices, mandi rates, selling value
- weather: asking about rain, temperature, forecast, farming season
- disease: asking about crop disease, pest, infection, spray, treatment
- news: asking about government schemes, subsidies, loans, agriculture news
- general_query: anything else (farming tips, advice, greetings, etc.)

Also extract:
- crop: name of crop mentioned (or null if none)
- location: district/state mentioned (default: "Tamil Nadu")
- confidence: your confidence 0.0-1.0 (be honest)

Return ONLY JSON, no markdown:
{{
  "intent": "<one of the 5 intents above>",
  "confidence": <0.0-1.0>,
  "crop": "<crop name or null>",
  "location": "<location or Tamil Nadu>"
}}"""

        last_error = None
        for attempt in range(2):
            try:
                response = await asyncio.to_thread(
                    self._model.generate_content,
                    prompt,
                    generation_config={"response_mime_type": "application/json"},
                )
                data = json.loads(response.text.strip())

                # Validate intent
                intent = data.get("intent", INTENT_GENERAL_QUERY)
                if intent not in ALL_INTENTS:
                    intent = INTENT_GENERAL_QUERY

                return {
                    "intent": intent,
                    "confidence": float(data.get("confidence", 0.5)),
                    "crop": data.get("crop"),
                    "location": data.get("location", "Tamil Nadu"),
                }
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "429" in error_str or "ResourceExhausted" in error_str or "quota" in error_str.lower():
                    logger.warning(f"Intent classifier 429, retry {attempt+1}/2")
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                raise

        raise last_error or Exception("Gemini intent classification failed")

    # ── Rule-Based Classification ────────────────────────────────────────────

    def _classify_rules(self, transcript: str) -> Dict[str, Any]:
        """
        Keyword-based classification.
        Scores each intent then picks the highest.
        """
        text_lower = transcript.lower()

        scores: Dict[str, float] = {intent: 0.0 for intent in ALL_INTENTS}

        for intent, keywords in _KEYWORD_RULES.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    scores[intent] += 1.0

        # Normalize scores
        max_score = max(scores.values()) if scores else 0
        if max_score > 0:
            best_intent = max(scores, key=scores.get)
            confidence = min(scores[best_intent] / (max_score + 1), 0.90)
        else:
            best_intent = INTENT_GENERAL_QUERY
            confidence = 0.55

        crop = _extract_crop_name(text_lower)
        location = _extract_location(text_lower)

        return {
            "intent": best_intent,
            "confidence": round(confidence, 2),
            "crop": crop,
            "location": location,
        }

    def _default_result(self, transcript: str) -> Dict[str, Any]:
        return {
            "intent": INTENT_GENERAL_QUERY,
            "confidence": 0.0,
            "crop": None,
            "location": "Tamil Nadu",
            "method": "default",
            "raw_transcript": transcript,
        }


# ─── Extraction Helpers ───────────────────────────────────────────────────────

_COMMON_CROPS = [
    "rice", "paddy", "wheat", "tomato", "onion", "potato", "cotton", "sugarcane",
    "chilli", "groundnut", "soybean", "maize", "corn", "banana", "mango", "coconut", "brinjal",
    # Tamil
    "நெல்", "தக்காளி", "வெங்காயம்", "உருளைக்கிழங்கு", "பருத்தி", "கரும்பு", "வாழை", "மாம்பழம்", "மிளகாய்", "கத்தரிக்காய்", "சோளம்", "தென்னை", "பயிறு",
    # Hindi
    "धान", "टमाटर", "प्याज", "आलू", "कपास", "गन्ना", "गेहूं", "मक्का", "मिर्च", "बैंगन", "सरसों", "चना",
    # Telugu
    "వరి", "టొమాటో", "ఉల్లిపాయ", "పత్తి", "చెరుకు", "మిరప", "వంకాయ", "మామిడి", "అరటి",
    # Kannada
    "ಭತ್ತ", "ಟೊಮೆಟೊ", "ಈರುಳ್ಳಿ", "ಹತ್ತಿ", "ಕಬ್ಬು", "ಮೆಣಸಿನಕಾಯಿ", "ಬದನೆಕಾಯಿ", "ಮಾವು", "ಬಾಳೆ",
    # Malayalam
    "നെല്ല്", "തക്കാളി", "ഉള്ളി", "പരുത്തി", "കരിമ്പ്", "വാഴ", "മാങ്ങ", "മുളക്", "കായ",
]

_COMMON_LOCATIONS = [
    "tamil nadu", "karnataka", "andhra pradesh", "telangana", "kerala",
    "maharashtra", "madhya pradesh", "rajasthan", "punjab", "haryana",
    "coimbatore", "madurai", "trichy", "salem", "erode",
    "bangalore", "mysore", "hubli", "hyderabad", "vijayawada",
]


def _extract_crop_name(text: str) -> Optional[str]:
    for crop in _COMMON_CROPS:
        if crop in text:
            return crop
    return None


def _extract_location(text: str) -> str:
    for loc in _COMMON_LOCATIONS:
        if loc in text:
            return loc.title()
    return "Tamil Nadu"


# ─── Singleton ────────────────────────────────────────────────────────────────
intent_classifier = IntentClassifier()
