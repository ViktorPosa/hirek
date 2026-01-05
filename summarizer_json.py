import os
import requests
import json
import re
from tqdm import tqdm
import time
import datetime
from history_manager import HistoryManager

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"
BATCH_SIZE = 10
MAX_RETRIES = 3

# Valid section codes
VALID_SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport']

# Required fields for each news item
REQUIRED_FIELDS = ['section', 'title', 'content', 'tags', 'image', 'sourceLink', 'author']


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


def validate_news_item(item, idx):
    """Validates a single news item and attempts to fix common issues.
    
    Returns: (fixed_item, is_valid, errors)
    """
    errors = []
    fixed_item = dict(item)
    
    # Check required fields
    for field in REQUIRED_FIELDS:
        # Special handling for image - it is optional in prompt now
        if field == 'image':
            if field not in fixed_item or fixed_item.get(field) is None:
                fixed_item[field] = ""
            # Do NOT error if image is empty
        elif not fixed_item.get(field):
            # Try to provide defaults for other fields
            if field == 'tags':
                fixed_item['tags'] = []
            else:
                fixed_item[field] = ""
                errors.append(f"Item {idx}: Missing field '{field}'")

    # Validate section
    if 'section' in fixed_item:
        section = fixed_item['section']
        # Handle if section is a list (for fooldal)
        if isinstance(section, list):
            for s in section:
                if s not in VALID_SECTIONS:
                    errors.append(f"Item {idx}: Invalid section '{s}'")
        elif section not in VALID_SECTIONS:
            # Try to map common variations
            section_map = {
                'technológia': 'tech',
                'technology': 'tech',
                'tudmány': 'tudomany',
                'science': 'tudomany',
                'belföld': 'belfold_kulfold',
                'külföld': 'belfold_kulfold',
                'belfold': 'belfold_kulfold',
                'kulfold': 'belfold_kulfold',
                'üzlet': 'uzlet',
                'business': 'uzlet',
                'szórakozás': 'szorakozas',
                'entertainment': 'szorakozas',
                'életmód': 'eletmod',
                'lifestyle': 'eletmod',
                'bulvár': 'bulvar',
            }
            if isinstance(section, str) and section.lower() in section_map:
                fixed_item['section'] = section_map[section.lower()]
            else:
                errors.append(f"Item {idx}: Invalid section '{section}'")
    
    # Validate tags is a list
    if 'tags' in fixed_item and not isinstance(fixed_item['tags'], list):
        if isinstance(fixed_item['tags'], str):
            # Try to split by comma
            fixed_item['tags'] = [t.strip().strip('#') for t in fixed_item['tags'].split(',')]
        else:
            fixed_item['tags'] = []
            errors.append(f"Item {idx}: tags should be a list")
    
    # Clean up tags - remove # prefix if present
    if 'tags' in fixed_item and isinstance(fixed_item['tags'], list):
        fixed_item['tags'] = [t.strip().strip('#') for t in fixed_item['tags']]
    
    # Validate URLs (skip image validation as it can be empty)
    for url_field in ['sourceLink']:
        if url_field in fixed_item and fixed_item[url_field]:
            url = fixed_item[url_field]
            if not url.startswith(('http://', 'https://', '')):
                errors.append(f"Item {idx}: Invalid URL in '{url_field}'")
    
    is_valid = len(errors) == 0
    return fixed_item, is_valid, errors


def validate_json_response(content):
    """Validates and parses JSON response from API.
    
    Returns: (parsed_news_list, is_valid, errors, failed_links)
    """
    errors = []
    failed_links = []
    
    # Clean up the response - remove markdown code blocks if present
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
        news_list = json.loads(content)
    except json.JSONDecodeError as e:
        # Try to fix common JSON issues
        try:
            # Fix trailing commas
            fixed_content = re.sub(r',\s*}', '}', content)
            fixed_content = re.sub(r',\s*]', ']', fixed_content)
            news_list = json.loads(fixed_content)
        except json.JSONDecodeError:
            errors.append(f"Invalid JSON: {str(e)[:100]}")
            return [], False, errors, []
    
    # Ensure it's a list
    if not isinstance(news_list, list):
        if isinstance(news_list, dict):
            news_list = [news_list]
        else:
            errors.append("Response is not a JSON array")
            return [], False, errors, []
    
    # Validate each item
    validated_news = []
    for idx, item in enumerate(news_list):
        if not isinstance(item, dict):
            errors.append(f"Item {idx} is not an object")
            continue
            
        fixed_item, is_valid, item_errors = validate_news_item(item, idx)
        if item_errors:
            errors.extend(item_errors)
            # Track failed link for retry
            if 'sourceLink' in item:
                failed_links.append(item['sourceLink'])
        
        # Keep the item even if it has minor errors (after fixing)
        if fixed_item.get('title') and fixed_item.get('content'):
            validated_news.append(fixed_item)
    
    # Consider valid if we have at least some news items
    is_valid = len(validated_news) > 0 and len(errors) < len(news_list) * 2
    
    return validated_news, is_valid, errors, failed_links


def process_batch(api_key, prompt_template, links, category_file):
    """Sends a batch of links to the API for summarization.
    
    Returns: (news_list, failed_links)
    """
    if not links:
        return [], []

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

    for attempt in range(MAX_RETRIES):
        try:
            print(f"      [Batch] Sending request to API (Timeout: 450s)... Attempt {attempt+1}/{MAX_RETRIES}")
            response = requests.post(API_URL, headers=headers, json=data, timeout=450)
            response.raise_for_status()
            
            result_json = response.json()
            content = result_json['choices'][0]['message']['content']
            print(f"      [Batch] Received {len(content)} chars.")
            
            # Validate JSON response
            news_list, is_valid, errors, failed_links = validate_json_response(content)
            
            if is_valid:
                print(f"      [Batch] Valid JSON with {len(news_list)} news items.")
                if errors:
                    print(f"      [Batch] Minor issues fixed: {len(errors)}")
                return news_list, failed_links
            else:
                print(f"      [Batch] Invalid JSON (attempt {attempt+1}/{MAX_RETRIES})")
                for err in errors[:5]:  # Show first 5 errors
                    print(f"        - {err}")
                if attempt < MAX_RETRIES - 1:
                    print("      [Batch] Retrying...")
                    time.sleep(5)
                continue

        except requests.exceptions.Timeout:
            print(f"      [Batch] ERROR: Timeout after 450s (Attempt {attempt+1}/{MAX_RETRIES})")
        except Exception as ex:
            print(f"      [Batch] ERROR: {ex} (Attempt {attempt+1}/{MAX_RETRIES})")
            
        if attempt < MAX_RETRIES - 1:
            print("      [Batch] Retrying in 10 seconds...")
            time.sleep(10)
    
    # All retries failed
    return [], links  # Return all links as failed


def process_file(filename, api_key, prompt_template, history):
    """Processes a single category file and returns news items."""
    input_path = os.path.join(OUTPUT_DIR, filename)
    
    print(f"Processing {filename}...")
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return []

    # Extract clean links
    links = []
    for line in lines:
        line = line.strip()
        if '][' in line:
            try:
                parts = line.split('][')
                if len(parts) >= 2:
                    link = parts[1].replace(']', '')
                    links.append(link)
            except:
                continue
    
    if not links:
        print(f"No valid links found in {filename}.")
        return []

    # Filter out already summarized links
    links_to_summarize = []
    for link in links:
        if history.is_summarized(link):
            continue
        links_to_summarize.append(link)
        
    if not links_to_summarize:
        print(f"  - All {len(links)} links in {filename} are already summarized.")
        return []

    # Batching
    batches = [links_to_summarize[i:i + BATCH_SIZE] for i in range(0, len(links_to_summarize), BATCH_SIZE)]
    
    print(f"  - Found {len(links_to_summarize)} new links (out of {len(links)}), creating {len(batches)} batches.")

    all_news = []
    all_failed = []

    # Process batches
    for i, batch in enumerate(tqdm(batches, desc=f"  Summarizing {filename}", unit="batch")):
        news_list, failed_links = process_batch(api_key, prompt_template, batch, filename)
        
        if news_list:
            all_news.extend(news_list)
            # Mark successful links as summarized
            successful_links = set(batch) - set(failed_links)
            for link in successful_links:
                history.update(link, summarized=True)
        
        if failed_links:
            all_failed.extend(failed_links)
            # Mark as processing error after all retries exhausted
            for link in failed_links:
                history.mark_processing_error(link, "Failed after max retries")
    
    if all_failed:
        print(f"  - Warning: {len(all_failed)} links failed processing")
    
    return all_news


def add_fooldal_section(all_news):
    """Randomly selects up to 30 news items and adds 'fooldal' to their section."""
    import random
    
    if len(all_news) <= 30:
        selected = all_news
    else:
        selected = random.sample(all_news, 30)
    
    for item in selected:
        current_section = item.get('section', '')
        if isinstance(current_section, str):
            item['section'] = [current_section, 'fooldal']
        elif isinstance(current_section, list) and 'fooldal' not in current_section:
            item['section'].append('fooldal')
    
    return all_news


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    api_key, prompt_template = load_config()
    if not api_key or not prompt_template:
        return

    history = HistoryManager()

    # Discover category files
    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.txt') and f != 'output.txt']
    
    print(f"Found {len(files)} category files to process.")

    # Collect all news from all files
    all_news = []
    
    for filename in files:
        news_items = process_file(filename, api_key, prompt_template, history)
        all_news.extend(news_items)

    if not all_news:
        print("No news items generated.")
        return
    
    print(f"\nTotal news items collected: {len(all_news)}")
    
    # Add fooldal section to random 30 items
    all_news = add_fooldal_section(all_news)
    
    # Write unified data.json
    output_path = os.path.join(OUTPUT_DIR, 'data.json')
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        print(f"\nSaved {len(all_news)} news items to {output_path}")
    except Exception as e:
        print(f"Error writing data.json: {e}")

    print("\nAll processing complete.")


if __name__ == "__main__":
    main()
