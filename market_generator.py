import os
import requests
import datetime

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = os.path.join(DAILY_OUTPUT_DIR, 'Tartalom')

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"

def load_config():
    """Loads API Key and Prompt."""
    api_key = ""
    try:
        with open(os.path.join(INPUT_DIR, 'input.txt'), 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.replace("API_KEY=", "").strip()
                    break
    except FileNotFoundError:
        print("Error: input.txt not found.")
        return None, None

    prompt = ""
    try:
        with open(os.path.join(INPUT_DIR, 'piacok_prompt.txt'), 'r', encoding='utf-8') as f:
            prompt = f.read().strip()
    except FileNotFoundError:
        print("Error: piacok_prompt.txt not found.")
        return api_key, None

    return api_key, prompt

def generate_market_analysis(api_key, prompt):
    """Sends request to API for market analysis."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    # Add today's date to prompt
    today = datetime.date.today().strftime('%Y. %m. %d.')
    full_prompt = f"Mai dátum: {today}\n\n{prompt}"

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": full_prompt}
        ],
        "temperature": 0.4,
        "stream": False 
    }

    try:
        print("Sending request to API for market analysis...")
        response = requests.post(API_URL, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        
        result_json = response.json()
        content = result_json['choices'][0]['message']['content']
        print(f"Success! Received {len(content)} chars.")
        return content

    except requests.exceptions.Timeout:
        print("ERROR: Timeout after 120s")
        return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None

def main():
    print(f"Market Generator running for: {DAILY_OUTPUT_DIR}")
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    api_key, prompt = load_config()
    if not api_key or not prompt:
        print("Configuration error. Exiting.")
        return
    
    content = generate_market_analysis(api_key, prompt)
    if content:
        output_path = os.path.join(OUTPUT_DIR, 'piacok.txt')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Saved to {output_path}")
    else:
        print("Failed to generate market analysis.")

if __name__ == "__main__":
    main()
