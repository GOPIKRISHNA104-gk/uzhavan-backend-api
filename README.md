# Uzhavan AI - FastAPI Backend

A comprehensive FastAPI backend for the Uzhavan AI farming assistant app.

## Features

- 🔐 **Authentication** - JWT-based login/register with phone number
- 💬 **AI Chat** - Gemini-powered farming assistant with multilingual support
- 🌱 **Disease Detection** - Plant disease identification using Gemini Vision
- 📈 **Market Prices** - Crop price tracking with AI recommendations
- 🌤️ **Weather** - Weather data with farming advisories
- 🌾 **Crop Recommendations** - AI-powered crop suggestions
- 📞 **Voice Assistant** - Conversational AI for phone calls

## Setup

### 1. Create Virtual Environment

```bash
cd backend
python -m venv venv

# Windows
.\venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Required:
- `GEMINI_API_KEY` - Get from [Google AI Studio](https://aistudio.google.com/)

Optional:
- `WEATHER_API_KEY` - Get from [OpenWeatherMap](https://openweathermap.org/api)

### 4. Run the Server

```bash
python main.py
```

Or with uvicorn directly:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## API Endpoints

### Authentication
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login with phone/password
- `GET /api/auth/me` - Get current user

### Chat
- `POST /api/chat/send` - Send message to AI
- `GET /api/chat/history` - Get chat history

### Disease Detection
- `POST /api/disease/predict` - Analyze plant image
- `GET /api/disease/history` - Get prediction history

### Market
- `POST /api/market/prices` - Get crop prices
- `GET /api/market/crops` - List available crops
- `GET /api/market/trending` - Get trending crops

### Weather
- `POST /api/weather/current` - Get current weather
- `GET /api/weather/alerts` - Get weather alerts

### Crop Recommendations
- `POST /api/crop/recommend` - Get crop suggestions
- `GET /api/crop/soil-types` - List soil types
- `GET /api/crop/seasons` - List farming seasons

### Voice Call
- `POST /api/call/query` - Process voice query
- `GET /api/call/supported-languages` - List languages

## Database

The app uses SQLite by default (file: `uzhavan.db`). Tables are created automatically on first run.

## Supported Languages

- English
- Tamil (தமிழ்)
- Hindi (हिंदी)
- Telugu (తెలుగు)
- Kannada (ಕನ್ನಡ)
- Malayalam (മലയാളം)
