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

# Symbols to generate analysis for
SYMBOLS = [
    ("BTCUSD", "Bitcoin"),
    ("ETHUSD", "Ethereum"),
    ("USDHUF", "Dollár/Forint árfolyam"),
    ("EURHUF", "Euró/Forint árfolyam"),
]

VALID_SENTIMENTS = ["Bullish", "Bearish", "Semleges"]

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

def generate_for_symbol(api_key, symbol, name):
    """Generates market analysis for a single symbol using Gemini API."""
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

    # Gemini API format
    url = f"{API_URL}?key={GEMINI_API_KEY}"
    
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.4
        }
    }

    try:
        response = requests.post(url, json=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        safe_print(f"    ERROR ({symbol}): {e}")
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

def process_symbol(api_key, symbol, name):
    """Process a single symbol with retries. Returns (symbol, parsed_data) or (symbol, None)."""
    safe_print(f"  Generating for {symbol} ({name})...")
    
    max_attempts = 3
    parsed = None
    for attempt in range(max_attempts):
        content = generate_for_symbol(api_key, symbol, name)
        parsed, is_valid, errors = validate_and_fix(content, symbol)
        
        if is_valid:
            safe_print(f"    [OK] {symbol}: Valid JSON format")
            return symbol, parsed
        else:
            safe_print(f"    [FAIL] {symbol}: Invalid format (attempt {attempt+1}/{max_attempts}). Errors: {errors}")
            if attempt < max_attempts - 1:
                safe_print(f"    {symbol}: Retrying...")
    
    safe_print(f"    [WARN] {symbol}: Failed after {max_attempts} attempts.")
    return symbol, parsed

def main():
    print(f"Market Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    api_key = load_api_key()  # Not used for Gemini, but kept for compatibility
    
    all_analyses = {}
    
    # Process symbols sequentially to respect Gemini rate limits
    for symbol, name in SYMBOLS:
        symbol, parsed = process_symbol(api_key, symbol, name)
        if parsed:
            all_analyses[symbol] = parsed
        time.sleep(2)  # Rate limit delay for Gemini API
    
    # Write all analyses to JSON file
    output_path = os.path.join(OUTPUT_DIR, 'piacok.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_analyses, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(all_analyses)} analyses to {output_path}")

if __name__ == "__main__":
    main()

