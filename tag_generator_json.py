"""
Tags JSON Generator

Létrehoz egy központi tags.json fájlt az Output mappába, ami a korábbi 3 nap
híreiből gyűjti össze a címkéket kategóriánként.
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
SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport']

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
    """Gets the first tag from a news item."""
    tags = item.get('tags', [])
    if isinstance(tags, list) and len(tags) > 0:
        tag = tags[0]
        # Clean up the tag
        if isinstance(tag, str):
            tag = tag.strip().strip('#')
            return tag if tag else None
    return None


def collect_tags_by_section(all_news):
    """Collects first tag from each news item, grouped by section."""
    section_tags = {section: set() for section in SECTIONS}
    
    for item in all_news:
        sections = get_section_from_item(item)
        first_tag = get_first_tag(item)
        
        if first_tag:
            for section in sections:
                if section in section_tags:
                    section_tags[section].add(first_tag)
    
    return section_tags


def select_random_tags(tags_set, max_count):
    """Randomly selects up to max_count tags from the set."""
    tags_list = list(tags_set)
    if len(tags_list) <= max_count:
        random.shuffle(tags_list)
        return tags_list
    return random.sample(tags_list, max_count)


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


def main():
    print(f"Tags Generator - Central tags.json")
    
    # Get past dates
    past_dates = get_past_dates()
    print(f"Looking at past {len(past_dates)} days: {past_dates}")
    
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
            # Collect tags by section
            section_tags = collect_tags_by_section(all_news)
            
            # Build result with random selection
            tags_result = {}
            for section in SECTIONS:
                tags = section_tags.get(section, set())
                selected_tags = select_random_tags(tags, MAX_TAGS_PER_SECTION)
                if selected_tags:  # Only include sections with tags
                    tags_result[section] = selected_tags
                    print(f"  {section}: {len(selected_tags)} tags")
    
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
        
        # Final validation by re-reading
        with open(TAGS_OUTPUT_PATH, 'r', encoding='utf-8') as f:
            reloaded = json.load(f)
        print("JSON validation: OK")
        
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON generated: {e}")
    except Exception as e:
        print(f"ERROR writing tags.json: {e}")


if __name__ == "__main__":
    main()
