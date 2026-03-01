import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

endpoints = [
    {
        "name": "Dashboard Aggregated API",
        "method": "GET",
        "url": f"{BASE_URL}/api/v1/dashboard/?lat=13.0827&lon=80.2707&state=Tamil%20Nadu&lang=en"
    },
    {
        "name": "Market Prices API",
        "method": "GET",
        "url": f"{BASE_URL}/api/market/prices?state=Tamil%20Nadu&lang=en&category=all"
    },
    {
        "name": "Agriculture News API",
        "method": "GET",
        "url": f"{BASE_URL}/api/news/cards?language=tamil&state=tamil_nadu"
    }
]

def run_tests():
    print("🚀 Fetching all key APIs... Please wait.")
    print("-" * 50)
    
    for ep in endpoints:
        print(f"Fetching: {ep['name']}")
        print(f"{ep['method']} {ep['url']}")
        
        try:
            start_time = time.time()
            if ep['method'] == 'GET':
                response = requests.get(ep['url'], timeout=15)
            else:
                response = requests.post(ep['url'], json=ep.get('payload', {}), timeout=15)
            
            elapsed = time.time() - start_time
            print(f"Status Code: {response.status_code}")
            print(f"Time Taken : {elapsed:.2f}s")
            
            if response.status_code == 200:
                data = response.json()
                print("✅ SUCCESS")
                # Print a small snippet of the response
                json_str = json.dumps(data, indent=2)
                if len(json_str) > 300:
                    print(json_str[:300] + "\n... [truncated] ...")
                else:
                    print(json_str)
            else:
                print("❌ FAILED")
                print(response.text[:200])
        except Exception as e:
            print(f"❌ ERROR: {e}")
        
        print("-" * 50)

if __name__ == "__main__":
    run_tests()
