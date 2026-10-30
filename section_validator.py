"""
LEÍRÁS:
Section Validator & Fixer: Adatvalidálás a pipeline végén.
Ellenőrzi a data.json fájlban lévő hírek 'section' mezőjét.
Ha egy section nem felel meg az engedélyezett értékeknek (VALID_SECTIONS),
automatikusan kijavítja "szorakozas"-ra vagy az első érvényes értékre.

BEMENET:
- data.json (napi kimenet)

KIMENET:
- Javított data.json
"""


import json
import os
import datetime

# Engedélyezett section értékek
VALID_SECTIONS = {
    "tech",           # Technológia, IT, digitális világ
    "tudomany",       # Tudomány, kutatás, felfedezések
    "belfold_kulfold", # Belföldi és külföldi általános hírek
    "sport",          # Sport hírek
    "uzlet",          # Üzlet, gazdaság, pénzügyek
    "szorakozas",     # Szórakozás, filmek, zenék, játékok
    "gamer",          # Gaming, videojátékok
    "kripto",         # Kriptovaluták, blockchain
    "eletmod",        # Életmód, egészség, wellness
    "bulvar",         # Bulvár, celebek, könnyű témák
    "fooldal",        # Főoldal (speciális, csak listában szerepelhet)
}

DEFAULT_SECTION = "szorakozas"

# Source-based overrides: URL pattern -> {field: value}
# Articles matching these URL patterns get forced importance/section
SOURCE_OVERRIDES = [
    {"url_contains": "telex.hu/eszkombajn",       "section": "szorakozas", "importance": 5},
    {"url_contains": "index.hu/tudomany/til",      "section": "szorakozas", "importance": 5},
]

def fix_section(section):
    """
    Validates and fixes a section value.
    Returns the fixed section or DEFAULT_SECTION if invalid.
    """
    if isinstance(section, list):
        # Handle list of sections (e.g., ["belfold_kulfold", "fooldal"])
        fixed_list = []
        for s in section:
            if s in VALID_SECTIONS:
                fixed_list.append(s)
            else:
                print(f"    [!] Invalid section '{s}' in list -> keeping valid ones")
        
        if not fixed_list:
            return DEFAULT_SECTION
        elif len(fixed_list) == 1:
            return fixed_list[0]
        else:
            return fixed_list
    
    elif isinstance(section, str):
        if section in VALID_SECTIONS:
            return section
        else:
            print(f"    [!] Invalid section '{section}' -> '{DEFAULT_SECTION}'")
            return DEFAULT_SECTION
    
    else:
        print(f"    [!] Section is not string/list: {type(section)} -> '{DEFAULT_SECTION}'")
        return DEFAULT_SECTION


def validate_and_fix_data(data_path):
    """
    Reads data.json, validates all section fields, and fixes invalid ones.
    """
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found")
        return False
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_items = json.load(f)
    except Exception as e:
        print(f"Error reading {data_path}: {e}")
        return False
    
    fixed_count = 0
    total_count = len(news_items)
    
    print(f"Validating {total_count} news items...")
    
    for i, item in enumerate(news_items):
        original_section = item.get('section', None)
        
        if original_section is None:
            print(f"  [{i+1}] Missing section -> '{DEFAULT_SECTION}'")
            item['section'] = DEFAULT_SECTION
            fixed_count += 1
        else:
            fixed_section = fix_section(original_section)
            if fixed_section != original_section:
                item['section'] = fixed_section
                fixed_count += 1
        
        # Apply source-based overrides (importance, section) by URL pattern
        source_link = item.get('sourceLink', '')
        for rule in SOURCE_OVERRIDES:
            if rule["url_contains"] in source_link:
                changed = False
                if "section" in rule and item.get('section') != rule["section"]:
                    old_sec = item.get('section')
                    item['section'] = rule["section"]
                    changed = True
                if "importance" in rule and item.get('importance') != rule["importance"]:
                    old_imp = item.get('importance')
                    item['importance'] = rule["importance"]
                    changed = True
                if changed:
                    print(f"    [⚡] Source override ({rule['url_contains']}): section={item['section']}, importance={item['importance']}")
                    fixed_count += 1
                break
    
    if fixed_count > 0:
        try:
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(news_items, f, ensure_ascii=False, indent=2)
            print(f"\n[OK] Fixed {fixed_count}/{total_count} items with invalid sections.")
        except Exception as e:
            print(f"Error writing {data_path}: {e}")
            return False
    else:
        print(f"\n[OK] All {total_count} items have valid sections. No changes needed.")
    
    return True


def main():
    # Get the daily output directory
    env_date_dir = os.environ.get('DAILY_OUTPUT_DIR')
    if env_date_dir:
        date_folder = env_date_dir
    else:
        today = datetime.date.today().strftime('%Y-%m-%d')
        date_folder = os.path.join('Output', today)
    
    data_path = os.path.join(date_folder, 'data.json')
    
    print("Section Validator & Fixer")
    print("=" * 40)
    print(f"Processing: {data_path}")
    print(f"Valid sections: {', '.join(sorted(VALID_SECTIONS))}")
    print(f"Default fallback: {DEFAULT_SECTION}")
    print()
    
    success = validate_and_fix_data(data_path)
    
    if not success:
        exit(1)


if __name__ == "__main__":
    main()
