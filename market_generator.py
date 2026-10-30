"""
LEÍRÁS:
Piac-elemzés generátor.
A beállított pénzügyi eszközökhöz (Input/piaci_parok.txt) készít rövid elemzést és hangulatértékelést (Bullish/Bearish/Semleges).
JSON formátumban menti a kimenetet.

BEMENET:
- Eszközlista: Input/piaci_parok.txt
- Prompt sablon: Input/piacok_prompt.txt

KIMENET:
- Output/[Dátum]/piacok.json: Elemzések listája
"""

import os
import sys
import datetime
import json
import time
import re

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Import Clients
import geminipro_client
import gemini_api_client as geminipro_cli_client
import perplexity_client
import gemini_selenium_client
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Enable wait-for-rate-limit mode if WAITREFRESH env var is set
if os.environ.get('WAITREFRESH') == '1':
    geminipro_client.set_wait_for_rate_limit(True)

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR

VALID_SENTIMENTS = ["Bullish", "Bearish", "Semleges"]


def load_symbols():
    """Loads symbols from piaci_parok.txt. Returns list of (symbol, name) tuples."""
    symbols = []
    try:
        with open(os.path.join(INPUT_DIR, 'piaci_parok.txt'), 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) == 2:
                    symbols.append((parts[0].strip(), parts[1].strip()))
    except FileNotFoundError:
        print("Error: piaci_parok.txt not found.")
    return symbols


def load_prompt_template():
    """Loads prompt template from piacok_prompt.txt."""
    try:
        with open(os.path.join(INPUT_DIR, 'piacok_prompt.txt'), 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print("Error: piacok_prompt.txt not found.")
        return None


def generate_for_symbol(symbol, name, prompt_template, backend='gemini', gemini_api_key=None):
    """Generates market analysis for a single symbol using GeminiPro or Perplexity."""
    today = datetime.date.today().strftime('%Y. %m. %d.')
    today_iso = datetime.date.today().strftime('%Y-%m-%d')
    
    # Fill template
    prompt = prompt_template.replace("{SYMBOL}", symbol).replace("{NAME}", name)\
                            .replace("{DATE}", today).replace("{DATE_ISO}", today_iso)
    
    if backend == 'perplexity':
        content, model = perplexity_client.call_with_fallback(prompt, use_pro=True)
    elif backend == 'geminipro-cli':
        content, model = geminipro_cli_client.call_with_fallback(prompt, use_pro=True, api_key=gemini_api_key)
    elif backend == 'gemini-selenium':
        content, model = gemini_selenium_client.call_with_fallback(prompt, use_pro=True)
    elif backend == 'gemini-api':
        content, model = geminipro_cli_client.call_with_fallback(prompt, use_pro=True, api_key=gemini_api_key)
    elif backend == 'free-gemini-api':
        content, model = geminipro_cli_client.call_with_fallback(prompt, use_pro=True)
    else:
        # Gemini (Cookie)
        content, model = geminipro_client.call_with_fallback(prompt, use_pro=True)
    
    return content, model


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
    
    # Remove citation markers like [1], [12], [2][4]
    content = re.sub(r'\[\d+\]', '', content)
    
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


def process_symbol(symbol, name, prompt_template, preferred_backend='gemini', gemini_api_key=None, available_backends=None):
    """Process a single symbol with retries. Returns (symbol, parsed_data, backend_used) or (symbol, None, None)."""
    # print(f"  Generating for {symbol} ({name})...")
    
    if available_backends is None:
        available_backends = ['gemini']
        
    max_attempts = 3
    parsed = None
    for attempt in range(max_attempts):
        current_backend = preferred_backend
        if attempt > 0 and len(available_backends) > 1:
            import random
            others = [b for b in available_backends if b != preferred_backend]
            if others:
                current_backend = random.choice(others)
            
        content, model = generate_for_symbol(symbol, name, prompt_template, current_backend, gemini_api_key)
        parsed, is_valid, errors = validate_and_fix(content, symbol)
        
        if is_valid:
            print(f"    [OK] {symbol}: Valid JSON format (via {model})")
            return symbol, parsed, model
        else:
            print(f"    [FAIL] {symbol}: Invalid via {current_backend} (attempt {attempt+1}/{max_attempts})")
            if attempt < max_attempts - 1:
                time.sleep(1)
    
    print(f"    [WARN] {symbol}: Failed after {max_attempts} attempts.")
    return symbol, parsed, None


def main():
    print(f"Market Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    prompt_template = load_prompt_template()
    if not prompt_template:
        print("Template loading failed. Exiting.")
        return

    symbols = load_symbols()
    if not symbols:
        print("No symbols found in piaci_parok.txt. Exiting.")
        return

    print(f"Loaded {len(symbols)} symbols from config.")
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--use-geminipro-cli', action='store_true')
    parser.add_argument('--use-geminipro', action='store_true')
    parser.add_argument('--use-perplexity', action='store_true')
    parser.add_argument('--use-gemini-api', action='store_true')
    parser.add_argument('--use-gemini-selenium', action='store_true')
    parser.add_argument('--use-free-gemini-api', action='store_true')
    parser.add_argument('--gemini-api-key', type=str)
    args = parser.parse_args()

    # Determine enabled backends
    enabled_backends = []
    if args.use_geminipro_cli:
        enabled_backends.append('geminipro-cli')
    if args.use_geminipro:
        enabled_backends.append('gemini')
    if args.use_perplexity:
        enabled_backends.append('perplexity')
    if args.use_gemini_api:
        enabled_backends.append('gemini-api')
    if args.use_gemini_selenium:
        enabled_backends.append('gemini-selenium')
    if args.use_free_gemini_api:
        enabled_backends.append('free-gemini-api')
    
    if not enabled_backends:
        # Default: use geminipro-cli (Gemini API key pool)
        enabled_backends = ['geminipro-cli']

    print(f"Enabled backends: {enabled_backends}")
    
    output_path = os.path.join(OUTPUT_DIR, 'piacok.json')
    all_analyses = {}
    
    # Load existing data if available
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                all_analyses = json.load(f)
            print(f"Loaded {len(all_analyses)} existing analyses from {output_path}")
        except Exception as e:
            print(f"Error loading existing data: {e}")
            all_analyses = {}
    lock = threading.Lock()
    
    # Prepare work items with alternating backends
    work_items = []
    # Determine which symbols need processing
    symbols_to_process = [s for s in symbols if s[0] not in all_analyses]
    print(f"Skipping {len(symbols) - len(symbols_to_process)} symbols (already done). Processing {len(symbols_to_process)} remaining.")

    work_items = []
    for i, (symbol, name) in enumerate(symbols_to_process):
        backend = enabled_backends[i % len(enabled_backends)]
        work_items.append((symbol, name, backend))
        
    for b in enabled_backends:
        count = len([x for x in work_items if x[2] == b])
        print(f"  - {b}: {count}")
    
    def worker(work_item):
        symbol, name, backend = work_item
        sym, parsed, used_backend = process_symbol(symbol, name, prompt_template, backend, args.gemini_api_key, enabled_backends)
        if parsed:
            with lock:
                all_analyses[sym] = parsed
                # SAVE IMMEDIATELY
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(all_analyses, f, ensure_ascii=False, indent=2)
                print(f"      💾 Saved {sym} to disk.")
        return sym, used_backend

    # Process in parallel
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(worker, item): item[0] for item in work_items}
        
        for future in as_completed(futures):
            sym = futures[future]
            try:
                res_sym, res_backend = future.result()
            except Exception as e:
                print(f"  Error processing {sym}: {e}")
    
    # Final summary
    print(f"\nFinished. Total analyses in {output_path}: {len(all_analyses)}")


if __name__ == "__main__":
    main()
