"""
LEÍRÁS:
Időjárás-előrejelzés generátor.
A beállított városokhoz (Input/varosok.txt) készít szöveges előrejelzést AI segítségével (Gemini/Perplexity).
JSON formátumban menti a kimenetet.

BEMENET:
- Városlista: Input/varosok.txt
- Prompt sablon: Input/idojaras_prompt.txt

KIMENET:
- Output/[Dátum]/idojaras.json: Város -> Előrejelzés leképezés
"""

import os
import sys
import datetime
import json
import time

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


def load_cities():
    """Loads cities from varosok.txt."""
    cities = []
    try:
        with open(os.path.join(INPUT_DIR, 'varosok.txt'), 'r', encoding='utf-8') as f:
            lines = f.readlines()
            cities = [line.strip() for line in lines if line.strip()]
    except FileNotFoundError:
        print("Error: varosok.txt not found.")
    return cities


def load_prompt_template():
    """Loads prompt template from idojaras_prompt.txt."""
    try:
        with open(os.path.join(INPUT_DIR, 'idojaras_prompt.txt'), 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print("Error: idojaras_prompt.txt not found.")
        return None


def generate_for_city(city, prompt_template, backend='gemini', gemini_api_key=None):
    """Generates weather forecast for a single city using GeminiPro, CLI or Perplexity."""
    today = datetime.date.today().strftime('%Y. %m. %d.')
    
    # Fill template
    prompt = prompt_template.replace("{CITY}", city).replace("{DATE}", today)
    
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
    
    if content:
        # Clean up the response
        content = content.strip().strip('"').strip("'")
        return content, model
    
    return None, None


def validate_forecast(content, city):
    """Validates the forecast content. Returns (content, is_valid, errors)."""
    if not content:
        return None, False, ["No content received"]
    
    errors = []
    
    # Check minimum length
    if len(content) < 20:
        errors.append("Forecast too short")
    
    return content, len(errors) == 0, errors


def process_city(city, prompt_template, preferred_backend='gemini', gemini_api_key=None, available_backends=None):
    """Process a single city with retries. Returns (city, forecast, backend_used) or (city, None, None)."""
    # print(f"  Generating for {city} ({preferred_backend})...") # too chatty
    
    if available_backends is None:
        available_backends = ['gemini']
        
    max_attempts = 3
    for attempt in range(max_attempts):
        # Swap backend on retry?
        current_backend = preferred_backend
        if attempt > 0 and len(available_backends) > 1:
             # Just pick another one
             import random
             others = [b for b in available_backends if b != preferred_backend]
             if others:
                current_backend = random.choice(others)
        
        content, model = generate_for_city(city, prompt_template, current_backend, gemini_api_key)
        fixed_content, is_valid, errors = validate_forecast(content, city)
        
        if is_valid:
            print(f"    [OK] {city}: Valid forecast (via {model})")
            return city, fixed_content, model
        else:
            print(f"    [FAIL] {city}: Invalid via {current_backend} (attempt {attempt+1}/{max_attempts})")
            if attempt < max_attempts - 1:
                time.sleep(1)
    
    print(f"    [WARN] {city}: Failed after {max_attempts} attempts.")
    return city, None, None


def main():
    print(f"Weather Generator running for: {DAILY_OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    prompt_template = load_prompt_template()
    if not prompt_template:
        print("Template loading failed. Exiting.")
        return

    cities = load_cities()
    if not cities:
        print("No cities found in varosok.txt. Exiting.")
        return
    
    print(f"Loaded {len(cities)} cities from config.")
    
    print(f"Loaded {len(cities)} cities from config.")
    
    print(f"Loaded {len(cities)} cities from config.")
    
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

    output_path = os.path.join(OUTPUT_DIR, 'idojaras.json')
    all_forecasts = {}
    
    # Load existing data if available
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                all_forecasts = json.load(f)
            print(f"Loaded {len(all_forecasts)} existing forecasts from {output_path}")
        except Exception as e:
            print(f"Error loading existing data: {e}")
            all_forecasts = {}
    lock = threading.Lock()
    
    # Simple round-robin or parallel strategy
    # Let's run parallel threads
    
    cities_with_params = []
    # Alternate backends
    # Determine which cities need processing
    cities_to_process = [c for c in cities if c not in all_forecasts]
    print(f"Skipping {len(cities) - len(cities_to_process)} cities (already done). Processing {len(cities_to_process)} remaining.")

    cities_with_params = []
    # Distribute backends
    for i, city in enumerate(cities_to_process):
        # Round-robin selection from enabled backends
        backend = enabled_backends[i % len(enabled_backends)]
        cities_with_params.append((city, backend))
        
    for b in enabled_backends:
        count = len([c for c in cities_with_params if c[1] == b])
        print(f"  - {b}: {count}")
    
    def worker(work_item):
        city, backend = work_item
        city_name, forecast, used_backend = process_city(city, prompt_template, backend, args.gemini_api_key, enabled_backends)
        if forecast:
            with lock:
                all_forecasts[city_name] = forecast
                # SAVE IMMEDIATELY
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(all_forecasts, f, ensure_ascii=False, indent=2)
                print(f"      💾 Saved {city_name} to disk.")
        return city_name, used_backend

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(worker, arg): arg[0] for arg in cities_with_params}
        for future in as_completed(futures):
            c_name = futures[future]
            try:
                res_city, res_backend = future.result()
                # print(f"  Finished {res_city} using {res_backend}") # process_city already prints
            except Exception as e:
                print(f"  Error processing {c_name}: {e}")

    # Final summary (optional, since we saved incrementally)
    print(f"\nFinished. Total forecasts in {output_path}: {len(all_forecasts)}")


if __name__ == "__main__":
    main()
