
import os

import datetime

# Output directory config
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

INPUT_FILE = os.path.join(DAILY_OUTPUT_DIR, 'output.txt')
OUTPUT_DIR = DAILY_OUTPUT_DIR # Write split files to same daily dir

# Category to Filename mapping
CATEGORY_MAPPING = {
    # Belföld, Nemzetközi hír, Időjárás -> belfold_kulfold.txt
    "Belföld": os.path.join(OUTPUT_DIR, "belfold_kulfold.txt"),
    "Nemzetközi hír": os.path.join(OUTPUT_DIR, "belfold_kulfold.txt"),
    "Időjárás": os.path.join(OUTPUT_DIR, "belfold_kulfold.txt"),

    "Gazdaság": os.path.join(OUTPUT_DIR, "uzlet.txt"),
    "Crypto": os.path.join(OUTPUT_DIR, "uzlet.txt"),

    # Tudomány -> tudomany.txt
    "Tudomány": os.path.join(OUTPUT_DIR, "tudomany.txt"),
    "Zöld hírek": os.path.join(OUTPUT_DIR, "tudomany.txt"),

    # Technika -> tech.txt (now tech.txt)
    "Technika": os.path.join(OUTPUT_DIR, "tech.txt"), # Corrected from Tech.txt
    "Gaming": os.path.join(OUTPUT_DIR, "tech.txt"),
    "Podcast/Videó": os.path.join(OUTPUT_DIR, "tech.txt"),

    # Életmód -> eletmod.txt
    "Életmód": os.path.join(OUTPUT_DIR, "eletmod.txt"),
    "Egészség": os.path.join(OUTPUT_DIR, "eletmod.txt"),
    "Gasztronómia": os.path.join(OUTPUT_DIR, "eletmod.txt"),
    "Utazás": os.path.join(OUTPUT_DIR, "eletmod.txt"),
    "Autó-Motor": os.path.join(OUTPUT_DIR, "eletmod.txt"),

    # Sport -> sport.txt
    "Sport": os.path.join(OUTPUT_DIR, "sport.txt"),

    # Kultúra -> szorakozas.txt
    "Kultúra": os.path.join(OUTPUT_DIR, "szorakozas.txt"),
    "Film/Sorozat": os.path.join(OUTPUT_DIR, "szorakozas.txt"),
    "Vicces/Abszurd": os.path.join(OUTPUT_DIR, "szorakozas.txt"),
    "Bulvár": os.path.join(OUTPUT_DIR, "bulvar.txt"),
    
    # Egyéb -> egyeb.txt (added logic in main to handle unmapped)
    "Egyéb": os.path.join(OUTPUT_DIR, "szorakozas.txt"), # Default fallback if needed
}

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    # Store lines for each file to write them in one go (or append)
    files_content = {}
    
    print(f"Reading {INPUT_FILE}...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Parse category from format [Category][Link]
        # We assume the format starts with [Category]
        if line.startswith('[') and '][' in line:
            try:
                category_end = line.index('][')
                category = line[1:category_end]
                
                target_file = CATEGORY_MAPPING.get(category)
                
                if target_file:
                    if target_file not in files_content:
                        files_content[target_file] = []
                    files_content[target_file].append(line)
                    count += 1
                else:
                    print(f"Warning: Unknown category '{category}' in line: {line}")
            except ValueError:
                print(f"Warning: Could not parse line: {line}")
        else:
             print(f"Warning: Invalid format line: {line}")

    print(f"Sorted {count} links.")

    # Write to files
    for filename, content in files_content.items():
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(content) + '\n')
            print(f"Created {filename} with {len(content)} links.")
        except Exception as e:
            print(f"Error writing to {filename}: {e}")

if __name__ == "__main__":
    main()
