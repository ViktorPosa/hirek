import os
import requests
import json
import time
from tqdm import tqdm

# --- CONFIGURATION ---
INPUT_DIR = 'Input'
OUTPUT_DIR = os.path.join('Output', 'Tartalom')
MISSING_FILE = os.path.join('Output', 'missing_links.txt')
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'recovered.txt')
API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"
BATCH_SIZE = 5

def load_config():
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

    prompt_template = ""
    try:
        with open(os.path.join(INPUT_DIR, 'summarize.txt'), 'r', encoding='utf-8') as f:
            content = f.read()
            if "[gemini_summarize]" in content:
                prompt_template = content.split("[gemini_summarize]")[1].strip()
            else:
                prompt_template = content
    except FileNotFoundError:
        print("Error: summarize.txt not found.")
        return None, None

    return api_key, prompt_template

def process_batch(api_key, prompt_template, links):
    if not links:
        return ""

    links_str = "\n".join(links)
    final_prompt = prompt_template.replace("{links}", links_str)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": final_prompt}
        ],
        "temperature": 0.3, 
        "stream": False 
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=data, timeout=180)
            response.raise_for_status()
            
            result_json = response.json()
            content = result_json['choices'][0]['message']['content']
            return content

        except Exception as e:
            print(f"Error processing batch (Attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                return f"[ERROR PROCESSING RECOVERY BATCH]\nLinks:\n{links_str}\nError: {e}\n"

def main():
    if not os.path.exists(MISSING_FILE):
        print("No missing_links.txt found.")
        return

    api_key, prompt_template = load_config()
    if not api_key:
        return

    with open(MISSING_FILE, 'r', encoding='utf-8') as f:
        links = [l.strip() for l in f.readlines() if l.strip()]

    print(f"Found {len(links)} missing links to recover.")
    
    batches = [links[i:i + BATCH_SIZE] for i in range(0, len(links), BATCH_SIZE)]
    
    all_summaries = []
    for batch in tqdm(batches, desc="Recovering batches"):
        summary = process_batch(api_key, prompt_template, batch)
        all_summaries.append(summary)
        time.sleep(2)

    # Append to recovered.txt (create if needed)
    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
         f.write("\n\n---\n\n".join(all_summaries))
         f.write("\n\n---\n\n") 
    
    print(f"Saved recovered articles to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
