import os
import re
import requests
import time
from tqdm import tqdm

import sys
import datetime

# --- CONFIGURATION ---
# Configuration
INPUT_FILE = os.path.join('Input', 'input.txt')
INPUT_DIR = 'Input'


DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR
TARTALOM_DIR = os.path.join(OUTPUT_DIR, 'Tartalom')

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"

# Valid categories and their target files
CATEGORY_MAP = {
    'Tech': 'tech.txt',
    'Tudomány': 'tudomany.txt',
    'Belföld/Külföld': 'belfold_kulfold.txt',
    'Sport': 'sport.txt',
    'Kultúra/Szórakozás': 'szorakozas.txt',
    'Életmód': 'eletmod.txt',
    'Üzlet': 'uzlet.txt',
    'Bulvár': 'bulvar.txt',
    'Egyéb': 'egyeb.txt'
}

# The files in TARTALOM_DIR to process
# We scan the directory instead of hardcoding, but map categories to filenames
# when doing fix-ups.

def load_config():
    """Loads API Key and Prompt."""
    api_key = ""
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.replace("API_KEY=", "").strip()
                    break
    except FileNotFoundError:
        print("Error: input.txt not found.")
        return None, None

    prompt_template = ""
    try:
        with open(os.path.join(INPUT_DIR, 'fix_category_prompt.txt'), 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        print("Error: fix_category_prompt.txt not found.")
        return None, None

    return api_key, prompt_template

def clean_text(text):
    """Removes ** and --- and extra whitespace."""
    text = text.replace('**', '').replace('---', '')
    # Remove multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def extract_category(article_text):
    """Extracts category from [Hírszekció]: ..."""
    match = re.search(r'\[Hírszekció\]:\s*(.*?)(\n|$)', article_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

def fix_category_with_api(api_key, prompt_template, article_text):
    """Calls API to fix the category."""
    final_prompt = prompt_template.replace("{article}", article_text)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": final_prompt}
        ],
        "temperature": 0.1 # Deterministic
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        result_json = response.json()
        content = result_json['choices'][0]['message']['content']
        return content.strip()
    except Exception as e:
        print(f"  API Error fixing category: {e}")
        return None

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    api_key, prompt_template = load_config()
    if not api_key:
        return

    all_articles = []
    
    # 1. Read all files
    # 1. Read all files
    print(f"Reading files from {TARTALOM_DIR}...")
    if not os.path.exists(TARTALOM_DIR):
        print(f"Error: Directory {TARTALOM_DIR} does not exist.")
        return

    files = [f for f in os.listdir(TARTALOM_DIR) if f.endswith('.txt')]
    for filename in files:
        path = os.path.join(TARTALOM_DIR, filename)

        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Split by [Hírszekció] to get individual articles
            # The file contains multiple batches separated by ---, and each batch has multiple articles.
            # We first clean the entire content of batch separators, then split by article start.
            
            # Remove the batch separator used by summarizer
            content = content.replace('\n\n---\n\n', '\n')
            
            # Use regex to find all article starts
            # We assume every article starts with [Hírszekció]:
            # We use a lookahead to split but keep the delimiter, or manually find iterators.
            
            # Splits string at every occurrence of [Hírszekció]:
            parts = re.split(r'(?=\[Hírszekció\]:)', content)
            
            for item in parts:
                cleaned = clean_text(item)
                if not cleaned or len(cleaned) < 50: # Skip empty or junk
                    continue
                if '[Hírszekció]:' not in cleaned: # Skip preamble text
                    continue
                    
                all_articles.append(cleaned)
                
        except Exception as e:
            print(f"Error reading {filename}: {e}")

    print(f"Found {len(all_articles)} total articles.")

    # 2. Sort and Fix
    buckets = {filename: [] for filename in CATEGORY_MAP.values()}
    buckets['egyeb.txt'] = [] # Fallback

    # Normalize category map for fuzzy matching
    NORM_MAP = {}
    for k, v in CATEGORY_MAP.items():
        NORM_MAP[k.lower()] = v
        # Special cases
        if 'belföld' in k.lower():
            NORM_MAP['belföld'] = v
            NORM_MAP['külföld'] = v
        
    print("Sorting and fixing categories...")
    
    # Debug counter
    fixed_count = 0
    api_fix_count = 0
    
    for article in tqdm(all_articles):
        cat = extract_category(article)
        cat_norm = cat.lower().strip() if cat else ""
        
        target_file = None
        
        # 1. Direct Match
        if cat in CATEGORY_MAP:
             target_file = CATEGORY_MAP[cat]
             buckets[target_file].append(article)
             
        # 2. Fuzzy/Normalized Match
        elif cat_norm in NORM_MAP:
             target_file = NORM_MAP[cat_norm]
             buckets[target_file].append(article)
             if fixed_count < 5: print(f"  Fuzzy match: '{cat}' -> {target_file}")
             fixed_count += 1
             
        # 3. API Fix
        else:
            # Invalid category, try to fix
            print(f"  Invalid category '{cat}'. Fixing via API...")
            fixed_article = fix_category_with_api(api_key, prompt_template, article)
            api_fix_count += 1
            
            if fixed_article:
                # Clean the fixed article just in case
                fixed_article = clean_text(fixed_article)
                new_cat = extract_category(fixed_article)
                # Normalize new cat check too
                new_cat_norm = new_cat.lower().strip() if new_cat else ""
                
                if new_cat in CATEGORY_MAP:
                    print(f"    -> Fixed to '{new_cat}'")
                    target_file = CATEGORY_MAP[new_cat]
                    buckets[target_file].append(fixed_article)
                elif new_cat_norm in NORM_MAP:
                     target_file = NORM_MAP[new_cat_norm]
                     print(f"    -> Fixed (fuzzy) to '{target_file}'")
                     buckets[target_file].append(fixed_article)
                else:
                    print(f"    -> Failed to fix (got '{new_cat}'). Moving to Egyéb.")
                    buckets['egyeb.txt'].append(article) 
            else:
                 print("    -> API failed. Moving to Egyéb.")
                 buckets['egyeb.txt'].append(article)
                 
    print(f"Done. Fuzzy fixes: {fixed_count}, API fixes: {api_fix_count}")

    # 3. Write back
    print("Writing processed files...")
    for filename, articles in buckets.items():
        if not articles:
            continue
            
        if not articles:
            continue
            
        path = os.path.join(TARTALOM_DIR, filename)

        # We join with just \n\n as requested "csak a hír maradjon" 
        # But readable separation is good. User said "Szedje ki az extra ... --- -is". 
        # So probably just newlines between fields are preserved, but between articles we need some separation or just \n\n.
        # Standard block separation:
        # User requested specific format: NO COLONS in headers. 
        # The clean_text removed ** and ---.
        # But extracted articles still have [Header]: content.
        # We need to strip those colons before writing.
        
        final_articles = []
        for art in articles:
            # Replace [Header]: with [Header]
            art = re.sub(r'^\[(Hírszekció|Cím|Tagek|Tartalom|Forráslink|Hír szerzője)\]:\s*', r'[\1] ', art, flags=re.MULTILINE)
            # Also fix image link if present
            art = re.sub(r'^\{\{kép linkje\}\}:\s*', r'{{kép linkje}} ', art, flags=re.MULTILINE)
            
            # User requested hashtags in [Tagek]
            def add_hashtags(match):
                content = match.group(1)
                if not content: return match.group(0)
                tags = [t.strip() for t in content.split(',')]
                new_tags = [f"#{t}" if not t.startswith('#') else t for t in tags]
                return f"[Tagek] {', '.join(new_tags)}"
            
            art = re.sub(r'^\[Tagek\]\s*(.*)', add_hashtags, art, flags=re.MULTILINE)
            
            final_articles.append(art)


        content_str = "\n\n".join(final_articles)
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content_str)
            print(f"  Saved {len(articles)} articles to {filename}")
        except Exception as e:
             print(f"Error writing {filename}: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"CRITICAL ERROR in post_processor.py: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

