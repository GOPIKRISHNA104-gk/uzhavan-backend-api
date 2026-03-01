import requests
import json

def test_prediction():
    url = "http://localhost:8000/api/prices/predict"
    payload = {
        "commodity": "Tomato",
        "state": "Tamil Nadu",
        "days_ahead": 1
    }
    
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print("✅ Prediction SUCCESS:")
                print(json.dumps(data, indent=2))
            else:
                print("❌ Prediction FAILED (API Success=False):")
                print(data)
        else:
            print("❌ Prediction FAILED (HTTP Error):")
            print(response.text)
            
    except Exception as e:
        print(f"❌ Error connecting to API: {e}")

if __name__ == "__main__":
    test_prediction()
