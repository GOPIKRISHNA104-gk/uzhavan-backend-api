"""
Mandi Price Service - Fetches and manages mandi prices from data.gov.in (Agmarknet)
This is the SINGLE SOURCE OF TRUTH for all mandi prices

PERFORMANCE OPTIMIZATIONS:
- In-memory caching with 1-hour TTL for today's prices
- Persistent SQLite cache for fallback on API failures
- Automatic retry with exponential backoff (max 2 retries)
- Circuit breaker to prevent overwhelming failing services
- Request deduplication for concurrent requests
"""

import httpx
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.dialects.sqlite import insert
import logging
import time
import asyncio

from config import settings
from database import MandiPrice, PriceFetchLog, Base

# Import performance services
try:
    from services.cache_service import cache_manager, CacheConfig, cached
    from services.http_client import http_client, TimeoutConfig, HTTPClientError, fetch_with_fallback
    CACHE_ENABLED = True
except ImportError:
    CACHE_ENABLED = False
    logger.warning("Cache/HTTP services not available, running without caching")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MandiPriceService:
    """
    Service to fetch and manage mandi prices from data.gov.in
    API Documentation: https://data.gov.in/
    Resource: Daily Prices of Commodities
    """
    
    def __init__(self):
        self.api_key = settings.DATA_GOV_API_KEY
        self.resource_id = settings.DATA_GOV_RESOURCE_ID
        self.base_url = settings.DATA_GOV_BASE_URL
        
    def _build_api_url(
        self,
        limit: int = 1000,
        offset: int = 0,
        filters: Optional[Dict[str, str]] = None
    ) -> str:
        """Build the API URL with parameters"""
        url = f"{self.base_url}/{self.resource_id}"
        params = [
            f"api-key={self.api_key}",
            f"format=json",
            f"limit={limit}",
            f"offset={offset}"
        ]
        
        if filters:
            for key, value in filters.items():
                if value:
                    params.append(f"filters[{key}]={value}")
        
        return f"{url}?{'&'.join(params)}"
    
    async def fetch_prices_from_api(
        self,
        limit: int = 1000,
        offset: int = 0,
        state: Optional[str] = None,
        commodity: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch prices from data.gov.in API with robust error handling
        
        Features:
        - Automatic retry with exponential backoff (2 retries)
        - 30 second timeout (data.gov.in can be slow)
        - Circuit breaker to prevent overwhelming failing service
        - Fallback to cached data on failure
        """
        filters = {}
        if state:
            filters["state"] = state
        if commodity:
            filters["commodity"] = commodity
        if district:
            filters["district"] = district
        if market:
            filters["market"] = market
            
        url = self._build_api_url(limit=limit, offset=offset, filters=filters)
        cache_key = f"mandi_api:{state}:{commodity}:{offset}:{limit}"
        
        # Try using the robust HTTP client if available
        if CACHE_ENABLED:
            try:
                logger.info(f"Fetching mandi prices from: {url[:80]}...")
                
                response = await http_client.get(
                    url,
                    service_name="data_gov_in",
                    timeout=TimeoutConfig.STANDARD,  # 30 second timeout
                    max_retries=2
                )
                data = response.json()
                
                result = {
                    "success": True,
                    "total_records": data.get("total", 0),
                    "records": data.get("records", []),
                    "count": data.get("count", 0)
                }
                
                # Cache successful response for fallback
                await cache_manager.set(
                    cache_key, 
                    result, 
                    CacheConfig.MANDI_PRICES,  # 4 hours
                    category="mandi_prices"
                )
                
                return result
                
            except HTTPClientError as e:
                logger.error(f"HTTP client error fetching mandi prices: {e}")
                
                # Try to get cached fallback data
                cached_data, is_stale = await cache_manager.get(cache_key, use_persistent_fallback=True)
                if cached_data:
                    logger.info(f"Using {'stale' if is_stale else 'cached'} mandi price data")
                    cached_data["from_cache"] = True
                    return cached_data
                
                return {
                    "success": False,
                    "error": str(e),
                    "records": []
                }
                
            except Exception as e:
                logger.error(f"Error fetching mandi prices: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "records": []
                }
        
        # Fallback to original implementation if cache service not available
        transport = httpx.AsyncHTTPTransport(retries=2)
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
            try:
                logger.info(f"Fetching mandi prices from: {url[:100]}...")
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                return {
                    "success": True,
                    "total_records": data.get("total", 0),
                    "records": data.get("records", []),
                    "count": data.get("count", 0)
                }
                
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error fetching mandi prices: {e}")
                return {
                    "success": False,
                    "error": f"HTTP Error: {e.response.status_code}",
                    "records": []
                }
            except httpx.TimeoutException as e:
                logger.error(f"Timeout fetching mandi prices: {e}")
                return {
                    "success": False,
                    "error": "Request timed out. Please try again.",
                    "records": []
                }
            except httpx.RequestError as e:
                logger.error(f"Request error fetching mandi prices: {e}")
                return {
                    "success": False,
                    "error": f"Network error. Please check your connection.",
                    "records": []
                }
            except Exception as e:
                logger.error(f"Error fetching mandi prices: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "records": []
                }
                return {
                    "success": False,
                    "error": str(e),
                    "records": []
                }
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date from API response (format: DD/MM/YYYY or YYYY-MM-DD)"""
        if not date_str:
            return None
            
        formats = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
    
    def _parse_price(self, price_val: Any) -> float:
        """Parse price value from API (handles string and numeric)"""
        if price_val is None:
            return 0.0
        try:
            return float(price_val)
        except (ValueError, TypeError):
            return 0.0
    
    def _clean_string(self, value: Any) -> str:
        """Clean and normalize string values"""
        if value is None:
            return ""
        return str(value).strip().title()
    
    async def store_prices(
        self,
        db: AsyncSession,
        records: List[Dict]
    ) -> Dict[str, int]:
        """
        Store fetched prices in database
        Returns count of inserted records
        """
        inserted = 0
        skipped = 0
        errors = 0
        
        for record in records:
            try:
                # Parse the record based on data.gov.in API response structure
                # Field names may vary, handle common variations
                arrival_date = self._parse_date(
                    record.get("arrival_date") or 
                    record.get("Arrival_Date") or
                    record.get("date")
                )
                
                if not arrival_date:
                    skipped += 1
                    continue
                
                # Extract commodity and location data
                commodity = self._clean_string(
                    record.get("commodity") or 
                    record.get("Commodity") or 
                    record.get("commodity_name")
                )
                
                if not commodity:
                    skipped += 1
                    continue
                
                state = self._clean_string(
                    record.get("state") or 
                    record.get("State")
                )
                
                district = self._clean_string(
                    record.get("district") or 
                    record.get("District")
                )
                
                market = self._clean_string(
                    record.get("market") or 
                    record.get("Market") or
                    record.get("market_name")
                )
                
                # Parse prices
                min_price = self._parse_price(
                    record.get("min_price") or 
                    record.get("Min_x0020_Price") or
                    record.get("min_price")
                )
                
                max_price = self._parse_price(
                    record.get("max_price") or 
                    record.get("Max_x0020_Price") or
                    record.get("max_price")
                )
                
                modal_price = self._parse_price(
                    record.get("modal_price") or 
                    record.get("Modal_x0020_Price") or
                    record.get("modal_price")
                )
                
                # Skip if no valid prices
                if min_price == 0 and max_price == 0 and modal_price == 0:
                    skipped += 1
                    continue
                
                variety = self._clean_string(
                    record.get("variety") or 
                    record.get("Variety")
                )
                
                grade = self._clean_string(
                    record.get("grade") or 
                    record.get("Grade")
                )
                
                commodity_code = str(record.get("commodity_code", "") or record.get("Commodity_Code", ""))
                
                # Check if record already exists (same date, commodity, market)
                existing = await db.execute(
                    select(MandiPrice).where(
                        and_(
                            MandiPrice.arrival_date == arrival_date,
                            MandiPrice.commodity == commodity,
                            MandiPrice.market == market,
                            MandiPrice.state == state
                        )
                    )
                )
                
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue
                
                # Create new price record
                price_record = MandiPrice(
                    arrival_date=arrival_date,
                    state=state,
                    district=district,
                    market=market,
                    commodity=commodity,
                    variety=variety,
                    grade=grade,
                    min_price=min_price,
                    max_price=max_price,
                    modal_price=modal_price,
                    commodity_code=commodity_code
                )
                
                db.add(price_record)
                inserted += 1
                
            except Exception as e:
                logger.error(f"Error storing record: {e}")
                errors += 1
                continue
        
        try:
            await db.commit()
        except Exception as e:
            logger.error(f"Error committing prices: {e}")
            await db.rollback()
            return {"inserted": 0, "skipped": skipped, "errors": errors + inserted}
        
        return {"inserted": inserted, "skipped": skipped, "errors": errors}
    
    async def fetch_and_store_daily_prices(
        self,
        db: AsyncSession,
        max_records: int = 10000,
        state_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main function to fetch daily prices and store in database
        Called by the scheduler
        """
        # Invalidate cache before update
        self._prices_cache = {}
        
        start_time = time.time()
        logger.info("Starting daily mandi price fetch...")
        
        total_fetched = 0
        total_inserted = 0
        total_skipped = 0
        total_errors = 0
        
        offset = 0
        batch_size = 1000
        
        while offset < max_records:
            result = await self.fetch_prices_from_api(
                limit=batch_size,
                offset=offset,
                state=state_filter
            )
            
            if not result["success"]:
                break
                
            records = result["records"]
            if not records:
                break
                
            total_fetched += len(records)
            
            # Store records
            store_result = await self.store_prices(db, records)
            total_inserted += store_result["inserted"]
            total_skipped += store_result["skipped"]
            total_errors += store_result["errors"]
            
            logger.info(f"Processed batch: {offset}-{offset+len(records)}, "
                       f"Inserted: {store_result['inserted']}")
            
            if len(records) < batch_size:
                break
                
            offset += batch_size
        
        duration = time.time() - start_time
        
        # Log the fetch operation
        status = "success" if total_errors == 0 else "partial" if total_inserted > 0 else "failed"
        
        fetch_log = PriceFetchLog(
            fetch_date=datetime.utcnow(),
            records_fetched=total_fetched,
            records_inserted=total_inserted,
            status=status,
            duration_seconds=duration
        )
        db.add(fetch_log)
        await db.commit()
        
        logger.info(f"Daily fetch complete: Fetched={total_fetched}, "
                   f"Inserted={total_inserted}, Skipped={total_skipped}, "
                   f"Errors={total_errors}, Duration={duration:.2f}s")
        
        # Clear cache again just to be safe
        self._prices_cache = {}
        
        return {
            "success": True,
            "total_fetched": total_fetched,
            "total_inserted": total_inserted,
            "total_skipped": total_skipped,
            "total_errors": total_errors,
            "duration_seconds": duration
        }
    
    async def get_today_prices(
        self,
        db: AsyncSession,
        commodity: Optional[str] = None,
        state: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Get today's mandi prices.
        Uses Multi-layer Cache (Memory -> Persistent -> DB)
        """
        # Cache Key
        cache_key = f"mandi:today:{commodity}:{state}:{district}:{market}:{limit}"
        
        # 1. Try Cache
        if CACHE_ENABLED:
            cached_data, is_stale = await cache_manager.get(cache_key)
            if cached_data:
                logger.info(f"🚀 Serving mandi prices from {'stale ' if is_stale else ''}Cache")
                return cached_data

        # 2. Query Database
        today = date.today()
        yesterday = today - timedelta(days=1)
        
        query = select(MandiPrice).where(
            MandiPrice.arrival_date >= yesterday
        )
        
        if commodity:
            query = query.where(MandiPrice.commodity.ilike(f"%{commodity}%"))
        if state:
            query = query.where(MandiPrice.state.ilike(f"%{state}%"))
        if district:
            query = query.where(MandiPrice.district.ilike(f"%{district}%"))
        if market:
            query = query.where(MandiPrice.market.ilike(f"%{market}%"))
        
        query = query.order_by(desc(MandiPrice.arrival_date)).limit(limit)
        
        result = await db.execute(query)
        prices = result.scalars().all()
        
        # Convert ORM objects to dicts for caching
        prices_data = []
        for p in prices:
            p_dict = {k: v for k, v in p.__dict__.items() if not k.startswith('_')}
            # Convert date/datetime to string
            if isinstance(p_dict.get("arrival_date"), (date, datetime)):
                p_dict["arrival_date"] = p_dict["arrival_date"].isoformat()
            if isinstance(p_dict.get("created_at"), (date, datetime)):
                p_dict["created_at"] = p_dict["created_at"].isoformat()
            prices_data.append(p_dict)
            
        # 3. Store in Cache (1 hour TTL for today's queries)
        if CACHE_ENABLED:
            await cache_manager.set(
                cache_key,
                prices_data,
                CacheConfig.MANDI_PRICES_TODAY,
                category="mandi_prices"
            )
        
        return prices_data
    
    async def get_historical_prices(
        self,
        db: AsyncSession,
        commodity: str,
        state: Optional[str] = None,
        district: Optional[str] = None,
        market: Optional[str] = None,
        days: int = 30
    ) -> List[MandiPrice]:
        """Get historical prices for a commodity"""
        start_date = datetime.now() - timedelta(days=days)
        
        query = select(MandiPrice).where(
            and_(
                MandiPrice.commodity.ilike(f"%{commodity}%"),
                MandiPrice.arrival_date >= start_date
            )
        )
        
        if state:
            query = query.where(MandiPrice.state.ilike(f"%{state}%"))
        if district:
            query = query.where(MandiPrice.district.ilike(f"%{district}%"))
        if market:
            query = query.where(MandiPrice.market.ilike(f"%{market}%"))
        
        query = query.order_by(MandiPrice.arrival_date)
        
        result = await db.execute(query)
        return result.scalars().all()
    
    async def get_available_commodities(
        self,
        db: AsyncSession,
        state: Optional[str] = None
    ) -> List[str]:
        """Get list of available commodities in database"""
        query = select(MandiPrice.commodity).distinct()
        
        if state:
            query = query.where(MandiPrice.state.ilike(f"%{state}%"))
        
        result = await db.execute(query)
        commodities = [row[0] for row in result.fetchall()]
        return sorted(set(commodities))
    
    async def get_available_states(self, db: AsyncSession) -> List[str]:
        """Get list of available states in database"""
        query = select(MandiPrice.state).distinct()
        result = await db.execute(query)
        states = [row[0] for row in result.fetchall()]
        return sorted(set(states))
    
    async def get_available_markets(
        self,
        db: AsyncSession,
        state: Optional[str] = None,
        district: Optional[str] = None
    ) -> List[str]:
        """Get list of available markets"""
        query = select(MandiPrice.market).distinct()
        
        if state:
            query = query.where(MandiPrice.state.ilike(f"%{state}%"))
        if district:
            query = query.where(MandiPrice.district.ilike(f"%{district}%"))
        
        result = await db.execute(query)
        markets = [row[0] for row in result.fetchall()]
        return sorted(set(markets))
    
    async def get_fetch_status(self, db: AsyncSession) -> Dict[str, Any]:
        """Get the status of the last price fetch"""
        query = select(PriceFetchLog).order_by(desc(PriceFetchLog.created_at)).limit(1)
        result = await db.execute(query)
        last_fetch = result.scalar_one_or_none()
        
        if not last_fetch:
            return {
                "last_fetch": None,
                "status": "never_fetched",
                "message": "No price data has been fetched yet"
            }
        
        return {
            "last_fetch": last_fetch.fetch_date.isoformat(),
            "status": last_fetch.status,
            "records_fetched": last_fetch.records_fetched,
            "records_inserted": last_fetch.records_inserted,
            "duration_seconds": last_fetch.duration_seconds
        }


# Singleton instance
mandi_service = MandiPriceService()
