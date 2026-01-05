import os
import re
import datetime
from history_manager import HistoryManager

# --- CONFIGURATION ---
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

# Blacklist keywords (URL-safe, lowercase, no accents)
# These will be checked as substrings in the URL
BLACKLIST = [
    # Politics - Hungarian
    "orban", "magyar-peter", "meszaros", "pinter-sandor", "fidesz",
    "valasztas", "parlament", "miniszter",
    # Politics - International  
    "trump", "putin", "putyin", "politika", "politics", "election",
    # War/Conflict
    "haboru", "war", "ukrajna", "ukran", "oroszorszag", "russia", 
    "venezuela", "hadsereg", "konfliktus",
    # Death/Tragedy
    "halal", "meghalt", "halott", "death", "murder", "gyilkossag", 
    "eltunt", "holttest", "aldozat",
    # Disasters
    "katasztrofa", "catastrophe", "tragedy", "tragedia",
    "tuz-utott", "tuzvesz", "fire-broke", "pandemic",
    "virus", "betegseg", "jarvany",
    # Crime
    "borton", "prison", "buncselekmeny", "crime", "letartoztatas",
    # Crisis
    "krizis", "crisis", "valsag",
    # Other negative
    "rejtveny",  # User specified
]

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
