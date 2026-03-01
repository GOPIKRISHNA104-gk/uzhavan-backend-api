"""
Edge Deployment Configuration for Uzhavan AI Voice System
Regional deployment optimized for low-latency voice AI in rural India.
"""

# ─── Redis Cache Configuration ────────────────────────
REDIS_CONFIG = {
    "host": "redis-voice-cache",
    "port": 6379,
    "db": 0,
    "decode_responses": True,
    "socket_timeout": 5,
    "retry_on_timeout": True,
    
    # Cache TTLs
    "ttl": {
        "common_qa": 3600 * 24,      # 24 hours for common farming Q&A
        "market_prices": 3600 * 2,     # 2 hours for market prices
        "weather": 3600,               # 1 hour for weather
        "tts_audio": 3600 * 6,         # 6 hours for TTS audio cache
        "session": 1800,               # 30 min for voice sessions
    },
    
    # Common Q&A patterns to pre-cache
    "common_patterns": {
        "tamil": [
            "தக்காளி விலை என்ன",
            "மழை வருமா",
            "நெல் விலை",
            "பூச்சி மருந்து",
        ],
        "hindi": [
            "टमाटर का भाव",
            "बारिश कब होगी",
            "धान का भाव",
            "कीटनाशक",
        ],
        "english": [
            "tomato price",
            "will it rain",
            "rice price",
            "pest control",
        ],
    }
}

# ─── Edge Node Configuration ─────────────────────────
EDGE_NODES = {
    "south-india-1": {
        "region": "asia-south1",          # Mumbai
        "location": "Chennai",
        "priority": 1,
        "services": ["stt", "tts", "gemini-proxy", "voice-ws"],
    },
    "south-india-2": {
        "region": "asia-south2",          # Delhi (fallback)
        "location": "Hyderabad",
        "priority": 2,
        "services": ["stt", "tts", "gemini-proxy"],
    },
}

# ─── gRPC Service Configuration ──────────────────────
GRPC_CONFIG = {
    "stt_service": {
        "host": "stt-service",
        "port": 50051,
        "max_concurrent_streams": 100,
        "keepalive_time_ms": 30000,
    },
    "tts_service": {
        "host": "tts-service",
        "port": 50052,
        "max_message_size": 10 * 1024 * 1024,  # 10MB for audio
    },
    "emotion_service": {
        "host": "emotion-service",
        "port": 50053,
    },
}
