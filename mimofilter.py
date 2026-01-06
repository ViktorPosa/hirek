import os
import feedparser
import requests
import json
from tqdm import tqdm
import concurrent.futures
from history_manager import HistoryManager
import datetime

# --- CONFIGURATION ---
INPUT_FILE = os.path.join('Input', 'input.txt')

# Use env var or default to today's date
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)
    if not os.path.exists(DAILY_OUTPUT_DIR):
        os.makedirs(DAILY_OUTPUT_DIR)

OUTPUT_FILE = os.path.join(DAILY_OUTPUT_DIR, 'output.txt')

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"
MAX_ITEMS_PER_BATCH = 15
MAX_WORKERS = 20 # Parallel feed fetching

# The specific categories requested
ALLOWED_CATEGORIES = [
    "Belföld", "Nemzetközi hír", "Gazdaság", "Tudomány", "Crypto", 
    "Technika", "Zöld hírek", "Sport", "Kultúra", "Bulvár", 
    "Életmód", "Egészség", "Gasztronómia", "Utazás", "Autó-Motor", 
    "Időjárás", "Gaming", "Vicces/Abszurd", "Podcast/Videó", "Film/Sorozat"
]

def parse_input_file(filepath):
    """Parses the input file for API Key, Prompt, and Feeds."""
    config = {
        "api_key": "",
        "prompt": "",
        "feeds": []
    }
    
    current_section = None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("API_KEY="):
                config["api_key"] = line.replace("API_KEY=", "").strip()
            elif line.startswith("PROMPT="):
                config["prompt"] = line.replace("PROMPT=", "").strip()
            elif line == "FEEDS:":
                current_section = "feeds"
            elif current_section == "feeds" and line.startswith("http"):
                config["feeds"].append(line)
                
    except FileNotFoundError:
        print(f"Error: {filepath} not found.")
        exit(1)
        
    return config

def fetch_feed_items(url):
    """Fetches and parses a single RSS feed."""
    try:
        # print(f"  Fetching: {url}") # Too verbose if many feeds, let's stick to tqdm or summary
        feed = feedparser.parse(url)
        if hasattr(feed, 'bozo_exception') and feed.bozo_exception:
             # print(f"    Warning parsing {url}: {feed.bozo_exception}")
             pass
             
        items = []
        for entry in feed.entries[:10]: # Limit to top 10 per feed to save tokens

            items.append({
                "title": entry.get('title', 'No Title'),
                "description": entry.get('description', '')[:300], # Truncate
                "link": entry.get('link', ''),
                "pubDate": entry.get('published', '')
            })
        return items
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []

def analyze_batch(api_key, user_prompt, items):
    """Sends a batch of items to Mimo API for analysis."""
    
    if not items:
        return []

    # Simplified items for the LLM
    payload_items = [{"id": idx, "title": i["title"], "desc": i["description"]} for idx, i in enumerate(items)]

    # Construct the technical system instruction merging user prompt + required formatting
    technical_instruction = f"""
    {user_prompt}

    MANDATORY OUTPUT FORMAT:
    You must return a valid JSON object with a "results" key containing an array.
    
    CATEGORIES allowed (choose exactly one per item):
    {', '.join(ALLOWED_CATEGORIES)}

    SCHEMA:
    {{
        "results": [
            {{
                "id": 0,
                "sentiment": "POSITIVE" | "NEUTRAL" | "NEGATIVE",
                "category": "One of the allowed categories"
            }}
        ]
    }}
    """

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": technical_instruction},
            {"role": "user", "content": json.dumps(payload_items)}
        ],
        "temperature": 0.1
    }

    try:
        print(f"      [Batch] Sending {len(items)} items to filtering API (Timeout: 120s)...")
        response = requests.post(API_URL, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        
        result_json = response.json()
        print(f"      [Batch] Response received.")
        
        content = result_json['choices'][0]['message']['content']
        
        # Clean up potential markdown code blocks
        if "```json" in content:
            content = content.replace("```json", "").replace("```", "")
        
        parsed = json.loads(content)
        
        analyzed_results = []
        for res in parsed.get('results', []):
            original_item = items[res['id']]
            if res.get('sentiment') in ['POSITIVE', 'NEUTRAL']:
                analyzed_results.append({
                    "category": res.get('category', 'Egyéb'),
                    "link": original_item['link'],
                    "title": original_item['title']
                })
        
        print(f"      [Batch] Found {len(analyzed_results)} positive items.")
        return analyzed_results

    except requests.exceptions.Timeout:
        print(f"      [Batch] Error: Timeout after 120s.")
        return []
    except Exception as e:
        print(f"      [Batch] API Error: {e}")

        return []

def main():
    print(f"Reading {INPUT_FILE}...")
    config = parse_input_file(INPUT_FILE)
    
    if not config["api_key"]:
        print("Error: API_KEY missing in input.txt")
        return
    if not config["feeds"]:
        print("Error: No feeds found in input.txt")
        return

from google_rss_resolver import resolve_google_news_urls_batch

# ... (rest of imports)

# ... (parse_input_file remains same)

# ... (fetch_feed_items remains same)

def main():
    print(f"Reading {INPUT_FILE}...")
    config = parse_input_file(INPUT_FILE)
    
    if not config["api_key"]:
        print("Error: API_KEY missing in input.txt")
        return
    if not config["feeds"]:
        print("Error: No feeds found in input.txt")
        return

    print(f"Found {len(config['feeds'])} feeds. Fetching...")
    
    all_rss_items = []
    
    # Fetch feeds in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(executor.map(fetch_feed_items, config['feeds']), total=len(config['feeds']), unit="feed"))
        for res in results:
            all_rss_items.extend(res)

    print(f"Initial raw articles parsed: {len(all_rss_items)}")

    # --- GOOGLE RSS RESOLVER INTEGRATION ---
    print("Resolving Google News URLs...")
    # Extract links list
    raw_links = [item['link'] for item in all_rss_items]
    
    # Resolve efficiently in batch
    resolved_links = resolve_google_news_urls_batch(raw_links, max_workers=20, show_progress=True)
    
    # Update items with real links
    for i, item in enumerate(all_rss_items):
        item['link'] = resolved_links[i]
    # ---------------------------------------

    # Deduplicate by link
    unique_items = {i['link']: i for i in all_rss_items}.values()
    all_rss_items = list(unique_items)
    
    print(f"Total unique articles found after deduplication: {len(all_rss_items)}")

    print(f"Total unique articles found: {len(all_rss_items)}")
    
    # Initialize History Manager
    history = HistoryManager()
    
    # Filter out already processed items
    items_to_process = []
    skipped_count = 0
    
    print("Checking history cache...")
    for item in all_rss_items:
        link = item['link']
        if history.is_negative(link) or history.is_positive(link):
            skipped_count += 1
            continue
        items_to_process.append(item)
        
    print(f"Skipped {skipped_count} known links. Items to analyze: {len(items_to_process)}")

    
    final_output_lines = []
    
    # Process in batches
    if items_to_process:
        for i in tqdm(range(0, len(items_to_process), MAX_ITEMS_PER_BATCH), desc="Analyzing with AI"):
            batch = items_to_process[i : i + MAX_ITEMS_PER_BATCH]
            results = analyze_batch(config["api_key"], config["prompt"], batch)
            
            # Map results back to update history
            # Result contains {category, link, title}
            # We need to implicitly handle items that were in batch but NOT in results (e.g. filtered as negative by LLM implicit logic? 
            # Wait, prompt schema returns sentiment for ALL input items. 
            # analyze_batch() currently only returns POSITIVE/NEUTRAL items.
            # We need to know about NEGATIVE items to update history.
            
            # To fix this, we should look at 'parsed' logic in analyze_batch, but that function returns a filtered list.
            # Let's trust analyze_batch returns what it deemed positive. 
            # BUT we need to mark the negatives in history too.
            # Refactoring analyze_batch might be cleaner, but for now let's assume:
            # If an item went IN the batch, and didn't come OUT as positive, it was negative.
            
            batch_links = {item['link'] for item in batch}
            positive_links = {res['link'] for res in results}
            negative_links = batch_links - positive_links
            
            for link in negative_links:
                history.update(link, status='NEGATIVE')
            
            for res in results:
                # Update history
                history.update(res['link'], status='POSITIVE')
                
                # Ensure category is in our allowed list, fallback if LLM hallucinated
                cat = res['category']
                if cat not in ALLOWED_CATEGORIES:
                    cat = "Egyéb" # Fallback
                    
                line = f"[{cat}][{res['link']}]"
                final_output_lines.append(line)
        
    print(f"New positive items processed: {len(final_output_lines)}")

    # We might want to append new items to output.txt instead of overwriting, 
    # OR overwrite with ONLY new items. 
    # Usually pipelines like this run daily. 
    # Let's save ONLY the newly found positive items to output.txt. 
    # Previous day's output.txt is already archived.
    
    # Write output

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(final_output_lines))
        print(f"\nSuccess! Found {len(final_output_lines)} positive/neutral articles.")
        print(f"Results saved to {OUTPUT_FILE}")
    except Exception as e:
        print(f"Error writing output file: {e}")

if __name__ == "__main__":
    main()