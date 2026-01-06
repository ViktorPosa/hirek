import os
import requests
import datetime
import json
import re
import time
import concurrent.futures
import threading

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GEMINI_API_KEY = "AIzaSyDqvul5imv3Qnc6FV-o3AoBV3nomg7Zk0E"
MAX_WORKERS = 20  # Parallel processing threads

# Cities to generate weather for (updated list)
CITIES = [
    "Budapest",
    "Wien",
    "Székesfehérvár",
    "Rijeka",
]

# Thread-safe print lock
print_lock = threading.Lock()

def safe_print(msg):
    """Thread-safe print function."""
    with print_lock:
        print(msg)

def load_api_key():
    """Loads API Key from input.txt."""
    try:
        with open(os.path.join(INPUT_DIR, 'input.txt'), 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("API_KEY="):
                    return line.replace("API_KEY=", "").strip()
    except FileNotFoundError:
        print("Error: input.txt not found.")
    return None

def generate_for_city(api_key, city):
    """Generates weather forecast for a single city using Gemini API."""
    today = datetime.date.today().strftime('%Y. %m. %d.')
    
    prompt = f"""Mai dátum: {today}

Te egy magyar meteorológus vagy. Készíts rövid időjárás-előrejelzést {city} városra a mai napra.

Válaszolj CSAK egyetlen mondatban vagy két rövid mondatban, ami tartalmazza:
- A várható hőmérsékleteket (°C-ban)
- Felhőzet, csapadék információt
- Szél információt ha releváns

Példa: "Ma délután záporok várhatók, 5-8°C között alakul a hőmérséklet, estére kitisztul az ég."

FONTOS: 
- Január eleji, téli időjárást írj le!
- Csak az előrejelzés szövegét add vissza, semmi mást!
- Ne használj idézőjeleket!"""

    # Gemini API format
    url = f"{API_URL}?key={GEMINI_API_KEY}"
    
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:
        response = requests.post(url, json=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        content = result['candidates'][0]['content']['parts'][0]['text']
        # Clean up the response
        content = content.strip().strip('"').strip("'")
        return content
    except Exception as e:
        safe_print(f"    ERROR ({city}): {e}")
        return None

def validate_forecast(content, city):
    """Validates the forecast content. Returns (content, is_valid, errors)."""
    if not content:
        return None, False, ["No content received"]
    
    errors = []
    
    # Check minimum length
    if len(content) < 20:
        errors.append("Forecast too short")
    
    # Check for temperature mention
    if '°' not in content and 'fok' not in content.lower():
        errors.append("No temperature mentioned")
    
    return content, len(errors) == 0, errors

def process_city(api_key, city):
    """Process a single city with retries. Returns (city, forecast) or (city, None)."""
    safe_print(f"  Generating for {city}...")
    
    max_attempts = 3
    for attempt in range(max_attempts):
        content = generate_for_city(api_key, city)
        fixed_content, is_valid, errors = validate_forecast(content, city)
        
        if is_valid:
            safe_print(f"    [OK] {city}: Valid forecast")
            return city, fixed_content
        else:
            safe_print(f"    [FAIL] {city}: Invalid format (attempt {attempt+1}/{max_attempts}). Errors: {errors}")
            if attempt < max_attempts - 1:
                safe_print(f"    {city}: Retrying...")
    
    safe_print(f"    [WARN] {city}: Failed after {max_attempts} attempts. Using partial content if available.")
    return city, fixed_content if fixed_content else None

def main():
    print(f"Weather Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    api_key = load_api_key()  # Not used for Gemini, but kept for compatibility
    
    all_forecasts = {}
    
    # Process cities sequentially to respect Gemini rate limits
    for city in CITIES:
        city, forecast = process_city(api_key, city)
        if forecast:
            all_forecasts[city] = forecast
        time.sleep(2)  # Rate limit delay for Gemini API
    
    # Write all forecasts to JSON file
    output_path = os.path.join(OUTPUT_DIR, 'idojaras.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_forecasts, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(all_forecasts)} forecasts to {output_path}")

if __name__ == "__main__":
    main()

