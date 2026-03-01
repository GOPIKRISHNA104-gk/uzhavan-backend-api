# FastAPI Performance Optimization Guide
## Uzhavan AI Backend - Production-Ready Optimization

### 🔍 ROOT CAUSE ANALYSIS

#### Why "Failed to fetch" Errors Occur:

1. **Network Timeouts**
   - data.gov.in API can be slow (5-30 seconds response time)
   - Open-Meteo occasionally faces latency spikes
   - Default timeouts too aggressive for slow networks

2. **No Retry Mechanism**
   - Single API failure = immediate error to user
   - Transient failures not handled

3. **No Cache/Fallback**
   - Every request hits external API
   - No data available when API fails

4. **CORS Issues**
   - Frontend fetch failures due to misconfigured CORS

5. **Concurrent Request Overload**
   - Multiple users hitting same API = rate limiting

---

### ✅ IMPLEMENTED SOLUTIONS

#### 1. Robust HTTP Client (`services/http_client.py`)

```python
# Features:
- Automatic retry with exponential backoff (max 2 retries)
- Configurable timeouts (FAST: 10s, STANDARD: 30s, SLOW: 60s)
- Circuit breaker pattern (prevents overwhelming failing services)
- Request deduplication (prevents duplicate concurrent requests)

# Usage:
from services.http_client import http_client, TimeoutConfig

response = await http_client.get(
    url,
    service_name="data_gov_in",
    timeout=TimeoutConfig.STANDARD,
    max_retries=2
)
```

#### 2. Multi-Layer Caching (`services/cache_service.py`)

```python
# Cache Strategy:
- In-memory cache: Ultra-fast (microseconds)
- SQLite persistent cache: Survives restarts
- Automatic fallback: Stale data > no data

# TTL Configuration:
MANDI_PRICES = 4 hours      # Prices don't change intraday
MANDI_PRICES_TODAY = 1 hour # Today's prices
WEATHER_CURRENT = 10 min    # Current weather
WEATHER_FORECAST = 30 min   # Weather forecast
RAIN_ALERT = 1 hour         # Rain alerts
STATIC_DATA = 24 hours      # Commodities list, states, markets

# Usage:
from services.cache_service import cache_manager, CacheConfig

# Store
await cache_manager.set(key, value, CacheConfig.WEATHER_FORECAST)

# Retrieve (with stale fallback)
cached_data, is_stale = await cache_manager.get(key, use_persistent_fallback=True)
```

#### 3. Timeout Configuration

```
External API Timeouts:
┌──────────────────────┬─────────┬──────────┐
│ Service              │ Connect │ Read     │
├──────────────────────┼─────────┼──────────┤
│ Open-Meteo (Weather) │ 5s      │ 10s      │
│ data.gov.in (Prices) │ 10s     │ 30s      │
│ Firebase Auth        │ 10s     │ 20s      │
│ Gemini AI            │ 15s     │ 60s      │
└──────────────────────┴─────────┴──────────┘
```

#### 4. Circuit Breaker Pattern

```
Circuit States:
CLOSED → Normal operation
OPEN   → Service failing, reject requests for 30s
HALF_OPEN → Testing if service recovered

Thresholds:
- 5 consecutive failures → Open circuit
- 30s recovery timeout → Try again
- 1 successful request → Close circuit
```

---

### 📊 OPTIMIZED FLOW DIAGRAM

```
┌─────────────┐    Request    ┌──────────────────┐
│   Frontend  │ ─────────────→│   FastAPI        │
│  (Mobile)   │               │   Backend        │
└─────────────┘               └────────┬─────────┘
                                       │
                                       ▼
                              ┌────────────────────┐
                              │  Check Memory Cache │
                              │    (Microseconds)   │
                              └────────┬───────────┘
                                       │
                          ┌────────────┴────────────┐
                          │                         │
                     CACHE HIT                  CACHE MISS
                          │                         │
                          ▼                         ▼
                  ┌───────────────┐       ┌─────────────────┐
                  │ Return Cached │       │ Check Circuit   │
                  │    Response   │       │    Breaker      │
                  └───────────────┘       └────────┬────────┘
                                                   │
                                    ┌──────────────┴──────────────┐
                                    │                             │
                              CIRCUIT OPEN                  CIRCUIT CLOSED
                                    │                             │
                                    ▼                             ▼
                          ┌─────────────────┐          ┌─────────────────────┐
                          │ Return Stale    │          │  HTTP Request       │
                          │ Cache (Fallback)│          │  + Retry Logic      │
                          └─────────────────┘          │  (Max 2 retries)    │
                                                       └──────────┬──────────┘
                                                                  │
                                                   ┌──────────────┴──────────────┐
                                                   │                             │
                                               SUCCESS                       FAILURE
                                                   │                             │
                                                   ▼                             ▼
                                          ┌───────────────────┐      ┌─────────────────┐
                                          │ Cache Response    │      │ Return Stale    │
                                          │ Return to Client  │      │ Cache + Log     │
                                          └───────────────────┘      └─────────────────┘
```

---

### 🚀 EXPECTED PERFORMANCE

| Scenario | Before | After |
|----------|--------|-------|
| First Request (Cold) | 3-10s | 2-5s |
| Repeated Request (Cached) | 3-10s | <10ms |
| API Failure | Error shown | Stale data served |
| Concurrent Requests | All hit API | Deduplicated |
| Slow Network | Timeout error | Cached fallback |

---

### 📁 FILES MODIFIED/CREATED

```
backend/
├── services/
│   ├── cache_service.py      # NEW: Multi-layer caching
│   ├── http_client.py        # NEW: Robust HTTP client
│   ├── weather_service.py    # REFACTORED: Service layer for weather
│   ├── mandi_service.py      # UPDATED: Uses new services
│   └── __init__.py           # UPDATED: Exports new services
├── routers/
│   └── openmeteo_weather.py  # UPDATED: Uses WeatherService
├── main.py                    # UPDATED: Cache startup & Gzip
└── PERFORMANCE_OPTIMIZATION.md  # This file
```

#### 5. Payload Optimization

- **Gzip Compression**: Enabled globally in `main.py` (threshold: 1KB).
- **Reduced API Responses**: External APIs are filtered to return only essential fields.
- **Efficient JSON**: Pydantic models ensure clean, minimal JSON output.

---

---

### 🔧 MONITORING

**Health Check Endpoint:**
```bash
GET /health
{
  "status": "healthy",
  "service": "uzhavan-ai-backend",
  "scheduler": "running",
  "cache_enabled": true
}
```

**Performance Metrics:**
```bash
GET /performance
{
  "cache_enabled": true,
  "cache_stats": {
    "size": 45,
    "max_size": 2000,
    "hits": 1234,
    "misses": 56,
    "hit_rate": "95.7%"
  },
  "circuit_breakers": {
    "data_gov_in": "closed",
    "open_meteo": "closed"
  }
}
```

---

### 🐛 TROUBLESHOOTING

**If "Failed to fetch" still occurs:**

1. Check circuit breaker status at `/performance`
2. If circuit is OPEN, wait 30 seconds for recovery
3. Check network connectivity to external APIs
4. Verify cache.db file exists and is writable

**If cache not working:**

1. Verify `cache.db` file exists in backend directory
2. Check logs for cache errors
3. Restart backend to reinitialize cache

**If response still slow:**

1. Check if request is hitting cache (logs show "🚀 Serving from cache")
2. Verify external API response times
3. Consider increasing cache TTL

---

### 📋 BEST PRACTICES FOR NEW ENDPOINTS

```python
from services.cache_service import cache_manager, CacheConfig
from services.http_client import http_client, TimeoutConfig

@router.get("/my-endpoint")
async def my_endpoint():
    cache_key = "my_endpoint:unique_key"
    
    # 1. Check cache first
    cached_data, is_stale = await cache_manager.get(cache_key)
    if cached_data and not is_stale:
        return cached_data
    
    # 2. Make API call with retry
    try:
        response = await http_client.get(
            url,
            service_name="my_service",
            timeout=TimeoutConfig.STANDARD,
            max_retries=2
        )
        data = response.json()
        
        # 3. Cache the response
        await cache_manager.set(cache_key, data, CacheConfig.WEATHER_FORECAST)
        return data
        
    except HTTPClientError:
        # 4. Fallback to stale cache
        if cached_data:
            return cached_data
        raise HTTPException(502, "Service unavailable")
```

---

### ✨ SUMMARY

The optimization provides:
- ✅ **Fast responses** (<10ms for cached data)
- ✅ **No more "Failed to fetch"** (fallback to cached data)
- ✅ **Reliable API** (retry + circuit breaker)
- ✅ **Works on slow networks** (appropriate timeouts)
- ✅ **Production-ready** (monitoring, logging, graceful degradation)
