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

# Symbols to generate analysis for
SYMBOLS = [
    ("BTCUSD", "Bitcoin"),
    ("ETHUSD", "Ethereum"),
    ("USDHUF", "Dollár/Forint árfolyam"),
    ("EURHUF", "Euró/Forint árfolyam"),
]

VALID_SENTIMENTS = ["Bullish", "Bearish", "Semleges"]

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

def generate_for_symbol(api_key, symbol, name):
    """Generates market analysis for a single symbol."""
    today = datetime.date.today().strftime('%Y. %m. %d.')
    today_iso = datetime.date.today().strftime('%Y-%m-%d')
    
    prompt = f"""Mai dátum: {today}

Készíts piaci elemzést a következő instrumentumról: {name} (szimbólum: {symbol})

Válaszolj PONTOSAN ebben a JSON formátumban (csak a JSON-t, semmi mást):

{{
  "title": "Rövid, figyelemfelkeltő cím",
  "summary": "1-2 mondat a legfontosabb fejleményről",
  "details": "2-3 mondat részletesebb elemzéssel",
  "sentiment": "Bullish / Bearish / Semleges",
  "date": "{today_iso}"
}}

FONTOS: 
- A sentiment CSAK "Bullish", "Bearish" vagy "Semleges" lehet!
- Csak a JSON objektumot add vissza, semmi mást!"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "stream": False 
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"    ERROR: {e}")
        return None

def validate_and_fix(content, symbol):
    """Validates JSON format and fixes if possible. Returns (parsed_dict, is_valid, errors)."""
    if not content:
        return None, False, ["No content received"]
    
    errors = []
    
    # Clean up response
    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    content = content.strip()
    
    # Try to parse JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {str(e)[:50]}")
        return None, False, errors
    
    # Check required fields
    required = ['title', 'summary', 'details', 'sentiment', 'date']
    for field in required:
        if field not in parsed:
            errors.append(f"Missing field: {field}")
    
    if errors:
        return parsed, False, errors
    
    # Normalize sentiment
    sentiment = parsed.get('sentiment', '').strip()
    sentiment_map = {
        'bullish': 'Bullish', 'bika': 'Bullish', 'pozitív': 'Bullish', 'emelkedő': 'Bullish',
        'bearish': 'Bearish', 'medve': 'Bearish', 'negatív': 'Bearish', 'csökkenő': 'Bearish',
        'semleges': 'Semleges', 'neutral': 'Semleges', 'neutrális': 'Semleges', 'vegyes': 'Semleges'
    }
    if sentiment.lower() in sentiment_map:
        parsed['sentiment'] = sentiment_map[sentiment.lower()]
    elif sentiment not in VALID_SENTIMENTS:
        errors.append(f"Invalid sentiment: {sentiment}")
        parsed['sentiment'] = 'Semleges'
    
    return parsed, True, []

def main():
    print(f"Market Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    api_key = load_api_key()
    if not api_key:
        print("No API key found. Exiting.")
        return
    
    all_analyses = {}
    
    for symbol, name in SYMBOLS:
        print(f"\n  Generating for {symbol} ({name})...")
        
        max_attempts = 3
        for attempt in range(max_attempts):
            content = generate_for_symbol(api_key, symbol, name)
            parsed, is_valid, errors = validate_and_fix(content, symbol)
            
            if is_valid:
                print(f"    [OK] Valid JSON format")
                all_analyses[symbol] = parsed
                break
            else:
                print(f"    [FAIL] Invalid format (attempt {attempt+1}/{max_attempts}). Errors: {errors}")
                if attempt < max_attempts - 1:
                    print(f"    Retrying...")
        else:
            print(f"    [WARN] Failed after {max_attempts} attempts.")
            if parsed:
                all_analyses[symbol] = parsed
    
    # Write all analyses to JSON file
    output_path = os.path.join(OUTPUT_DIR, 'piacok.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_analyses, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(all_analyses)} analyses to {output_path}")

if __name__ == "__main__":
    main()
