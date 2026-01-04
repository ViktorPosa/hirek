import os
import requests
import json
import concurrent.futures
from tqdm import tqdm
import time
from history_manager import HistoryManager


# --- CONFIGURATION ---
import datetime

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR
OUTPUT_TARTALOM_DIR = os.path.join(OUTPUT_DIR, 'Tartalom')

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"
BATCH_SIZE = 10
MAX_WORKERS = 1  # Sequential processing to avoid rate limits

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

def process_batch(api_key, prompt_template, links, category_file):
    """Sends a batch of links to the API for summarization."""
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
        "temperature": 0.3, # Slightly creative but factual
        "stream": False 
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"      [Batch] Sending request to API (Timeout: 450s)...")
            # Increase timeout significantly as summarization might take time
            # User requested min 7 minutes (420s). Setting to 450s for safety.
            response = requests.post(API_URL, headers=headers, json=data, timeout=450)
            response.raise_for_status()
            
            result_json = response.json()
            content = result_json['choices'][0]['message']['content']
            print(f"      [Batch] Success! Received {len(content)} chars.")
            return content

        except requests.exceptions.Timeout:
            print(f"      [Batch] ERROR: Timeout after 450s (Attempt {attempt+1}/{max_retries})")
        except Exception as ex: # Rename to avoid conflict if any, though 'e' is standard
            print(f"      [Batch] ERROR: {ex} (Attempt {attempt+1}/{max_retries})")
            error_msg = str(ex)
            
        if attempt < max_retries - 1:
            print("      [Batch] Retrying in 10 seconds...")
            time.sleep(10)  # Wait before retry
        else:
            return f"[ERROR PROCESSING BATCH in {category_file}]\nLinks:\n{links_str}\nError: {error_msg}\n"


def process_file(filename, api_key, prompt_template, history):

    """Processes a single category file."""
    input_path = os.path.join(OUTPUT_DIR, filename)
    output_path = os.path.join(OUTPUT_TARTALOM_DIR, filename)
    
    print(f"Processing {filename}...")
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return

    # Extract clean links (assuming format [Category][Link])
    links = []
    for line in lines:
        line = line.strip()
        if '][' in line:
            try:
                # Extract link part: [Cat][Link] -> Link] -> Link
                # Using simple split to be robust
                parts = line.split('][')
                if len(parts) >= 2:
                    link = parts[1].replace(']', '')
                    links.append(link)
            except:
                continue
    
    if not links:
        print(f"No valid links found in {filename}.")
        return

    # Filter out already summarized links
    links_to_summarize = []
    for link in links:
        if history.is_summarized(link):
            # print(f"  - Skipping already summarized: {link}")
            continue
        links_to_summarize.append(link)
        
    if not links_to_summarize:
        print(f"  - All {len(links)} links in {filename} are already summarized.")
        return

    # Batching
    batches = [links_to_summarize[i:i + BATCH_SIZE] for i in range(0, len(links_to_summarize), BATCH_SIZE)]
    
    print(f"  - Found {len(links_to_summarize)} new links (out of {len(links)}), creating {len(batches)} batches.")

    # Process batches
    for i, batch in enumerate(tqdm(batches, desc=f"  Summarizing {filename}", unit="batch")):
        summary = process_batch(api_key, prompt_template, batch, filename)
        
        # If successfully processed, update history for all links in this batch
        # process_batch returns a string content. If it failed, it might return an error string.
        # But process_batch handles retries and returns string. 
        # Ideally we should only update history if successful.
        # The current process_batch returns error string on failure.
        
        if summary and not summary.startswith("[ERROR"):
            for link in batch:
                history.update(link, summarized=True)
                
            # Append immediately to avoid data loss?
            # The current logic collects all_summaries and writes at END.
            # If we want to support incremental, we should append.
            # But the requirement was just "build in an rss link filtering... check that cache".
            # The existing logic overwrites the file. 
            # If we skip already summarized links, we won't produce summaries for them again.
            # BUT we enter a problem: If we skip them, they won't be in the output file if we overwrite it!
            # The user's goal is "don't send links to summerize...".
            # If `content/Tartalom/Tech.txt` already exists, we should probably APPEND new summaries?
            # Or assume the file is cumulative?
            # The current `sorter.py` overwrites `Output/Tech.txt` every run?
            # Actually valid point: `sorter.py` takes `output.txt` and splits it. 
            # `mimofilter.py` overwrites `output.txt` with NEW links (based on my previous edit).
            # So `sorter.py` will create `Tech.txt` with only NEW links.
            # So `summarizer.py` will see `Tech.txt` with NEW links.
            # So generally they shouldn't be summarized yet.
            # BUT if the script crashed halfway, sorting again might re-introduce them?
            # Or if `output.txt` wasn't cleared.
            
            # Since `mimofilter.py` now overwrites `output.txt` with ONLY new positive links, 
            # the downstream files will also mostly contain new links.
            # Checking `history.is_summarized` is a safety net.
            
            # Appending to the existing file in `Tartalom/` is safer than keeping all in memory.
            try:
                with open(output_path, 'a', encoding='utf-8') as f: # Append mode
                     # Add separator if file not empty?
                     if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                         f.write("\n\n---\n\n")
                     f.write(summary)
            except Exception as e:
                print(f"Error appending to {output_path}: {e}")

        
        # time.sleep(1) # Sleep handled by tqdm or standard op


def main():
    if not os.path.exists(OUTPUT_TARTALOM_DIR):
        os.makedirs(OUTPUT_TARTALOM_DIR)

    api_key, prompt_template = load_config()
    if not api_key or not prompt_template:
        return

    history = HistoryManager()


    # Discover files
    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.txt') and f != 'output.txt']
    
    print(f"Found {len(files)} category files to process.")

    # Process files
    for filename in files:
        # We need to pass history to process_file, or just instantiate it inside
        # Refactoring process_file to handle history check would mean parsing links there.
        # It already parses links. Let's modify process_file to take history object.
        process_file(filename, api_key, prompt_template, history)


    print("\nAll processing complete.")

if __name__ == "__main__":
    main()
