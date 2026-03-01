"""
HTTP Client Service - Robust HTTP client with retry, timeout, and fallback
Designed for unreliable network conditions (rural India)

Features:
- Automatic retry with exponential backoff
- Configurable timeouts
- Circuit breaker pattern
- Request deduplication
- Fallback to cached data
"""

import asyncio
import httpx
import time
import logging
from typing import Any, Dict, Optional, Callable, TypeVar
from functools import wraps
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============== CONFIGURATION ==============

class TimeoutConfig:
    """Timeout configurations for different API types"""
    
    # Fast APIs (User-facing external calls) - STRICT 5s timeout
    FAST = httpx.Timeout(
        connect=5.0,
        read=5.0,
        write=5.0,
        pool=5.0
    )
    
    # Standard APIs (Background fetch)
    STANDARD = httpx.Timeout(
        connect=5.0,
        read=10.0,
        write=10.0,
        pool=10.0
    )
    
    # Slow APIs (Heavy processing / Large payloads)
    SLOW = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=10.0,
        pool=10.0
    )


class RetryConfig:
    """Retry configuration"""
    MAX_RETRIES = 2
    INITIAL_DELAY = 0.5  # seconds
    MAX_DELAY = 5.0  # seconds
    EXPONENTIAL_BASE = 2
    RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}


# ============== CIRCUIT BREAKER ==============

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker to prevent overwhelming failing services
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_requests: int = 1
    
    _failures: int = field(default=0, init=False)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _last_failure_time: float = field(default=0, init=False)
    _half_open_count: int = field(default=0, init=False)
    
    def can_request(self) -> bool:
        """Check if request should be allowed"""
        if self._state == CircuitState.CLOSED:
            return True
        
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_count = 0
                return True
            return False
        
        # HALF_OPEN - allow limited requests
        if self._half_open_count < self.half_open_requests:
            self._half_open_count += 1
            return True
        return False
    
    def record_success(self) -> None:
        """Record successful request"""
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info("🟢 Circuit breaker closed - service recovered")
        self._failures = 0
    
    def record_failure(self) -> None:
        """Record failed request"""
        self._failures += 1
        self._last_failure_time = time.time()
        
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("🔴 Circuit breaker reopened - service still failing")
        elif self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(f"🔴 Circuit breaker opened after {self._failures} failures")
    
    @property
    def state(self) -> CircuitState:
        return self._state


# ============== HTTP CLIENT ==============

class RobustHTTPClient:
    """
    Production-ready HTTP client with built-in resilience
    """
    
    def __init__(self):
        self._circuits: Dict[str, CircuitBreaker] = {}
        self._pending_requests: Dict[str, asyncio.Task] = {}
    
    def _get_circuit(self, service_name: str) -> CircuitBreaker:
        """Get or create circuit breaker for a service"""
        if service_name not in self._circuits:
            self._circuits[service_name] = CircuitBreaker()
        return self._circuits[service_name]
    
    async def request(
        self,
        method: str,
        url: str,
        service_name: str = "default",
        timeout: httpx.Timeout = TimeoutConfig.STANDARD,
        max_retries: int = RetryConfig.MAX_RETRIES,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        deduplicate: bool = True
    ) -> httpx.Response:
        """
        Make HTTP request with retry, timeout, and circuit breaker
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            service_name: Name for circuit breaker tracking
            timeout: Request timeout configuration
            max_retries: Maximum retry attempts
            headers: Request headers
            params: Query parameters
            json_data: JSON body data
            deduplicate: Prevent duplicate concurrent requests
        """
        circuit = self._get_circuit(service_name)
        
        # Check circuit breaker
        if not circuit.can_request():
            raise HTTPClientError(
                f"Service {service_name} is temporarily unavailable (circuit open)",
                is_retriable=False
            )
        
        # Request deduplication
        request_key = f"{method}:{url}:{str(params)}"
        if deduplicate and request_key in self._pending_requests:
            logger.debug(f"Deduplicating request: {request_key[:50]}")
            return await self._pending_requests[request_key]
        
        # Create the request task
        task = asyncio.create_task(
            self._execute_with_retry(
                method=method,
                url=url,
                timeout=timeout,
                max_retries=max_retries,
                headers=headers,
                params=params,
                json_data=json_data,
                circuit=circuit
            )
        )
        
        if deduplicate:
            self._pending_requests[request_key] = task
        
        try:
            result = await task
            return result
        finally:
            if deduplicate and request_key in self._pending_requests:
                del self._pending_requests[request_key]
    
    async def _execute_with_retry(
        self,
        method: str,
        url: str,
        timeout: httpx.Timeout,
        max_retries: int,
        headers: Optional[Dict[str, str]],
        params: Optional[Dict[str, Any]],
        json_data: Optional[Dict[str, Any]],
        circuit: CircuitBreaker
    ) -> httpx.Response:
        """Execute request with retry logic"""
        last_error: Optional[Exception] = None
        
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_data
                    )
                    
                    # Check for retriable status codes
                    if response.status_code in RetryConfig.RETRY_STATUS_CODES:
                        if attempt < max_retries:
                            delay = self._calculate_delay(attempt)
                            logger.warning(
                                f"Retrying {url} (attempt {attempt + 1}/{max_retries + 1}) "
                                f"after {response.status_code}, waiting {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                    
                    response.raise_for_status()
                    circuit.record_success()
                    return response
                    
            except httpx.TimeoutException as e:
                last_error = HTTPClientError(f"Request timeout: {url}", is_retriable=True, original=e)
                logger.warning(f"Timeout on attempt {attempt + 1}: {url}")
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code in RetryConfig.RETRY_STATUS_CODES:
                    last_error = HTTPClientError(
                        f"HTTP {e.response.status_code}: {url}",
                        is_retriable=True,
                        original=e
                    )
                else:
                    circuit.record_failure()
                    raise HTTPClientError(
                        f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                        is_retriable=False,
                        original=e
                    )
                    
            except httpx.RequestError as e:
                last_error = HTTPClientError(
                    f"Request failed: {str(e)}",
                    is_retriable=True,
                    original=e
                )
                logger.warning(f"Request error on attempt {attempt + 1}: {e}")
            
            except Exception as e:
                circuit.record_failure()
                raise HTTPClientError(f"Unexpected error: {str(e)}", is_retriable=False, original=e)
            
            # Wait before retry
            if attempt < max_retries:
                delay = self._calculate_delay(attempt)
                await asyncio.sleep(delay)
        
        # All retries exhausted
        circuit.record_failure()
        raise last_error or HTTPClientError("Request failed after retries", is_retriable=False)
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay"""
        delay = RetryConfig.INITIAL_DELAY * (RetryConfig.EXPONENTIAL_BASE ** attempt)
        return min(delay, RetryConfig.MAX_DELAY)
    
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Convenience method for GET requests"""
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Convenience method for POST requests"""
        return await self.request("POST", url, **kwargs)
    
    def get_circuit_status(self) -> Dict[str, str]:
        """Get status of all circuit breakers"""
        return {
            name: circuit.state.value
            for name, circuit in self._circuits.items()
        }


class HTTPClientError(Exception):
    """Custom exception for HTTP client errors"""
    
    def __init__(
        self,
        message: str,
        is_retriable: bool = False,
        original: Optional[Exception] = None
    ):
        super().__init__(message)
        self.is_retriable = is_retriable
        self.original = original


# ============== SINGLETON INSTANCE ==============

http_client = RobustHTTPClient()


# ============== UTILITY FUNCTIONS ==============

async def fetch_with_fallback(
    url: str,
    cache_key: str,
    cache_ttl: int,
    service_name: str = "default",
    timeout: httpx.Timeout = TimeoutConfig.STANDARD,
    params: Optional[Dict[str, Any]] = None
) -> tuple[Dict[str, Any], bool]:
    """
    Fetch data with automatic cache fallback
    
    Returns: (data, is_from_cache)
    """
    from services.cache_service import cache_manager
    
    try:
        response = await http_client.get(
            url,
            service_name=service_name,
            timeout=timeout,
            params=params
        )
        data = response.json()
        
        # Cache successful response
        await cache_manager.set(cache_key, data, cache_ttl, service_name)
        
        return data, False
        
    except HTTPClientError as e:
        logger.warning(f"Fetch failed for {url}: {e}, trying cache fallback")
        
        # Try cache fallback
        cached_data, is_stale = await cache_manager.get(cache_key, use_persistent_fallback=True)
        
        if cached_data is not None:
            logger.info(f"Using {'stale' if is_stale else 'cached'} data for {cache_key}")
            return cached_data, True
        
        # No fallback available
        raise


T = TypeVar('T')


def with_fallback(fallback_value: T):
    """
    Decorator to provide fallback value on failure
    
    Usage:
        @with_fallback({"prices": [], "error": "Service unavailable"})
        async def get_prices():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Function {func.__name__} failed with fallback: {e}")
                return fallback_value
        return wrapper
    return decorator
