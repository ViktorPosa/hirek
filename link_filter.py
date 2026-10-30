import os
import re
import json
import datetime
from history_manager import HistoryManager

"""
LEÍRÁS:
Linkek előszűrése feketelista alapján.
Kiszűri a megadott kulcsszavakat (pl. politika, bűnözés, tragédia) tartalmazó URL-eket a feldolgozásból.
A kulcsszavak az Input/link_filter_keywords.json fájlból töltődnek be.

BEMENET:
- Napi kimeneti fájlok (.txt) a Output könyvtárban
- Feketelista kulcsszavak (Input/link_filter_keywords.json)

KIMENET:
- Szűrt .txt fájlok (feketelistás elemek eltávolítva)
- Frissített HistoryManager (kiszűrt elemek naplózása)
"""

# --- CONFIGURATION ---
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

KEYWORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Input', 'link_filter_keywords.json')

def _load_blacklist():
    """Load blacklist keywords from JSON file. Falls back to empty list on error."""
    if not os.path.exists(KEYWORDS_FILE):
        print(f"⚠️ link_filter: Keywords file not found: {KEYWORDS_FILE}")
        return []
    
    try:
        with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ link_filter: Error loading {KEYWORDS_FILE}: {e}")
        return []
    
    keywords = []
    for key, value in data.items():
        if key.startswith('_'):
            continue  # skip metadata fields like _description
        if isinstance(value, list):
            keywords.extend(value)
    
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if kw_lower and kw_lower not in seen:
            seen.add(kw_lower)
            unique.append(kw_lower)
    
    return unique

# Load once at module import
BLACKLIST = _load_blacklist()

def is_blacklisted(url):
    """Check if URL contains any blacklisted keyword."""
    url_lower = url.lower()
    for keyword in BLACKLIST:
        if keyword in url_lower:
            return True, keyword
    return False, None

def filter_file(filepath, history):
    """Filter out blacklisted links from a category file."""
    if not os.path.exists(filepath):
        return 0, 0
    
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    filtered_lines = []
    removed_count = 0
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Extract URL from line format: [Category][URL]
        match = re.match(r'\[.*?\]\[(.*?)\]', line)
        if match:
            url = match.group(1)
            is_bad, keyword = is_blacklisted(url)
            if is_bad:
                print(f"  [FILTERED] '{keyword}' in: {url[:80]}...")
                # Log to history.json with reason
                history.mark_filtered(url, "link_filter", f"URL contains blacklisted keyword: {keyword}")
                removed_count += 1
                continue
        
        filtered_lines.append(line)
    
    # Write back filtered content
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(filtered_lines))
    
    return len(filtered_lines), removed_count

def main():
    print(f"Link Pre-Filter running on: {DAILY_OUTPUT_DIR}")
    print(f"Keywords file: {KEYWORDS_FILE}")
    print(f"Blacklist contains {len(BLACKLIST)} keywords")
    print("-" * 50)
    
    if not os.path.exists(DAILY_OUTPUT_DIR):
        print(f"Error: Directory {DAILY_OUTPUT_DIR} not found.")
        return
    
    history = HistoryManager()
    
    # Find all .txt files in the daily output directory (not in Tartalom)
    files = [f for f in os.listdir(DAILY_OUTPUT_DIR) 
             if f.endswith('.txt') and os.path.isfile(os.path.join(DAILY_OUTPUT_DIR, f))]
    
    total_kept = 0
    total_removed = 0
    
    for filename in files:
        filepath = os.path.join(DAILY_OUTPUT_DIR, filename)
        print(f"\nProcessing {filename}...")
        kept, removed = filter_file(filepath, history)
        total_kept += kept
        total_removed += removed
        print(f"  Kept: {kept}, Removed: {removed}")
    
    print("\n" + "=" * 50)
    print(f"TOTAL: Kept {total_kept} links, Filtered out {total_removed} links")
    print("=" * 50)

if __name__ == "__main__":
    main()