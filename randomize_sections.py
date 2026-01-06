"""
Randomize existing data.json within sections.
Run this on an existing data.json to randomize items within each section.
"""
import os
import json
import random
import datetime
import sys

# Configuration
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

def get_primary_section(item):
    """Gets the primary (first) section from a news item."""
    section = item.get('section', '')
    if isinstance(section, list) and len(section) > 0:
        return section[0]
    return section if isinstance(section, str) else ''

def randomize_within_sections(all_news):
    """
    Groups news by their primary section and randomizes within each group.
    Returns a list where sections are grouped together, but items within each section are randomized.
    """
    # Define section order for consistent output
    section_order = ['belfold_kulfold', 'tech', 'tudomany', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport']
    
    # Group news by primary section
    section_groups = {}
    for item in all_news:
        primary_section = get_primary_section(item)
        if primary_section not in section_groups:
            section_groups[primary_section] = []
        section_groups[primary_section].append(item)
    
    print(f"Found {len(section_groups)} sections:")
    for section, items in section_groups.items():
        print(f"  {section}: {len(items)} items")
    
    # Randomize within each section group
    for section in section_groups:
        random.shuffle(section_groups[section])
    
    # Build result in section order
    result = []
    processed_sections = set()
    
    # First add sections in defined order
    for section in section_order:
        if section in section_groups:
            result.extend(section_groups[section])
            processed_sections.add(section)
    
    # Add any remaining sections not in the order list
    for section in section_groups:
        if section not in processed_sections:
            result.extend(section_groups[section])
    
    return result

def validate_news_integrity(original, randomized):
    """Validates that no news items were lost or corrupted."""
    if len(original) != len(randomized):
        print(f"ERROR: Item count mismatch! Original: {len(original)}, Randomized: {len(randomized)}")
        return False
    
    # Check all original items exist in randomized
    original_links = {item.get('sourceLink') for item in original}
    randomized_links = {item.get('sourceLink') for item in randomized}
    
    if original_links != randomized_links:
        missing = original_links - randomized_links
        extra = randomized_links - original_links
        if missing:
            print(f"ERROR: Missing items: {missing}")
        if extra:
            print(f"ERROR: Extra items: {extra}")
        return False
    
    # Check each item has all required fields
    required_fields = ['section', 'title', 'content', 'sourceLink', 'author']
    for item in randomized:
        for field in required_fields:
            if field not in item:
                print(f"ERROR: Missing field '{field}' in item: {item.get('title', 'Unknown')}")
                return False
    
    print("Validation passed: All items intact with correct fields.")
    return True

def main():
    data_path = os.path.join(DAILY_OUTPUT_DIR, 'data.json')
    
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found")
        return
    
    print(f"Loading {data_path}...")
    with open(data_path, 'r', encoding='utf-8') as f:
        original_news = json.load(f)
    
    print(f"Loaded {len(original_news)} news items")
    
    # Make a deep copy to preserve original for validation
    import copy
    original_copy = copy.deepcopy(original_news)
    
    # Randomize
    print("\nRandomizing within sections...")
    randomized_news = randomize_within_sections(original_news)
    
    # Validate
    print("\nValidating integrity...")
    if not validate_news_integrity(original_copy, randomized_news):
        print("Validation failed! Not saving.")
        return
    
    # Backup original
    backup_path = os.path.join(DAILY_OUTPUT_DIR, 'data_backup.json')
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(original_copy, f, ensure_ascii=False, indent=2)
    print(f"\nBackup saved to {backup_path}")
    
    # Save randomized
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(randomized_news, f, ensure_ascii=False, indent=2)
    print(f"Randomized data saved to {data_path}")
    
    # Show sample of result
    print("\nFirst 5 items after randomization:")
    for i, item in enumerate(randomized_news[:5]):
        section = get_primary_section(item)
        print(f"  {i+1}. [{section}] {item.get('title', 'No title')[:50]}... - {item.get('author', 'Unknown')}")

if __name__ == "__main__":
    main()
