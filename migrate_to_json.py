"""
Régi Hírek Migráló Script (.txt -> .json)

Ez a script átalakítja a régi .txt formátumú híreket az új JSON formátumra.
Végigmegy az összes Output/YYYY-MM-DD/Tartalom mappán és létrehozza a data.json fájlokat.
"""

import os
import json
import re
import datetime

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'

# Section name mapping (from Hungarian to code)
SECTION_MAP = {
    'Tech': 'tech',
    'tech': 'tech',
    'Tudomány': 'tudomany',
    'tudomany': 'tudomany',
    'Belföld/Külföld': 'belfold_kulfold',
    'Belföld': 'belfold_kulfold',
    'Külföld': 'belfold_kulfold',
    'belfold_kulfold': 'belfold_kulfold',
    'Sport': 'sport',
    'sport': 'sport',
    'Üzlet': 'uzlet',
    'uzlet': 'uzlet',
    'Szórakozás': 'szorakozas',
    'szorakozas': 'szorakozas',
    'Életmód': 'eletmod',
    'eletmod': 'eletmod',
    'Bulvár': 'bulvar',
    'bulvar': 'bulvar',
    'Egyéb': 'egyeb',
    'egyeb': 'egyeb',
}


ALLOWED_SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport']

def parse_txt_news(content):
    """Parses a .txt file content and extracts news items."""
    news_items = []
    
    # Split by [Hírszekció] marker (lookahead)
    # This handles cases where separators like --- or * are missing
    blocks = re.split(r'(?=\[Hírszekció\])', content, flags=re.IGNORECASE)
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        # Only process blocks that look like news items
        if '[Cím]' in block or '[Tartalom]' in block:
            item = parse_single_news_block(block)
            if item and item.get('title'):
                news_items.append(item)
    
    return news_items


def parse_single_news_block(block):
    """Parses a single news block into a dict."""
    item = {}
    
    # Extract section
    section_match = re.search(r'\[Hírszekció\][\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if section_match:
        raw_section = section_match.group(1).strip()
        # Map to code
        mapped_section = SECTION_MAP.get(raw_section, SECTION_MAP.get(raw_section.lower(), raw_section.lower()))
        
        # Consolidate multiple variations
        if 'tudomány' in mapped_section: mapped_section = 'tudomany'
        if 'szórakozás' in mapped_section: mapped_section = 'szorakozas'
        if 'életmód' in mapped_section: mapped_section = 'eletmod'
        
        # Check against strict list
        if mapped_section in ALLOWED_SECTIONS:
            item['section'] = mapped_section
        else:
            # Fallback or strict enforcement? 
            # If strictly requested, maybe map 'egyeb' to something or skip?
            # For now, let's look for partial matches or default to 'belfold_kulfold' if unclear,
            # BUT the user said strict categories only.
            # Let's try to map some common ones or leave as is if it matches allowed.
            pass # section will be handled/assigned later if missing
            
            # Additional mapping attempts
            if 'tech' in mapped_section: item['section'] = 'tech'
            elif 'sport' in mapped_section: item['section'] = 'sport'
            elif 'üzlet' in mapped_section or 'gazdaság' in mapped_section: item['section'] = 'uzlet'
            elif 'belföld' in mapped_section or 'külföld' in mapped_section: item['section'] = 'belfold_kulfold'
    
    # Extract title
    title_match = re.search(r'\[Cím\][\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if title_match:
        item['title'] = title_match.group(1).strip()
    
    # Extract tags
    tags_match = re.search(r'\[Tagek\][\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if tags_match:
        tags_str = tags_match.group(1).strip()
        tags = [t.strip().strip('#') for t in tags_str.split(',')]
        item['tags'] = [t for t in tags if t]
    else:
        item['tags'] = []
    
    # Extract content
    content_match = re.search(r'\[Tartalom\][\s:]*(.+?)(?=\[Forráslink\]|\[Hír szerzője\]|\{\{kép|\n\[Hírszekció\]|$)', block, re.IGNORECASE | re.DOTALL)
    if content_match:
        item['content'] = content_match.group(1).strip()
    
    # Extract source link
    source_match = re.search(r'\[Forráslink\][\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if source_match:
        item['sourceLink'] = source_match.group(1).strip()
    
    # Extract author
    author_match = re.search(r'\[Hír szerzője\][\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if author_match:
        item['author'] = author_match.group(1).strip()
    else:
        item['author'] = ""
    
    # Extract image
    image_match = re.search(r'\{\{kép linkje\}\}[\s:]*(.+?)(?:\n|$)', block, re.IGNORECASE)
    if image_match:
        img = image_match.group(1).strip()
        if img.lower() not in ['nincs elérhető kép', 'nincs kép', '', 'n/a']:
            item['image'] = img
        else:
            item['image'] = ""
    else:
        item['image'] = ""
    
    return item


def migrate_date_folder(date_folder):
    """Migrates all .txt files in a date's Tartalom folder to data.json."""
    tartalom_dir = os.path.join(BASE_OUTPUT_DIR, date_folder, 'Tartalom')
    
    if not os.path.exists(tartalom_dir):
        print(f"  Skipping {date_folder}: No Tartalom folder")
        return 0
    
    # Check if data.json already exists
    output_path = os.path.join(BASE_OUTPUT_DIR, date_folder, 'data.json')
    if os.path.exists(output_path):
        print(f"  Skipping {date_folder}: data.json already exists")
        return 0
    
    all_news = []
    
    # Process all .txt files in Tartalom (except _cimke files)
    txt_files = [f for f in os.listdir(tartalom_dir) 
                 if f.endswith('.txt') and '_cimke.txt' not in f 
                 and f not in ['piacok.txt', 'idojaras.txt']]
    
    for txt_file in txt_files:
        txt_path = os.path.join(tartalom_dir, txt_file)
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            news_items = parse_txt_news(content)
            
            # If section is missing, infer from filename
            base_name = os.path.splitext(txt_file)[0].lower()
            for item in news_items:
                if not item.get('section'):
                    item['section'] = SECTION_MAP.get(base_name, base_name)
            
            all_news.extend(news_items)
            print(f"    {txt_file}: {len(news_items)} items")
            
        except Exception as e:
            print(f"    Error processing {txt_file}: {e}")
    
    if not all_news:
        print(f"  No news items found in {date_folder}")
        return 0
    
    # Add fooldal section to random 30 items
    import random
    if len(all_news) > 30:
        fooldal_items = random.sample(all_news, 30)
    else:
        fooldal_items = all_news
    
    for item in fooldal_items:
        current_section = item.get('section', '')
        if isinstance(current_section, str):
            item['section'] = [current_section, 'fooldal']
        elif isinstance(current_section, list) and 'fooldal' not in current_section:
            item['section'].append('fooldal')
    
    # Write data.json
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_news, f, ensure_ascii=False, indent=2)
    
    print(f"  Created data.json with {len(all_news)} items")
    return len(all_news)


def migrate_piacok(date_folder):
    """Migrates piacok.txt to piacok.json for a date folder."""
    tartalom_dir = os.path.join(BASE_OUTPUT_DIR, date_folder, 'Tartalom')
    txt_path = os.path.join(tartalom_dir, 'piacok.txt')
    output_path = os.path.join(BASE_OUTPUT_DIR, date_folder, 'piacok.json')
    
    if not os.path.exists(txt_path):
        return
    
    if os.path.exists(output_path):
        return
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse piacok.txt format
        analyses = {}
        blocks = content.split('\n\n')
        
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            
            # Extract fields
            symbol_match = re.search(r'\[Szimbólum\][\s:]*(.+?)(?:\n|$)', block)
            title_match = re.search(r'\[Cím\][\s:]*(.+?)(?:\n|$)', block)
            summary_match = re.search(r'\[Összefoglaló\][\s:]*(.+?)(?:\n|$)', block)
            details_match = re.search(r'\[Részletek\][\s:]*(.+?)(?:\n|$)', block)
            sentiment_match = re.search(r'\[Hangulat\][\s:]*(.+?)(?:\n|$)', block)
            
            if symbol_match:
                symbol = symbol_match.group(1).strip()
                analyses[symbol] = {
                    'title': title_match.group(1).strip() if title_match else '',
                    'summary': summary_match.group(1).strip() if summary_match else '',
                    'details': details_match.group(1).strip() if details_match else '',
                    'sentiment': sentiment_match.group(1).strip() if sentiment_match else 'Semleges',
                    'date': date_folder
                }
        
        if analyses:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(analyses, f, ensure_ascii=False, indent=2)
            print(f"    Migrated piacok.json with {len(analyses)} symbols")
    
    except Exception as e:
        print(f"    Error migrating piacok: {e}")


def migrate_idojaras(date_folder):
    """Migrates idojaras.txt to idojaras.json for a date folder."""
    tartalom_dir = os.path.join(BASE_OUTPUT_DIR, date_folder, 'Tartalom')
    txt_path = os.path.join(tartalom_dir, 'idojaras.txt')
    output_path = os.path.join(BASE_OUTPUT_DIR, date_folder, 'idojaras.json')
    
    if not os.path.exists(txt_path):
        return
    
    if os.path.exists(output_path):
        return
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse idojaras.txt format
        forecasts = {}
        
        # Pattern: [City]: followed by [Előrejelzés]: text
        pattern = r'\[([^\]]+)\][\s:]*\n\[Előrejelzés\][\s:]*(.+?)(?=\n\n|\n\[|\Z)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for city, forecast in matches:
            city = city.strip()
            forecast = forecast.strip()
            if city and forecast:
                forecasts[city] = forecast
        
        if forecasts:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(forecasts, f, ensure_ascii=False, indent=2)
            print(f"    Migrated idojaras.json with {len(forecasts)} cities")
    
    except Exception as e:
        print(f"    Error migrating idojaras: {e}")


def get_date_folders():
    """Returns list of date folders in Output directory."""
    date_folders = []
    
    if not os.path.exists(BASE_OUTPUT_DIR):
        return []
    
    for folder in os.listdir(BASE_OUTPUT_DIR):
        folder_path = os.path.join(BASE_OUTPUT_DIR, folder)
        if folder == 'Ajanlott' or not os.path.isdir(folder_path):
            continue
        
        # Check if folder name looks like a date
        try:
            datetime.datetime.strptime(folder, '%Y-%m-%d')
            date_folders.append(folder)
        except ValueError:
            continue
    
    return sorted(date_folders)


def main():
    print("=" * 60)
    print("Régi Hírek Migráló Script (.txt -> .json)")
    print("=" * 60)
    
    date_folders = get_date_folders()
    print(f"\nFound {len(date_folders)} date folders to migrate")
    
    if not date_folders:
        print("No date folders found. Exiting.")
        return
    
    total_items = 0
    migrated_folders = 0
    
    for date_folder in date_folders:
        print(f"\nProcessing {date_folder}...")
        
        items = migrate_date_folder(date_folder)
        if items > 0:
            total_items += items
            migrated_folders += 1
        
        # Also migrate piacok and idojaras
        migrate_piacok(date_folder)
        migrate_idojaras(date_folder)
    
    print("\n" + "=" * 60)
    print(f"Migration complete!")
    print(f"  Migrated folders: {migrated_folders}")
    print(f"  Total news items: {total_items}")
    print("=" * 60)


if __name__ == "__main__":
    main()
