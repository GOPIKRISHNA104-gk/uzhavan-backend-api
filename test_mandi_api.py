"""
Test Script for Mandi Price API
Verifies the data.gov.in integration and price prediction functionality
"""

import asyncio
import httpx
import sys
from datetime import datetime

# Test the data.gov.in API directly
async def test_data_gov_api():
    """Test direct API call to data.gov.in"""
    print("\n" + "="*60)
    print("🧪 Testing data.gov.in Mandi Prices API")
    print("="*60)
    
    api_key = "579b464db66ec23bdd0000017e9eeb3f27364a7f787e6ae437618207"
    resource_id = "9ef84268-d588-465a-a308-a864a43d0070"
    base_url = "https://api.data.gov.in/resource"
    
    url = f"{base_url}/{resource_id}?api-key={api_key}&format=json&limit=5"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print(f"📡 Calling API: {url[:80]}...")
            response = await client.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            print(f"✅ API Response Status: {response.status_code}")
            print(f"📦 Total Records Available: {data.get('total', 'N/A')}")
            print(f"📊 Records in Response: {data.get('count', 'N/A')}")
            
            records = data.get('records', [])
            if records:
                print("\n📋 Sample Records:")
                print("-" * 60)
                for i, record in enumerate(records[:3], 1):
                    print(f"\n  Record {i}:")
                    print(f"    State: {record.get('state', 'N/A')}")
                    print(f"    District: {record.get('district', 'N/A')}")
                    print(f"    Market: {record.get('market', 'N/A')}")
                    print(f"    Commodity: {record.get('commodity', 'N/A')}")
                    print(f"    Arrival Date: {record.get('arrival_date', 'N/A')}")
                    print(f"    Min Price: ₹{record.get('min_price', 'N/A')}")
                    print(f"    Max Price: ₹{record.get('max_price', 'N/A')}")
                    print(f"    Modal Price: ₹{record.get('modal_price', 'N/A')}")
                
                return True
            else:
                print("⚠️ No records returned from API")
                return False
                
        except httpx.HTTPStatusError as e:
            print(f"❌ HTTP Error: {e.response.status_code}")
            return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False


async def test_mandi_service():
    """Test the MandiPriceService"""
    print("\n" + "="*60)
    print("🧪 Testing MandiPriceService")
    print("="*60)
    
    try:
        from services.mandi_service import mandi_service
        
        # Test API fetch
        result = await mandi_service.fetch_prices_from_api(limit=10)
        
        if result["success"]:
            print(f"✅ Service fetch successful")
            print(f"📦 Records fetched: {len(result['records'])}")
            return True
        else:
            print(f"❌ Service fetch failed: {result.get('error')}")
            return False
            
    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_price_predictor():
    """Test the PricePredictionService logic"""
    print("\n" + "="*60)
    print("🧪 Testing Price Prediction Logic")
    print("="*60)
    
    try:
        from services.price_predictor import PricePredictionService
        
        predictor = PricePredictionService()
        
        # Test moving average calculation
        test_prices = [100, 105, 110, 108, 112, 115, 120]
        
        short_ma = predictor._calculate_moving_average(test_prices, 3)
        long_ma = predictor._calculate_moving_average(test_prices, 7)
        wma = predictor._calculate_weighted_moving_average(test_prices, 5)
        slope, trend = predictor._calculate_trend(test_prices)
        volatility = predictor._calculate_volatility(test_prices)
        
        print(f"📊 Test Data: {test_prices}")
        print(f"📈 Short MA (3-day): {short_ma:.2f}")
        print(f"📈 Long MA (7-day): {long_ma:.2f}")
        print(f"📈 Weighted MA (5-day): {wma:.2f}")
        print(f"📈 Trend Slope: {slope:.2f}")
        print(f"📈 Trend Direction: {trend}")
        print(f"📈 Volatility: {volatility*100:.2f}%")
        
        confidence = predictor._calculate_confidence_score(
            data_points=len(test_prices),
            volatility=volatility,
            trend_consistency=1.0
        )
        print(f"📈 Confidence Score: {confidence:.2f}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def main():
    print("\n" + "="*60)
    print("🌾 MANDI PRICE SYSTEM TEST SUITE")
    print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = {}
    
    # Test 1: Direct API
    results["data_gov_api"] = await test_data_gov_api()
    
    # Test 2: Mandi Service
    results["mandi_service"] = await test_mandi_service()
    
    # Test 3: Price Predictor
    results["price_predictor"] = await test_price_predictor()
    
    # Summary
    print("\n" + "="*60)
    print("📊 TEST SUMMARY")
    print("="*60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "="*60)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
    else:
        print("⚠️ SOME TESTS FAILED")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
