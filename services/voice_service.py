import os
import google.generativeai as genai
from services.mandi_service import mandi_service
from services.weather_service import weather_service
from sqlalchemy.ext.asyncio import AsyncSession
from gtts import gTTS
import base64
import json
import tempfile
import asyncio
from typing import Dict, Any, Optional

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)

# Language Code Mapping for gTTS
LANG_MAP = {
    "tamil": "ta",
    "telugu": "te",
    "hindi": "hi",
    "kannada": "kn",
    "malayalam": "ml",
    "english": "en",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu"
}

class VoiceService:
    def __init__(self):
        self.model = genai.GenerativeModel("gemini-1.5-flash-latest")

    async def process_voice_query(self, audio_file_path: str, db: AsyncSession) -> Dict[str, Any]:
        """
        Process user voice query optimized for performance:
        1. STT + Intent Extraction (Gemini JSON Mode)
        2. Fetch Data (Mandi/Weather Cached)
        3. Generate Response (Gemini)
        4. TTS (gTTS)
        """
        
        # Step 1: Upload audio to Gemini for understanding
        try:
            # We must wrap blocking file upload in a thread logic
            sample_file = await asyncio.to_thread(
                genai.upload_file, path=audio_file_path, display_name="User Query"
            )
            
            # Prompt for intent understanding with JSON Schema enforcement
            intent_prompt = """
            Listen to the user's voice query.
            Return JSON with these keys:
            - language: detected language (e.g., 'tamil', 'english')
            - intent: 'market_price', 'weather', or 'general'
            - crop: extracted crop name (e.g., tomato) or null
            - location: extracted location (e.g., chennai) or 'Tamil Nadu' if missing
            """
            
            # Generate content (blocking -> to_thread)
            response = await asyncio.to_thread(
                self.model.generate_content, 
                [sample_file, intent_prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            
            parsed_response = json.loads(response.text)
            
            language = parsed_response.get("language", "english").lower()
            intent = parsed_response.get("intent", "general")
            crop = parsed_response.get("crop")
            location = parsed_response.get("location", "Tamil Nadu")
            
            # Step 2: Fetch Data (Optimized via Cache/Indexes)
            system_context = ""
            
            if intent == "market_price":
                # Try to search by state first (assuming location is state or district)
                prices = await mandi_service.get_today_prices(
                    db=db, 
                    state=location, 
                    commodity=crop, 
                    limit=5
                )
                
                if not prices:
                     prices = await mandi_service.get_today_prices(
                        db=db, 
                        district=location, 
                        commodity=crop, 
                        limit=5
                    )
                
                if not prices:
                     prices = await mandi_service.get_today_prices(
                        db=db, 
                        market=location, 
                        commodity=crop, 
                        limit=5
                    )

                if prices:
                    price_list = [
                        {
                            "commodity": p.commodity,
                            "variety": p.variety,
                            "market": p.market,
                            "modal_price": p.modal_price,
                            "date": p.arrival_date
                        } for p in prices
                    ]
                    system_context = f"Market Data for {crop} in {location}: {json.dumps(price_list, default=str)}"
                else:
                    system_context = f"No market data found for {crop} in {location} today."
                    
            elif intent == "weather":
                # Use cached weather service
                weather_data = await weather_service.get_weather_for_location(db, location)
                
                if weather_data:
                    current = weather_data.get("current", {})
                    daily = weather_data.get("daily", {})
                    weather_summary = {
                        "current_temp": current.get("temperature_2m"),
                        "rain_chance_max": daily.get("precipitation_probability_max", [])[:3],
                        "rain_sum": daily.get("precipitation_sum", [])[:3]
                    }
                    system_context = f"Weather Data for {location}: {json.dumps(weather_summary)}"
                else:
                    system_context = f"Could not find weather for {location}."

            # Step 3: Generate Answer
            answer_prompt = f"""
            You are Uzhavan AI, a friendly backend voice assistant for farmers.
            User asked a question in {language} about {intent}.
            Context Data: {system_context}
            
            Instructions:
            1. Answer specifically in {language}.
            2. Use a calm, respectful tone.
            3. Use the provided data. If data is a list of prices, summarize the modal price for the top market.
            4. If asking about rain, mention the forecast for the next 3 days.
            5. Keep it short (max 2-3 sentences) suitable for audio playback.
            6. Output ONLY the answer text.
            """
            
            final_response = await asyncio.to_thread(
                self.model.generate_content, answer_prompt
            )
            answer_text = final_response.text
            
            # Step 4: TTS
            audio_base64 = await asyncio.to_thread(
                self._generate_tts, answer_text, language
            )
            
            return {
                "text": answer_text,
                "audio": audio_base64,
                "language": language,
                "intent": intent, 
                "data_context": system_context
            }

        except Exception as e:
            print(f"Voice Processing Error: {e}")
            return {"error": str(e)}

    def _generate_tts(self, text: str, language: str) -> str:
        lang_code = LANG_MAP.get(language.lower(), 'en')
        try:
            # gTTS with safe file handling
            fp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            fp.close() # Close immediately so gTTS can open it
            
            temp_path = fp.name
            
            try:
                tts = gTTS(text=text, lang=lang_code, slow=False)
                tts.save(temp_path)
                
                with open(temp_path, "rb") as f:
                    audio_bytes = f.read()
                    
                return base64.b64encode(audio_bytes).decode('utf-8')
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                        
        except Exception as e:
            print(f"TTS Error: {e}")
            return ""

voice_service = VoiceService()
