import os
import requests
import datetime
import json
import re

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"

# Cities to generate weather for (updated list)
CITIES = [
    "Budapest",
    "Wien",
    "Székesfehérvár",
    "Rijeka",
]

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
    """Generates weather forecast for a single city."""
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

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "stream": False 
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        # Clean up the response
        content = content.strip().strip('"').strip("'")
        return content
    except Exception as e:
        print(f"    ERROR: {e}")
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

def main():
    print(f"Weather Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    api_key = load_api_key()
    if not api_key:
        print("No API key found. Exiting.")
        return
    
    all_forecasts = {}
    
    for city in CITIES:
        print(f"\n  Generating for {city}...")
        
        max_attempts = 3
        for attempt in range(max_attempts):
            content = generate_for_city(api_key, city)
            fixed_content, is_valid, errors = validate_forecast(content, city)
            
            if is_valid:
                print(f"    [OK] Valid forecast")
                all_forecasts[city] = fixed_content
                break
            else:
                print(f"    [FAIL] Invalid format (attempt {attempt+1}/{max_attempts}). Errors: {errors}")
                if attempt < max_attempts - 1:
                    print(f"    Retrying...")
        else:
            print(f"    [WARN] Failed after {max_attempts} attempts. Using partial content if available.")
            if fixed_content:
                all_forecasts[city] = fixed_content
    
    # Write all forecasts to JSON file
    output_path = os.path.join(OUTPUT_DIR, 'idojaras.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_forecasts, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(all_forecasts)} forecasts to {output_path}")

if __name__ == "__main__":
    main()
