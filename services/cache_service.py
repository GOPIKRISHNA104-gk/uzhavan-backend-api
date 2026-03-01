"""
Cache Service - High-performance caching for API responses
Supports in-memory caching with optional SQLite persistence
Designed for low-bandwidth farmer networks

Features:
- In-memory TTL cache for ultra-fast access
- SQLite persistence for cache survival across restarts
- Automatic cache cleanup
- Thread-safe operations
- Category-based TTL (prices: daily, weather: hourly)
"""

import asyncio
import json
import time
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Callable
from functools import wraps
import logging
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

# ============== CACHE CONFIGURATION ==============

class CacheConfig:
    """Cache TTL configurations (in seconds)"""
    
    MANDI_PRICES = 86400         # 24 Hours (User Requirement)
    MANDI_PRICES_TODAY = 86400   # 24 Hours (User Requirement)
    
    # Weather updates frequently - 1 Hour
    WEATHER_CURRENT = 3600       # 1 Hour (User Requirement)
    WEATHER_FORECAST = 3600      # 1 Hour (User Requirement)
    WEATHER_RAIN_ALERT = 3600    # 1 hour
    
    # Static data (commodities list, states, markets)
    STATIC_DATA = 86400          # 24 hours
    
    # Short-lived cache for user-specific data
    USER_DATA = 300              # 5 minutes
    
    # News data
    NEWS = 1800                  # 30 minutes


# ============== IN-MEMORY CACHE ==============

class InMemoryCache:
    """
    Fast in-memory cache with TTL support
    Thread-safe for async operations
    """
    
    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, tuple] = {}  # key -> (value, expiry_time)
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache if exists and not expired"""
        async with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if time.time() < expiry:
                    self._hits += 1
                    logger.debug(f"Cache HIT: {key[:50]}")
                    return value
                else:
                    # Expired, remove it
                    del self._cache[key]
            self._misses += 1
            return None
    
    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Set value in cache with TTL (seconds)"""
        async with self._lock:
            # Evict oldest entries if cache is full
            if len(self._cache) >= self._max_size:
                await self._evict_oldest()
            
            expiry = time.time() + ttl
            self._cache[key] = (value, expiry)
            logger.debug(f"Cache SET: {key[:50]} (TTL: {ttl}s)")
    
    async def delete(self, key: str) -> bool:
        """Delete a specific key from cache"""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self, pattern: Optional[str] = None) -> int:
        """Clear cache entries matching pattern, or all if no pattern"""
        async with self._lock:
            if pattern is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            else:
                keys_to_remove = [k for k in self._cache.keys() if pattern in k]
                for key in keys_to_remove:
                    del self._cache[key]
                return len(keys_to_remove)
    
    async def _evict_oldest(self) -> None:
        """Remove oldest 10% of cache entries"""
        if not self._cache:
            return
        
        # Sort by expiry time and remove oldest 10%
        sorted_keys = sorted(
            self._cache.keys(),
            key=lambda k: self._cache[k][1]
        )
        count_to_remove = max(1, len(sorted_keys) // 10)
        for key in sorted_keys[:count_to_remove]:
            del self._cache[key]
    
    async def cleanup_expired(self) -> int:
        """Remove all expired entries (call periodically)"""
        async with self._lock:
            now = time.time()
            expired_keys = [
                k for k, (_, expiry) in self._cache.items()
                if expiry <= now
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%"
        }


# ============== PERSISTENT CACHE (SQLite) ==============

class PersistentCache:
    """
    SQLite-based persistent cache for fallback data
    Used when network requests fail
    """
    
    def __init__(self, db_path: str = "cache.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    category TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_expiry 
                ON cache(expires_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_category 
                ON cache(category)
            """)
            conn.commit()
    
    async def get(self, key: str, allow_expired: bool = False) -> Optional[Any]:
        """
        Get value from persistent cache (Async wrapper)
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_sync, key, allow_expired)

    def _get_sync(self, key: str, allow_expired: bool) -> Optional[Any]:
        """Synchronous implementation of get"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT value, expires_at FROM cache WHERE key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                if row:
                    value, expires_at = row
                    if allow_expired or time.time() < expires_at:
                        return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Persistent cache get error: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int, category: str = "general") -> None:
        """Set value in persistent cache (Async wrapper)"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._set_sync, key, value, ttl, category)

    def _set_sync(self, key: str, value: Any, ttl: int, category: str) -> None:
        """Synchronous implementation of set"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                now = time.time()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache (key, value, category, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, json.dumps(value), category, now, now + ttl)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Persistent cache set error: {e}")
    
    async def cleanup(self) -> int:
        """Remove expired entries (Async wrapper)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._cleanup_sync)

    def _cleanup_sync(self) -> int:
        """Synchronous implementation of cleanup"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM cache WHERE expires_at < ?",
                    (time.time(),)
                )
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Persistent cache cleanup error: {e}")
            return 0
    
    async def get_category_data(self, category: str, allow_expired: bool = True) -> Dict[str, Any]:
        """Get all data for a category (Async wrapper)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_category_data_sync, category, allow_expired)

    def _get_category_data_sync(self, category: str, allow_expired: bool) -> Dict[str, Any]:
        """Synchronous implementation"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if allow_expired:
                    cursor = conn.execute(
                        "SELECT key, value FROM cache WHERE category = ?",
                        (category,)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT key, value FROM cache WHERE category = ? AND expires_at > ?",
                        (category, time.time())
                    )
                return {row[0]: json.loads(row[1]) for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Persistent cache category get error: {e}")
            return {}


# ============== CACHE MANAGER ==============

class CacheManager:
    """
    Unified cache manager combining in-memory and persistent caching
    with automatic fallback support
    """
    
    def __init__(self):
        self.memory_cache = InMemoryCache(max_size=2000)
        self.persistent_cache = PersistentCache(db_path="./cache.db")
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start background cleanup task"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("🗄️ Cache Manager started")
    
    async def stop(self) -> None:
        """Stop background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("🗄️ Cache Manager stopped")
    
    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of expired cache entries"""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                memory_cleaned = await self.memory_cache.cleanup_expired()
                persistent_cleaned = await self.persistent_cache.cleanup()
                if memory_cleaned > 0 or persistent_cleaned > 0:
                    logger.info(f"Cache cleanup: {memory_cleaned} memory, {persistent_cleaned} persistent")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
    
    def _generate_key(self, prefix: str, **kwargs) -> str:
        """Generate a deterministic cache key"""
        key_data = json.dumps(kwargs, sort_keys=True)
        key_hash = hashlib.md5(key_data.encode()).hexdigest()[:12]
        return f"{prefix}:{key_hash}"
    
    async def get(
        self,
        key: str,
        use_persistent_fallback: bool = True
    ) -> tuple[Optional[Any], bool]:
        """
        Get value from cache
        Returns: (value, is_stale)
        is_stale indicates if the value came from expired persistent cache
        """
        # Try memory cache first (fastest)
        value = await self.memory_cache.get(key)
        if value is not None:
            return value, False
        
        # Try persistent cache (check if expired)
        if use_persistent_fallback:
            value = await self.persistent_cache.get(key, allow_expired=False)
            if value is not None:
                # Warm the memory cache
                await self.memory_cache.set(key, value, CacheConfig.WEATHER_CURRENT)
                return value, False
            
            # Last resort: expired persistent cache for fallback
            value = await self.persistent_cache.get(key, allow_expired=True)
            if value is not None:
                return value, True  # Stale data
        
        return None, False
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: int,
        category: str = "general",
        persist: bool = True
    ) -> None:
        """Set value in both caches"""
        await self.memory_cache.set(key, value, ttl)
        if persist:
            await self.persistent_cache.set(key, value, ttl, category)
    
    async def invalidate(self, pattern: str) -> int:
        """Invalidate cache entries matching pattern"""
        return await self.memory_cache.clear(pattern)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "memory_cache": self.memory_cache.get_stats(),
            "persistent_cache": "active"
        }


# ============== CACHE DECORATOR ==============

def cached(
    ttl: int,
    category: str = "general",
    key_prefix: str = "",
    use_fallback: bool = True
):
    """
    Decorator for caching async function results
    
    Usage:
        @cached(ttl=CacheConfig.WEATHER_CURRENT, category="weather")
        async def get_weather(location: str):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = f"{key_prefix or func.__name__}:" + hashlib.md5(
                json.dumps({"args": str(args), "kwargs": kwargs}, sort_keys=True).encode()
            ).hexdigest()[:16]
            
            # Try to get from cache
            cached_value, is_stale = await cache_manager.get(cache_key, use_fallback)
            
            if cached_value is not None and not is_stale:
                logger.info(f"🚀 Serving {func.__name__} from cache")
                return cached_value
            
            # Call the actual function
            try:
                result = await func(*args, **kwargs)
                
                # Cache the result
                await cache_manager.set(cache_key, result, ttl, category)
                
                return result
                
            except Exception as e:
                # If we have stale data and the request failed, use it
                if is_stale and cached_value is not None:
                    logger.warning(f"⚠️ Using stale cache for {func.__name__} due to error: {e}")
                    return cached_value
                raise
        
        return wrapper
    return decorator


# ============== SINGLETON INSTANCE ==============

cache_manager = CacheManager()


# ============== HELPER FUNCTIONS ==============

async def warm_cache(db_session) -> None:
    """
    Pre-warm cache with commonly accessed data
    Call this on app startup
    """
    logger.info("🔥 Warming cache...")
    
    # Import here to avoid circular imports
    from services.mandi_service import mandi_service
    
    try:
        # Pre-fetch today's prices for common states
        common_states = ["Tamil Nadu", "Karnataka", "Maharashtra", "Andhra Pradesh"]
        for state in common_states:
            try:
                prices = await mandi_service.get_today_prices(
                    db=db_session,
                    state=state,
                    limit=100
                )
                cache_key = f"prices:today:{state.lower()}"
                await cache_manager.set(
                    cache_key,
                    [p.__dict__ for p in prices if hasattr(p, '__dict__')],
                    CacheConfig.MANDI_PRICES_TODAY,
                    "prices"
                )
            except Exception as e:
                logger.warning(f"Cache warm failed for {state}: {e}")
        
        logger.info("✅ Cache warmed successfully")
    except Exception as e:
        logger.error(f"Cache warm error: {e}")
