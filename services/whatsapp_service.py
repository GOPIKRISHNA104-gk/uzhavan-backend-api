"""
WhatsApp Cloud API Service — Uzhavan AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sends farmer alerts via Meta WhatsApp Cloud API.

Features:
  - Async httpx client with connection pooling
  - Retry with exponential back-off (3 attempts)
  - Rate limiter  (80 msg/sec Meta limit)
  - Batch sender (parallel, configurable concurrency)
  - Circuit breaker (5 failures → 60s cooldown)
  - Full delivery log stored in DB
  - Invalid number auto-removal

Env vars required:
  WHATSAPP_TOKEN          — Meta permanent / temporary token
  WHATSAPP_PHONE_ID       — Phone Number ID from Meta Business
"""

import os
import asyncio
import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# ─── Config (read at call time, not import time) ──────────────────────────────

WA_API_VERSION    = "v19.0"
WA_BASE_URL       = f"https://graph.facebook.com/{WA_API_VERSION}"

_RATE_LIMIT_PER_SEC = 60
_BATCH_CONCURRENCY  = 20

def _get_token() -> str:
    return os.getenv("WHATSAPP_TOKEN", "")

def _get_phone_id() -> str:
    return os.getenv("WHATSAPP_PHONE_ID", "")



# ─── Delivery Result ─────────────────────────────────────────────────────────

@dataclass
class DeliveryResult:
    phone: str
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    attempt: int = 1
    sent_at: datetime = field(default_factory=datetime.utcnow)


# ─── Circuit Breaker ─────────────────────────────────────────────────────────

class _CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 60.0):
        self._failures = 0
        self._threshold = threshold
        self._cooldown = cooldown
        self._opened_at: Optional[float] = None
        self._state = "closed"

    def record_success(self):
        self._failures = 0
        self._state = "closed"

    def record_failure(self):
        self._failures += 1
        if self._failures >= self._threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning("[WhatsApp] Circuit OPEN — too many API failures")

    def is_available(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if self._opened_at and (time.monotonic() - self._opened_at) > self._cooldown:
                self._state = "half_open"
                return True
            return False
        return True  # half_open: try one


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

class _RateLimiter:
    """Token-bucket rate limiter."""
    def __init__(self, rate: int):
        self._rate = rate
        self._tokens = float(rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last = now
            if self._tokens < 1:
                sleep_for = (1 - self._tokens) / self._rate
                await asyncio.sleep(sleep_for)
                self._tokens = 0
            else:
                self._tokens -= 1


# ─── WhatsApp Service ─────────────────────────────────────────────────────────

class WhatsAppService:
    """
    Production WhatsApp Cloud API sender.

    Usage:
        result = await whatsapp_service.send_text("+919876543210", "Hello!")
        results = await whatsapp_service.send_batch([(phone, msg), ...])
    """

    def __init__(self):
        self._circuit = _CircuitBreaker(threshold=5, cooldown=60.0)
        self._limiter = _RateLimiter(rate=_RATE_LIMIT_PER_SEC)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_text(
        self,
        phone: str,
        message: str,
        max_retries: int = 3,
    ) -> DeliveryResult:
        """
        Send a plain text WhatsApp message.

        phone: E.164 without '+', e.g. '919876543210'
        message: Text body (max 4096 chars)
        """
        clean_phone = _normalize_phone(phone)
        if not clean_phone:
            return DeliveryResult(phone=phone, success=False, error="Invalid phone number")

        if not _get_token() or not _get_phone_id():
            return DeliveryResult(phone=phone, success=False, error="WhatsApp credentials not configured")

        if not self._circuit.is_available():
            return DeliveryResult(phone=phone, success=False, error="Circuit breaker OPEN — WhatsApp API temporarily unavailable")

        for attempt in range(1, max_retries + 1):
            await self._limiter.acquire()
            try:
                result = await self._call_api(clean_phone, message, attempt)
                if result.success:
                    self._circuit.record_success()
                return result
            except Exception as e:
                logger.warning(f"[WhatsApp] Attempt {attempt}/{max_retries} failed for {clean_phone}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))  # 0.5s, 1s, 2s
                else:
                    self._circuit.record_failure()
                    return DeliveryResult(phone=phone, success=False, error=str(e), attempt=attempt)

        return DeliveryResult(phone=phone, success=False, error="Max retries exceeded")

    async def send_batch(
        self,
        messages: List[tuple],  # [(phone, message), ...]
        concurrency: int = _BATCH_CONCURRENCY,
    ) -> List[DeliveryResult]:
        """
        Send messages to multiple farmers in parallel.
        Respects rate limit and concurrency cap.

        Returns list of DeliveryResult in same order as input.
        """
        sem = asyncio.Semaphore(concurrency)

        async def _send_one(phone: str, msg: str) -> DeliveryResult:
            async with sem:
                return await self.send_text(phone, msg)

        tasks = [_send_one(phone, msg) for phone, msg in messages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                final.append(DeliveryResult(
                    phone=messages[i][0],
                    success=False,
                    error=str(r),
                ))
            else:
                final.append(r)

        return final

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _call_api(self, phone: str, message: str, attempt: int) -> DeliveryResult:
        """Direct call to Meta WhatsApp Cloud API."""
        url = f"{WA_BASE_URL}/{_get_phone_id()}/messages"
        headers = {
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message[:4096],  # WhatsApp text limit
            },
        }

        client = await self._get_client()
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
            logger.info(f"[WhatsApp] Sent to {phone} | msg_id={msg_id}")
            return DeliveryResult(phone=phone, success=True, message_id=msg_id, attempt=attempt)

        elif response.status_code == 400:
            # Bad phone number — mark as invalid
            error_data = response.json().get("error", {})
            error_msg = error_data.get("message", "Bad request")
            logger.warning(f"[WhatsApp] Invalid number {phone}: {error_msg}")
            return DeliveryResult(phone=phone, success=False, error=f"INVALID_NUMBER:{error_msg}", attempt=attempt)

        elif response.status_code == 429:
            # Rate limited by Meta
            logger.warning(f"[WhatsApp] Rate limited by Meta — sleeping 5s")
            await asyncio.sleep(5.0)
            raise Exception("Meta rate limit — retry")

        elif response.status_code in (500, 502, 503):
            self._circuit.record_failure()
            raise Exception(f"Meta API error {response.status_code}")

        else:
            raise Exception(f"Unexpected status {response.status_code}: {response.text[:200]}")

    def health(self) -> Dict[str, Any]:
        token = _get_token()
        phone_id = _get_phone_id()
        return {
            "configured": bool(token and phone_id
                               and token != "YOUR_WHATSAPP_PERMANENT_TOKEN_HERE"
                               and phone_id != "YOUR_PHONE_NUMBER_ID_HERE"),
            "circuit_state": self._circuit._state,
            "phone_id": phone_id or "NOT SET",
        }

    async def send_template(
        self,
        phone: str,
        template_name: str = "hello_world",
        language_code: str = "en_US",
        components: Optional[List[Dict]] = None,
    ) -> DeliveryResult:
        """
        Send a WhatsApp template message (works without 24h conversation window).
        
        Default uses Meta's built-in 'hello_world' template.
        For production, create custom templates in Meta Business Manager.
        """
        clean_phone = _normalize_phone(phone)
        if not clean_phone:
            return DeliveryResult(phone=phone, success=False, error="Invalid phone number")

        if not _get_token() or not _get_phone_id():
            return DeliveryResult(phone=phone, success=False, error="WhatsApp credentials not configured")

        url = f"{WA_BASE_URL}/{_get_phone_id()}/messages"
        headers = {
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components

        await self._limiter.acquire()
        client = await self._get_client()
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
            logger.info(f"[WhatsApp] Template '{template_name}' sent to {clean_phone} | msg_id={msg_id}")
            return DeliveryResult(phone=clean_phone, success=True, message_id=msg_id)

        error_data = response.json().get("error", {})
        error_msg = error_data.get("message", response.text[:200])
        logger.warning(f"[WhatsApp] Template send failed for {clean_phone}: {response.status_code} {error_msg}")
        return DeliveryResult(phone=clean_phone, success=False, error=f"{response.status_code}:{error_msg}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone to E.164 without '+'.
    Accepts: '9876543210', '+919876543210', '919876543210'
    Returns: '919876543210' or None if invalid
    """
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return f"91{digits}"  # Add India country code
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if len(digits) == 13 and digits.startswith("091"):
        return digits[1:]
    return None  # Invalid


# ─── Singleton ────────────────────────────────────────────────────────────────
whatsapp_service = WhatsAppService()
