"""
WhatsApp Welcome Message Service — Uzhavan AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sends instant welcome + live data message when farmer registers.

Flow:
  1. Farmer registers via /api/v2/auth/register
  2. Backend saves profile to Firestore
  3. Backend immediately fetches live weather + crop price
  4. Sends personalized WhatsApp message in farmer's language
  5. All in background — registration response is instant

No frontend trigger. Fully backend automated.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── Language Map ────────────────────────────────────────────────────────────

_LANG_MAP = {
    "tamil": "ta", "english": "en", "hindi": "hi",
    "telugu": "te", "kannada": "kn", "malayalam": "ml",
    "ta": "ta", "en": "en", "hi": "hi",
    "te": "te", "kn": "kn", "ml": "ml",
}


def _lang_code(language: str) -> str:
    return _LANG_MAP.get(language.lower().strip(), "en")


# ─── Welcome Message Templates ──────────────────────────────────────────────

def _generate_welcome_message(
    language: str,
    name: str,
    crop: str,
    district: str,
    modal_price: Optional[float],
    weather_desc: str,
    temperature: float,
    rain_alert: str,
) -> str:
    """Generate multilingual welcome + live data message."""
    
    date = datetime.now().strftime("%d %b %Y")
    price_str = f"₹{modal_price:,.0f}" if modal_price else "N/A"
    lang = _lang_code(language)

    templates = {
        # ── Tamil ─────────────────────────────────────────────────────────
        "ta": f"""🌾 *வணக்கம் {name}!* 🙏

உழவன் AI-இல் வரவேற்கிறோம்! 🎉
நீங்கள் வெற்றிகரமாக பதிவு செய்துள்ளீர்கள்.

📋 *உங்கள் விவரங்கள்*
• பயிர்     : {crop}
• மாவட்டம்  : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *இன்றைய நேரடி புதுப்பிப்பு*
📅 {date}

💰 *{crop} விலை*: {price_str} / குவிண்டால்

🌦️ *வானிலை*
• நிலை    : {weather_desc}
• வெப்பநிலை: {temperature:.0f}°C
• மழை     : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 தினமும் காலை 6 மணிக்கு உங்களுக்கான தனிப்பயன் விலை + வானிலை அறிவிப்பு வரும்!

_உழவன் AI — உங்கள் விவசாயத்திற்காக_ 🌱
_Reply STOP to unsubscribe_""",

        # ── English ───────────────────────────────────────────────────────
        "en": f"""🌾 *Welcome {name}!* 🙏

You've successfully registered on Uzhavan AI! 🎉

📋 *Your Profile*
• Crop     : {crop}
• District : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Today's Live Update*
📅 {date}

💰 *{crop} Price*: {price_str} / quintal

🌦️ *Weather*
• Condition  : {weather_desc}
• Temperature: {temperature:.0f}°C
• Rain Alert : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 You'll receive personalized daily updates at 6:00 AM with crop prices + weather!

_Uzhavan AI — Your trusted farming companion_ 🌱
_Reply STOP to unsubscribe_""",

        # ── Hindi ─────────────────────────────────────────────────────────
        "hi": f"""🌾 *नमस्ते {name}!* 🙏

उझवन AI में आपका स्वागत है! 🎉
आपका पंजीकरण सफल हुआ।

📋 *आपकी जानकारी*
• फसल    : {crop}
• जिला   : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *आज का लाइव अपडेट*
📅 {date}

💰 *{crop} भाव*: {price_str} / क्विंटल

🌦️ *मौसम*
• स्थिति   : {weather_desc}
• तापमान   : {temperature:.0f}°C
• बारिश    : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 रोज़ सुबह 6 बजे आपको फसल भाव + मौसम का पर्सनल अपडेट मिलेगा!

_उझवन AI — आपका भरोसेमंद कृषि साथी_ 🌱
_Reply STOP to unsubscribe_""",

        # ── Telugu ────────────────────────────────────────────────────────
        "te": f"""🌾 *స్వాగతం {name}!* 🙏

ఉజవన్ AI లో విజయవంతంగా నమోదు చేయబడింది! 🎉

📋 *మీ వివరాలు*
• పంట    : {crop}
• జిల్లా  : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *నేటి లైవ్ అప్‌డేట్*
📅 {date}

💰 *{crop} ధర*: {price_str} / క్వింటాల్

🌦️ *వాతావరణం*
• స్థితి     : {weather_desc}
• ఉష్ణోగ్రత  : {temperature:.0f}°C
• వర్షం      : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 ప్రతిరోజూ ఉదయం 6 గంటలకు వ్యక్తిగత ధర + వాతావరణ అప్‌డేట్ వస్తుంది!

_ఉజవన్ AI — మీ విశ్వసనీయ వ్యవసాయ సహచరుడు_ 🌱
_Reply STOP to unsubscribe_""",

        # ── Kannada ───────────────────────────────────────────────────────
        "kn": f"""🌾 *ಸ್ವಾಗತ {name}!* 🙏

ಉಝವನ್ AI ನಲ್ಲಿ ಯಶಸ್ವಿಯಾಗಿ ನೋಂದಣಿಯಾಗಿದೆ! 🎉

📋 *ನಿಮ್ಮ ವಿವರಗಳು*
• ಬೆಳೆ    : {crop}
• ಜಿಲ್ಲೆ   : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *ಇಂದಿನ ಲೈವ್ ಅಪ್‌ಡೇಟ್*
📅 {date}

💰 *{crop} ಬೆಲೆ*: {price_str} / ಕ್ವಿಂಟಾಲ್

🌦️ *ಹವಾಮಾನ*
• ಸ್ಥಿತಿ     : {weather_desc}
• ತಾಪಮಾನ    : {temperature:.0f}°C
• ಮಳೆ       : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 ಪ್ರತಿದಿನ ಬೆಳಿಗ್ಗೆ 6 ಗಂಟೆಗೆ ವೈಯಕ್ತಿಕ ಬೆಲೆ + ಹವಾಮಾನ ಅಪ್‌ಡೇಟ್ ಬರುತ್ತದೆ!

_ಉಝವನ್ AI — ನಿಮ್ಮ ವಿಶ್ವಾಸಾರ್ಹ ಕೃಷಿ ಸಂಗಾತಿ_ 🌱
_Reply STOP to unsubscribe_""",

        # ── Malayalam ─────────────────────────────────────────────────────
        "ml": f"""🌾 *സ്വാഗതം {name}!* 🙏

ഉഴവൻ AI-ൽ വിജയകരമായി രജിസ്റ്റർ ചെയ്തു! 🎉

📋 *നിങ്ങളുടെ വിവരങ്ങൾ*
• വിള    : {crop}
• ജില്ല   : {district}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 *ഇന്നത്തെ ലൈവ് അപ്‌ഡേറ്റ്*
📅 {date}

💰 *{crop} വില*: {price_str} / ക്വിന്റൽ

🌦️ *കാലാവസ്ഥ*
• അവസ്ഥ    : {weather_desc}
• താപനില   : {temperature:.0f}°C
• മഴ       : {rain_alert}
━━━━━━━━━━━━━━━━━━━━━━━━

📲 ദിവസവും രാവിലെ 6 മണിക്ക് വ്യക്തിഗത വില + കാലാവസ്ഥ അപ്‌ഡേറ്റ് ലഭിക്കും!

_ഉഴവൻ AI — നിങ്ങളുടെ വിശ്വസനീയ കൃഷി സഹചരൻ_ 🌱
_Reply STOP to unsubscribe_""",
    }

    return templates.get(lang, templates["en"])


# ─── Fetch Live Data ─────────────────────────────────────────────────────────

async def _fetch_crop_price(district: str, crop: str) -> Dict[str, Any]:
    """Fetch latest crop price from mandi database."""
    try:
        from database import async_session
        from services.mandi_service import mandi_service

        async with async_session() as db:
            prices = await asyncio.wait_for(
                mandi_service.get_today_prices(db=db, district=district, commodity=crop, limit=3),
                timeout=5.0,
            )
            if not prices:
                # Try without district filter
                prices = await asyncio.wait_for(
                    mandi_service.get_today_prices(db=db, commodity=crop, limit=3),
                    timeout=5.0,
                )

            if prices:
                p = prices[0]
                return {
                    "modal_price": float(p.modal_price or 0),
                    "found": True,
                }
    except Exception as e:
        logger.warning(f"[Welcome] Crop price fetch failed: {e}")

    return {"modal_price": None, "found": False}


async def _fetch_weather(district: str) -> Dict[str, Any]:
    """Fetch current weather for district."""
    try:
        from services.weather_service import weather_service

        forecast = await asyncio.wait_for(
            weather_service.get_forecast(district),
            timeout=6.0,
        )
        raw = forecast.get("raw_data", {})
        cw = raw.get("current_weather", {})
        daily = raw.get("daily", {})

        temp = float(cw.get("temperature", 30))
        code = int(cw.get("weathercode", 0))
        desc = weather_service._get_weather_description(code)

        rain_sums = (daily.get("precipitation_sum") or [])[1:4]
        total_rain = sum(r for r in rain_sums if r)

        if total_rain == 0:
            rain_alert = "No rain expected"
        elif total_rain < 5:
            rain_alert = f"Light rain possible ({total_rain:.0f}mm)"
        elif total_rain < 20:
            rain_alert = f"Moderate rain expected ({total_rain:.0f}mm)"
        else:
            rain_alert = f"Heavy rain alert ({total_rain:.0f}mm)"

        return {"desc": desc, "temp": temp, "rain": rain_alert, "found": True}

    except Exception as e:
        logger.warning(f"[Welcome] Weather fetch failed for {district}: {e}")
        return {
            "desc": "Partly cloudy", "temp": 30.0,
            "rain": "No data", "found": False,
        }


# ─── Main Welcome Function ──────────────────────────────────────────────────

async def send_welcome_whatsapp(
    phone: str,
    name: str,
    crop: str,
    district: str,
    language: str,
) -> Dict[str, Any]:
    """
    Send instant welcome + live data WhatsApp message.
    
    Called from registration endpoint as a background task.
    Returns delivery result dict.
    """
    from services.whatsapp_service import whatsapp_service

    logger.info(f"[Welcome WA] Sending to {phone} | name={name} crop={crop} lang={language}")

    try:
        # Fetch live data in parallel
        price_data, weather = await asyncio.gather(
            _fetch_crop_price(district or "Tamil Nadu", crop or "General"),
            _fetch_weather(district or "Tamil Nadu"),
        )

        # Generate welcome message
        message = _generate_welcome_message(
            language=language,
            name=name,
            crop=crop or "General",
            district=district or "Tamil Nadu",
            modal_price=price_data.get("modal_price"),
            weather_desc=weather["desc"],
            temperature=weather["temp"],
            rain_alert=weather["rain"],
        )

        # Clean phone number (ensure 91 prefix, no +)
        clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        if len(clean_phone) == 10:
            clean_phone = f"91{clean_phone}"

        # Send WhatsApp
        result = await whatsapp_service.send_text(phone=clean_phone, message=message)

        if result.success:
            logger.info(f"✅ [Welcome WA] Sent to {clean_phone} | msg_id={result.message_id}")
            return {"success": True, "message_id": result.message_id}
        else:
            logger.error(f"❌ [Welcome WA] Failed for {clean_phone}: {result.error}")
            return {"success": False, "error": result.error}

    except Exception as e:
        logger.error(f"❌ [Welcome WA] Error sending to {phone}: {e}")
        return {"success": False, "error": str(e)}
