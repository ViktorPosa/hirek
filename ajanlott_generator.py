"""
Ajánlott Hírek Generátor

Ez a script létrehozza az ajánlott híreket minden kategóriához úgy, hogy
a korábbi napok data.json fájljaiból random választ ki híreket.
"""

import os
import json
import random
import datetime

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'
AJANLOTT_DIR = os.path.join(BASE_OUTPUT_DIR, 'Ajanlott')

# Valid sections
SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport']

# Number of recommended items per category
ITEMS_PER_CATEGORY = 20


def get_past_dates():
    """Returns list of past dates that have data.json files."""
    past_dates = []
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    if not os.path.exists(BASE_OUTPUT_DIR):
        return []
    
    for folder in os.listdir(BASE_OUTPUT_DIR):
        folder_path = os.path.join(BASE_OUTPUT_DIR, folder)
        # Skip Ajanlott folder and check if it's a date folder
        if folder == 'Ajanlott' or not os.path.isdir(folder_path):
            continue
        
        # Check if folder name looks like a date (YYYY-MM-DD)
        try:
            datetime.datetime.strptime(folder, '%Y-%m-%d')
        except ValueError:
            continue
        
        # Skip today
        if folder == today:
            continue
        
        # Check if data.json exists
        data_path = os.path.join(folder_path, 'data.json')
        if os.path.exists(data_path):
            past_dates.append(folder)
    
    return sorted(past_dates)


def load_all_past_news(past_dates):
    """Loads all news from past dates' data.json files."""
    all_news = []
    
    for date_str in past_dates:
        data_path = os.path.join(BASE_OUTPUT_DIR, date_str, 'data.json')
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                news_list = json.load(f)
                if isinstance(news_list, list):
                    for item in news_list:
                        item['_source_date'] = date_str  # Track origin date
                        all_news.append(item)
        except Exception as e:
            print(f"  Warning: Could not load {data_path}: {e}")
    
    return all_news


def get_section(item):
    """Gets section(s) from a news item, handling both string and list formats."""
    section = item.get('section', '')
    if isinstance(section, list):
        return section
    return [section]


def filter_news_by_section(all_news, section):
    """Filters news items by section."""
    filtered = []
    for item in all_news:
        item_sections = get_section(item)
        if section in item_sections:
            filtered.append(item)
    return filtered


def select_random_items(items, count):
    """Randomly selects up to 'count' items from the list."""
    if len(items) <= count:
        return items
    return random.sample(items, count)


def clean_item_for_output(item):
    """Removes internal fields from item before output."""
    output = dict(item)
    # Remove internal tracking fields
    output.pop('_source_date', None)
    return output


def main():
    print("Ajánlott Generator running...")
    
    # Get past dates
    past_dates = get_past_dates()
    print(f"Found {len(past_dates)} past dates with data.json")
    
    if not past_dates:
        print("No past dates found. Exiting.")
        return
    
    # Load all past news
    all_news = load_all_past_news(past_dates)
    print(f"Loaded {len(all_news)} total news items from past dates")
    
    if not all_news:
        print("No news items found. Exiting.")
        return
    
    # Create Ajanlott directory
    os.makedirs(AJANLOTT_DIR, exist_ok=True)
    
    # Process each section
    for section in SECTIONS:
        print(f"\n  Processing section: {section}")
        
        # Filter news by section
        section_news = filter_news_by_section(all_news, section)
        print(f"    Found {len(section_news)} items")
        
        if not section_news:
            print(f"    No items for {section}, skipping...")
            continue
        
        # Select random items
        selected = select_random_items(section_news, ITEMS_PER_CATEGORY)
        print(f"    Selected {len(selected)} items")
        
        # Clean items for output
        output_items = [clean_item_for_output(item) for item in selected]
        
        # Write to file
        output_path = os.path.join(AJANLOTT_DIR, f'{section}.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_items, f, ensure_ascii=False, indent=2)
        
        print(f"    Saved to {output_path}")
    
    print("\nAjánlott generation complete!")


if __name__ == "__main__":
    main()
