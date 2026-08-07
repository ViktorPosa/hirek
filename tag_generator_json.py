"""
LEÍRÁS:
Központi Címkefelhő (Tags) Generátor.
Összegyűjti az elmúlt 3 nap híreinek címkéit az Output mappákból.
Létrehoz egy központi 'Output/tags.json' fájlt, amely kategóriánként tartalmazza a releváns címkéket.
A weboldal ezt használja a keresési javaslatokhoz vagy címkefelhőhöz.

BEMENET:
- Output/[YYYY-MM-DD]/data.json fájlok (múltbeli adatok)

KIMENET:
- Output/tags.json (Összesített címkék)
"""


import os
import json
import random
import datetime

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'

# Output is the central tags.json file, not in daily folders
TAGS_OUTPUT_PATH = os.path.join(BASE_OUTPUT_DIR, 'tags.json')

# Valid sections
SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'gamer', 'kripto', 'eletmod', 'bulvar', 'sport']

# Max tags per section
MAX_TAGS_PER_SECTION = 20

# Number of past days to look back
DAYS_TO_LOOK_BACK = 3


def get_past_dates(days_back=DAYS_TO_LOOK_BACK):
    """Returns list of past N dates that have data.json files."""
    past_dates = []
    today = datetime.date.today()
    
    if not os.path.exists(BASE_OUTPUT_DIR):
        return []
    
    # Get all date folders
    all_date_folders = []
    for folder in os.listdir(BASE_OUTPUT_DIR):
        folder_path = os.path.join(BASE_OUTPUT_DIR, folder)
        if folder == 'Ajanlott' or not os.path.isdir(folder_path):
            continue
        
        try:
            folder_date = datetime.datetime.strptime(folder, '%Y-%m-%d').date()
            # Only include dates before today
            if folder_date < today:
                data_path = os.path.join(folder_path, 'data.json')
                if os.path.exists(data_path):
                    all_date_folders.append((folder_date, folder))
        except ValueError:
            continue
    
    # Sort by date descending and take the last N days
    all_date_folders.sort(key=lambda x: x[0], reverse=True)
    past_dates = [folder for _, folder in all_date_folders[:days_back]]
    
    return past_dates


def load_news_from_dates(date_folders):
    """Loads all news items from the specified date folders."""
    all_news = []
    
    for date_str in date_folders:
        data_path = os.path.join(BASE_OUTPUT_DIR, date_str, 'data.json')
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                news_list = json.load(f)
                if isinstance(news_list, list):
                    all_news.extend(news_list)
        except Exception as e:
            print(f"  Warning: Could not load {data_path}: {e}")
    
    return all_news


def get_section_from_item(item):
    """Gets section(s) from a news item, handling both string and list formats."""
    section = item.get('section', '')
    if isinstance(section, list):
        return section
    return [section] if section else []


def get_first_tag(item):
    """Gets the first non-source tag from a news item.
    Skips tags that look like a domain/portal name derived from sourceLink or author.
    """
    tags = item.get('tags', [])
    if not (isinstance(tags, list) and tags):
        return None
    source_link = item.get('sourceLink', '')
    author = item.get('author', '')

    # Build a set of source-hint words to skip (domain parts, author words)
    skip_hints = set()
    if source_link:
        try:
            from urllib.parse import urlparse
            netloc = urlparse(source_link).netloc.lower().removeprefix('www.')
            # e.g. "portfolio.hu" → {"portfolio", "hu", "portfolio.hu"}
            sld = netloc.split('.')[0] if '.' in netloc else netloc
            skip_hints.add(sld)
            skip_hints.add(netloc)
        except Exception:
            pass
    if author:
        for word in author.lower().split():
            if len(word) > 3:
                skip_hints.add(word)

    for tag in tags:
        if not isinstance(tag, str):
            continue
        clean = tag.strip().strip('#').strip()
        if not clean:
            continue
        if clean.lower() in skip_hints:
            continue
        return clean
    return None



def collect_tags_by_section(all_news):
    """Collects first tag from each news item, grouped by section. Returns LISTS (with dupes)."""
    section_tags = {section: [] for section in SECTIONS}
    
    for item in all_news:
        sections = get_section_from_item(item)
        first_tag = get_first_tag(item)
        
        if first_tag:
            for section in sections:
                if section in section_tags:
                    section_tags[section].append(first_tag)
    
    return section_tags


def select_top_tags_with_random_fallback(tags_list, max_count):
    """
    Selects top tags by frequency. 
    If there's a tie at the cutoff, randomly select to fill quota.
    """
    from collections import Counter
    if not tags_list: return []

    counts = Counter(tags_list)
    freq_groups = {}
    for tag, count in counts.items():
        if count not in freq_groups: freq_groups[count] = []
        freq_groups[count].append(tag)
    
    sorted_freqs = sorted(freq_groups.keys(), reverse=True)
    selected_tags = []
    
    for freq in sorted_freqs:
        tags_at_level = freq_groups[freq]
        if len(selected_tags) + len(tags_at_level) <= max_count:
            random.shuffle(tags_at_level)
            selected_tags.extend(tags_at_level)
        else:
            remaining = max_count - len(selected_tags)
            if remaining > 0:
                random.shuffle(tags_at_level)
                selected_tags.extend(tags_at_level[:remaining])
            break
        if len(selected_tags) == max_count: break
            
    return selected_tags


def validate_tags_json(tags_dict):
    """Validates the tags JSON structure."""
    errors = []
    
    if not isinstance(tags_dict, dict):
        errors.append("Root should be an object/dict")
        return False, errors
    
    for section, tags in tags_dict.items():
        if section not in SECTIONS:
            errors.append(f"Unknown section: {section}")
        
        if not isinstance(tags, list):
            errors.append(f"Section '{section}' value should be a list")
            continue
        
        for tag in tags:
            if not isinstance(tag, str):
                errors.append(f"Tag in '{section}' should be a string: {tag}")
            elif not tag.strip():
                errors.append(f"Empty tag found in '{section}'")
    
    return len(errors) == 0, errors


def process_daily_fooldal_tags(date_folder):
    """
    Processes a specific date folder's data.json to add 'fooldal' tags.
    New Logic (Global Quota):
    - Goal: 40 items total.
    - Priority A: 20 items from Importance 5. (Fallback: Importance 3)
    - Priority B: 20 items from Importance 4. (Fallback: Importance 3)
    - 'fooldal' tag is APPENDED to the end of the tag list.
    """
    date_path = os.path.join(BASE_OUTPUT_DIR, date_folder)
    data_path = os.path.join(date_path, 'data.json')
    
    if not os.path.exists(data_path):
        return

    print(f"Processing 'fooldal' tags for {date_folder}...")
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_list = json.load(f)
    except Exception as e:
        print(f"  Error loading {data_path}: {e}")
        return

    if not news_list:
        return

    # 1. Clear existing 'fooldal' tags from ALL items to start fresh
    for item in news_list:
        tags = item.get('tags', [])
        if isinstance(tags, list):
            # Remove 'fooldal' (case-insensitive check, keep others)
            item['tags'] = [t for t in tags if isinstance(t, str) and t.lower() != 'fooldal']

    # 2. Group items by importance
    i5_items = []
    i4_items = []
    i3_items = []

    for item in news_list:
        try:
            imp = int(item.get('importance', 3))
        except:
            imp = 3
        
        # We can shuffle strictly or rely on list order. Random shuffle is better for variety if > 20.
        if imp == 5:
            i5_items.append(item)
        elif imp == 4:
            i4_items.append(item)
        else:
            # Group 1, 2, 3 together as fallback? User said "importance 3". 
            # Assuming items with imp < 3 are low quality, but let's include all non-4/5 in "rest".
            i3_items.append(item)

    # Shuffle for fairness
    random.shuffle(i5_items)
    random.shuffle(i4_items)
    random.shuffle(i3_items)

    selected_ids = set() # To avoid duplicates if object logic gets weird, though list split prevents it.
    final_selected = []

    # Helper to pick N items
    def pick_items(source_list, count):
        picked = []
        for x in source_list:
            # Assuming object identity works for dedupe (should be fine as lists are distinct)
            if id(x) not in selected_ids:
                picked.append(x)
                selected_ids.add(id(x))
                if len(picked) == count:
                    break
        return picked

    # Target A: 20 items (Prefer i5 -> i3)
    quota_a = 20
    from_i5 = pick_items(i5_items, quota_a)
    final_selected.extend(from_i5)
    
    shortfall_a = quota_a - len(from_i5)
    if shortfall_a > 0:
        print(f"  Shortage in Importance 5 ({len(from_i5)}/20). Filling {shortfall_a} from Importance 3/Rest.")
        from_i3_a = pick_items(i3_items, shortfall_a)
        final_selected.extend(from_i3_a)

    # Target B: 20 items (Prefer i4 -> i3)
    quota_b = 20
    from_i4 = pick_items(i4_items, quota_b)
    final_selected.extend(from_i4)
    
    shortfall_b = quota_b - len(from_i4)
    if shortfall_b > 0:
        print(f"  Shortage in Importance 4 ({len(from_i4)}/20). Filling {shortfall_b} from Importance 3/Rest.")
        # Note: i3_items might be depleted by shortfall_a, so pick_items continues correctly
        from_i3_b = pick_items(i3_items, shortfall_b)
        final_selected.extend(from_i3_b)

    # Apply tag
    total_fooldal_added = 0
    for item in final_selected:
        tags = item.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        
        # We already cleaned 'fooldal' at step 1.
        tags.append('fooldal')
        item['tags'] = tags
        total_fooldal_added += 1

    print(f"  Selected {total_fooldal_added} items for 'fooldal' tag (Target: 40).")
    
    if total_fooldal_added > 0:
        try:
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(news_list, f, ensure_ascii=False, indent=2)
            print(f"  Updated {data_path} successfully.")
        except Exception as e:
            print(f"  Error saving {data_path}: {e}")
    else:
        print("  No items selected.")


def generate_most_used_tags(max_days=10):
    """
    Generates 'Output/mostused_tags.json' containing the top 100 most frequently used tags.
    
    Logic:
    - Look back for UP TO 'max_days' ACTIVE days (folders that actually exist and contain data).
      (Skips missing days, keeps looking further back until limit is met or dates run out).
    - Collect ALL tags from every item.
    - Exclude 'fooldal' (case-insensitive).
    - Count frequency.
    - Sort desc -> Top 100.
    - Handle ties with random selection.
    """
    print(f"\nGenerating mostused_tags.json (Using last {max_days} active days)...")
    
    # 1. Find last N active folders
    all_dates = []
    
    # Scan directory for valid date folders
    if os.path.exists(BASE_OUTPUT_DIR):
        candidates = []
        for d_str in os.listdir(BASE_OUTPUT_DIR):
            path = os.path.join(BASE_OUTPUT_DIR, d_str, 'data.json')
            if os.path.isdir(os.path.join(BASE_OUTPUT_DIR, d_str)) and os.path.exists(path):
                # Verify it's a date format
                try:
                    datetime.datetime.strptime(d_str, '%Y-%m-%d')
                    candidates.append(d_str)
                except ValueError:
                    pass
        
        # Sort descending (newest first)
        candidates.sort(reverse=True)
        # Take top N
        all_dates = candidates[:max_days]
            
    if not all_dates:
        print("  No data files found.")
        # Create empty file
        output_path = os.path.join(BASE_OUTPUT_DIR, 'mostused_tags.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return

    print(f"  Using data from: {all_dates}")

    # Load All Tags
    all_tags_flat = []
    
    # Track display versions of tags to pick the most common casing (optional UI polish)
    # or strictly lowercase as requested. "ne legyen különbség" -> grouping.
    # Let's group by lowercase, but display the most frequent casing for UI?
    # User just said "normalize". Let's stick to strict lowercase for safety,
    # OR better: group by lowercase, but output the capitalized version if it's a proper noun like 'Bitcoin'.
    # Actually, simpler is better: Lowercase everything for counting. 
    # But for UI "bitcoin" looks worse than "Bitcoin".
    # Strategy: Count by lowercase key. Keep a separate frequency map of raw tags.
    # Use the most frequent raw tag for the display name.
    
    raw_tag_counts = {} # { "Bitcoin": 10, "bitcoin": 5 }
    
    for date_str in all_dates:
        path = os.path.join(BASE_OUTPUT_DIR, date_str, 'data.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                items = json.load(f)
                for item in items:
                    tags = item.get('tags', [])
                    if isinstance(tags, list):
                        for t in tags:
                            if isinstance(t, str) and t.strip():
                                # Clean: remove hash, strip
                                t_clean = t.strip().replace('#', '')
                                
                                # EXCLUDE 'fooldal' (case insensitive)
                                if t_clean.lower() == 'fooldal':
                                    continue
                                
                                if t_clean:
                                    # Update raw counts
                                    raw_tag_counts[t_clean] = raw_tag_counts.get(t_clean, 0) + 1
        except Exception as e:
            print(f"  Warning: Failed to load {path}: {e}")

    if not raw_tag_counts:
        print("  No valid tags found in loaded files.")
        return

    # Consolidate by lowercase
    grouped_counts = {} # { "bitcoin": 15 }
    lower_to_display = {} # { "bitcoin": "Bitcoin" } (the most frequent casing)
    
    # Helper to find best casing
    casing_candidates = {} # { "bitcoin": { "Bitcoin": 10, "bitcoin": 5 } }
    
    for raw_tag, count in raw_tag_counts.items():
        lower = raw_tag.lower()
        grouped_counts[lower] = grouped_counts.get(lower, 0) + count
        
        if lower not in casing_candidates:
            casing_candidates[lower] = {}
        casing_candidates[lower][raw_tag] = count
        
    # Pick best display casing (max count)
    for lower, candidates in casing_candidates.items():
        best_casing = max(candidates.items(), key=lambda x: x[1])[0]
        lower_to_display[lower] = best_casing

    # Group by frequency for smart selection (using grouped counts)
    freq_map = {} # {count: [lower_tag1, lower_tag2]}
    for lower, count in grouped_counts.items():
        if count not in freq_map:
            freq_map[count] = []
        freq_map[count].append(lower)
        
    # Sort frequencies desc
    sorted_freqs = sorted(freq_map.keys(), reverse=True)
    
    final_list = []
    limit = 100  # Updated limit
    
    for freq in sorted_freqs:
        lower_tags_at_this_level = freq_map[freq]
        
        # Shuffle for random fallback in ties
        random.shuffle(lower_tags_at_this_level)
        
        # Convert tags to objects with count AND restore best casing
        objs_at_this_level = [
            {'tag': lower_to_display[t], 'count': freq} 
            for t in lower_tags_at_this_level
        ]
        
        remaining_slots = limit - len(final_list)
        
        if len(objs_at_this_level) <= remaining_slots:
            final_list.extend(objs_at_this_level)
        else:
            # We need to pick 'remaining_slots' items random from this level
            selected = objs_at_this_level[:remaining_slots]
            final_list.extend(selected)
            break
            
        if len(final_list) >= limit:
            break
            
    # Save Output
    output_path = os.path.join(BASE_OUTPUT_DIR, 'mostused_tags.json')
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, ensure_ascii=False, indent=2)
        print(f"  ✅ Saved {len(final_list)} tags to {output_path}")
    except Exception as e:
        print(f"  ❌ Error saving mostused_tags.json: {e}")


def main():
    print(f"Tags Generator - Central tags.json & Main Page Selection")
    
    # 1. Process TODAY's news for 'fooldal' tag
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    process_daily_fooldal_tags(today_str)
    
    # 2. Most Used Tags Generation (Top 100, Last 10 Active Days)
    generate_most_used_tags(max_days=10)
    
    # 3. Get past dates for tags.json generation (Legacy)
    past_dates = get_past_dates()
    print(f"\nGenerators Legacy Cloud (tags.json) - Last {len(past_dates)} days: {past_dates}")
    
    if not past_dates:
        print("No past dates with data.json found. Creating empty tags.json.")
        tags_result = {section: [] for section in SECTIONS}
    else:
        # Load all news from past dates
        all_news = load_news_from_dates(past_dates)
        print(f"Loaded {len(all_news)} news items from past days")
        
        if not all_news:
            print("No news items found. Creating empty tags.json.")
            tags_result = {section: [] for section in SECTIONS}
        else:
            # Collect tags by section (Duplicates included)
            section_tags = collect_tags_by_section(all_news)
            
            # Build result with frequency logic
            tags_result = {}
            for section in SECTIONS:
                tags = section_tags.get(section, [])
                # Use new frequency-based selection
                selected_tags = select_top_tags_with_random_fallback(tags, MAX_TAGS_PER_SECTION)
                if selected_tags:
                    tags_result[section] = selected_tags
                    print(f"  {section}: {len(selected_tags)} tags (from {len(tags)} occurrences)")
    
    # Validate JSON
    is_valid, errors = validate_tags_json(tags_result)
    if not is_valid:
        print(f"Validation errors: {errors}")
        # Try to fix by removing problematic entries
        for section in list(tags_result.keys()):
            if section not in SECTIONS:
                del tags_result[section]
            else:
                tags_result[section] = [t for t in tags_result[section] if isinstance(t, str) and t.strip()]
    
    # Ensure output directory exists
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    
    # Write tags.json to central location
    try:
        with open(TAGS_OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(tags_result, f, ensure_ascii=False, indent=2)
        print(f"\nSaved tags.json to {TAGS_OUTPUT_PATH}")
        
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON generated: {e}")
    except Exception as e:
        print(f"ERROR writing tags.json: {e}")


if __name__ == "__main__":
    main()
