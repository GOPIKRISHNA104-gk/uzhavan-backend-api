"""
Multilingual Message Generator — Uzhavan AI WhatsApp Alerts
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generates farmer-friendly WhatsApp messages in 6 Indian languages.

Supported languages:
  ta — Tamil
  en — English
  hi — Hindi
  te — Telugu
  kn — Kannada
  ml — Malayalam

Each message contains:
  - Today's crop price (modal price from Agmark/data.gov.in)
  - Weather summary (temp, condition, rain alert)
  - Farming tip (advisory)
  - Motivational closing line
"""

from typing import Optional
from datetime import datetime


# ─── Weather Code → Emoji Map ─────────────────────────────────────────────────

_WEATHER_EMOJI = {
    "Clear sky":               "☀️",
    "Mainly clear":            "🌤",
    "Partly cloudy":           "⛅",
    "Overcast":                "☁️",
    "Foggy":                   "🌫",
    "Light drizzle":           "🌦",
    "Moderate drizzle":        "🌧",
    "Dense drizzle":           "🌧",
    "Slight rain":             "🌧",
    "Moderate rain":           "🌧",
    "Heavy rain":              "⛈️",
    "Slight rain showers":     "🌦",
    "Moderate rain showers":   "🌧",
    "Violent rain showers":    "⛈️",
    "Thunderstorm":            "⛈️",
}


def _weather_emoji(description: str) -> str:
    for key, emoji in _WEATHER_EMOJI.items():
        if key.lower() in description.lower():
            return emoji
    return "🌤"


# ─── Message Templates ────────────────────────────────────────────────────────

def generate_message(
    language: str,
    crop: str,
    district: str,
    modal_price: Optional[float],
    min_price: Optional[float],
    max_price: Optional[float],
    weather_desc: str,
    temperature: float,
    rain_alert: str,
    farming_advisory: str,
    date_str: Optional[str] = None,
) -> str:
    """
    Generate a complete farmer WhatsApp alert message.

    Args:
        language:         'ta', 'en', 'hi', 'te', 'kn', 'ml'
        crop:             Crop name (e.g. 'Tomato', 'Rice')
        district:         Farmer's district
        modal_price:      Today's modal market price (Rs/quintal)
        min_price:        Min price
        max_price:        Max price
        weather_desc:     Weather description string
        temperature:      Current temperature in Celsius
        rain_alert:       Rain alert string
        farming_advisory: Short advisory sentence
        date_str:         Date string override (default: today)

    Returns:
        Formatted WhatsApp message string
    """
    date = date_str or datetime.now().strftime("%d %b %Y")
    emoji = _weather_emoji(weather_desc)

    # Handle missing price gracefully
    if modal_price is not None:
        price_str   = f"₹{modal_price:,.0f}"
        min_str     = f"₹{min_price:,.0f}" if min_price else "N/A"
        max_str     = f"₹{max_price:,.0f}" if max_price else "N/A"
    else:
        price_str = min_str = max_str = "N/A"

    lang = language.lower().strip()

    templates = {

        # ── Tamil ─────────────────────────────────────────────────────────────
        "ta": f"""🌾 *உழவன் AI — தினசரி வேளாண் அறிவிப்பு*
📅 {date} | 📍 {district}

🌾 *இன்றைய {crop} விலை*
• சராசரி விலை : {price_str} / குவிண்டால்
• குறைந்தபட்சம் : {min_str}
• அதிகபட்சம்   : {max_str}

{emoji} *வானிலை*
• நிலை        : {weather_desc}
• வெப்பநிலை   : {temperature:.0f}°C
• மழை எச்சரிக்கை: {rain_alert}

💡 *வேளாண் ஆலோசனை*
{farming_advisory}

_உழவன் AI உங்கள் விவசாயத்திற்காக அர்ப்பணிக்கப்பட்டுள்ளது_ 🙏
_Reply STOP to unsubscribe_""",

        # ── English ───────────────────────────────────────────────────────────
        "en": f"""🌾 *Uzhavan AI — Daily Farm Alert*
📅 {date} | 📍 {district}

🌾 *Today's {crop} Market Price*
• Modal Price : {price_str} / quintal
• Min Price   : {min_str}
• Max Price   : {max_str}

{emoji} *Weather Forecast*
• Condition  : {weather_desc}
• Temperature: {temperature:.0f}°C
• Rain Alert : {rain_alert}

💡 *Farming Advisory*
{farming_advisory}

_Uzhavan AI — Your trusted farming companion_ 🙏
_Reply STOP to unsubscribe_""",

        # ── Hindi ─────────────────────────────────────────────────────────────
        "hi": f"""🌾 *उझवन AI — दैनिक किसान अलर्ट*
📅 {date} | 📍 {district}

🌾 *आज का {crop} बाज़ार भाव*
• सामान्य भाव  : {price_str} / क्विंटल
• न्यूनतम भाव  : {min_str}
• अधिकतम भाव : {max_str}

{emoji} *मौसम पूर्वानुमान*
• स्थिति     : {weather_desc}
• तापमान     : {temperature:.0f}°C
• बारिश चेतावनी: {rain_alert}

💡 *कृषि सलाह*
{farming_advisory}

_उझवन AI — आपका विश्वसनीय कृषि साथी_ 🙏
_Reply STOP to unsubscribe_""",

        # ── Telugu ────────────────────────────────────────────────────────────
        "te": f"""🌾 *ఉజవన్ AI — రోజువారీ రైతు హెచ్చరిక*
📅 {date} | 📍 {district}

🌾 *నేటి {crop} మార్కెట్ ధర*
• సగటు ధర    : {price_str} / క్వింటాల్
• కనిష్ట ధర  : {min_str}
• గరిష్ట ధర  : {max_str}

{emoji} *వాతావరణ సూచన*
• స్థితి      : {weather_desc}
• ఉష్ణోగ్రత   : {temperature:.0f}°C
• వర్షం హెచ్చరిక: {rain_alert}

💡 *వ్యవసాయ సలహా*
{farming_advisory}

_ఉజవన్ AI — మీ విశ్వసనీయ వ్యవసాయ సహచరుడు_ 🙏
_Reply STOP to unsubscribe_""",

        # ── Kannada ───────────────────────────────────────────────────────────
        "kn": f"""🌾 *ಉಝವನ್ AI — ದೈನಂದಿನ ರೈತ ಎಚ್ಚರಿಕೆ*
📅 {date} | 📍 {district}

🌾 *ಇಂದಿನ {crop} ಮಾರುಕಟ್ಟೆ ಬೆಲೆ*
• ಸರಾಸರಿ ಬೆಲೆ  : {price_str} / ಕ್ವಿಂಟಾಲ್
• ಕನಿಷ್ಠ ಬೆಲೆ  : {min_str}
• ಗರಿಷ್ಠ ಬೆಲೆ  : {max_str}

{emoji} *ಹವಾಮಾನ ಮುನ್ಸೂಚನೆ*
• ಸ್ಥಿತಿ       : {weather_desc}
• ತಾಪಮಾನ      : {temperature:.0f}°C
• ಮಳೆ ಎಚ್ಚರಿಕೆ : {rain_alert}

💡 *ಕೃಷಿ ಸಲಹೆ*
{farming_advisory}

_ಉಝವನ್ AI — ನಿಮ್ಮ ವಿಶ್ವಾಸಾರ್ಹ ಕೃಷಿ ಸಂಗಾತಿ_ 🙏
_Reply STOP to unsubscribe_""",

        # ── Malayalam ────────────────────────────────────────────────────────
        "ml": f"""🌾 *ഉഴവൻ AI — ദൈനംദിന കർഷക അറിയിപ്പ്*
📅 {date} | 📍 {district}

🌾 *ഇന്നത്തെ {crop} വിലനിലവാരം*
• ശരാശരി വില  : {price_str} / ക്വിന്റൽ
• ഏറ്റവും കുറഞ്ഞ: {min_str}
• ഏറ്റവും കൂടിയ: {max_str}

{emoji} *കാലാവസ്ഥാ പ്രവചനം*
• അവസ്ഥ      : {weather_desc}
• താപനില     : {temperature:.0f}°C
• മഴ മുന്നറിയിപ്പ്: {rain_alert}

💡 *കൃഷി ഉപദേശം*
{farming_advisory}

_ഉഴവൻ AI — നിങ്ങളുടെ വിശ്വസനീയ കൃഷി സഹചരൻ_ 🙏
_Reply STOP to unsubscribe_""",
    }

    return templates.get(lang, templates["en"])


def generate_no_price_message(
    language: str,
    crop: str,
    district: str,
    weather_desc: str,
    temperature: float,
    rain_alert: str,
    farming_advisory: str,
) -> str:
    """Generate message when price data is unavailable — honest fallback."""
    date = datetime.now().strftime("%d %b %Y")
    emoji = _weather_emoji(weather_desc)

    no_price_note = {
        "ta": f"இன்று {crop} விலை தரவு கிடைக்கவில்லை. நேரில் மண்டியை தொடர்பு கொள்ளவும்.",
        "en": f"Today's {crop} price data is unavailable. Please check your local mandi.",
        "hi": f"आज {crop} का भाव उपलब्ध नहीं है। कृपया स्थानीय मंडी से संपर्क करें।",
        "te": f"నేడు {crop} ధర డేటా అందుబాటులో లేదు. స్థానిక మండిని సంప్రదించండి.",
        "kn": f"ಇಂದು {crop} ಬೆಲೆ ಡೇಟಾ ಲಭ್ಯವಿಲ್ಲ. ಸ್ಥಳೀಯ ಮಂಡಿಯನ್ನು ಸಂಪರ್ಕಿಸಿ.",
        "ml": f"ഇന്ന് {crop} വില ഡേറ്റ ലഭ്യമല്ല. പ്രാദേശിക മണ്ടി ബന്ധപ്പെടുക.",
    }
    note = no_price_note.get(language.lower(), no_price_note["en"])

    return generate_message(
        language=language,
        crop=crop,
        district=district,
        modal_price=None,
        min_price=None,
        max_price=None,
        weather_desc=weather_desc,
        temperature=temperature,
        rain_alert=rain_alert,
        farming_advisory=f"{note}\n{farming_advisory}",
        date_str=date,
    )
