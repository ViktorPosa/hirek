import os
import re

import datetime

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

TARGET_DIR = os.path.join(DAILY_OUTPUT_DIR, 'Tartalom')


def get_first_tag(content):
    # Regex to find [Tagek] value
    # Matches [Tagek] tag1, tag2, tag3...
    match = re.search(r'\[Tagek\]\s*(.*?)(\n|$)', content, re.IGNORECASE)
    if match:
        tags_line = match.group(1).strip()
        if tags_line:
            # Split by comma and take the first one
            first_tag = tags_line.split(',')[0].strip()
            return first_tag
    return None

def main():
    if not os.path.exists(TARGET_DIR):
        print(f"Directory {TARGET_DIR} not found.")
        return

    # First, delete any existing _cimke.txt files
    existing_tag_files = [f for f in os.listdir(TARGET_DIR) if f.endswith('_cimke.txt')]
    if existing_tag_files:
        print(f"Deleting {len(existing_tag_files)} existing tag files...")
        for tag_file in existing_tag_files:
            tag_path = os.path.join(TARGET_DIR, tag_file)
            try:
                os.remove(tag_path)
                print(f"  Deleted: {tag_file}")
            except Exception as e:
                print(f"  Error deleting {tag_file}: {e}")

    # Files to process (exclude _cimke.txt files)
    files = [f for f in os.listdir(TARGET_DIR) if f.endswith('.txt') and '_cimke.txt' not in f]

    
    for filename in files:
        print(f"Processing {filename}...")
        path = os.path.join(TARGET_DIR, filename)
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Split into articles
            # We assume articles are separated by double newlines or similar, 
            # but robust finding of [Tagek] is safer.
            
            # Regex to iterate all [Tagek] occurrences
            # We limit to first 30 matches
            matches = re.findall(r'\[Tagek\]\s*(.*?)(\n|$)', content, re.IGNORECASE)
            
            tags_list = []
            for i, match in enumerate(matches):
                if i >= 30:
                    break
                
                tags_line = match[0].strip()
                if tags_line:
                    first_tag = tags_line.split(',')[0].strip()
                    if first_tag:
                        tags_list.append(first_tag)
            
            if tags_list:
                # Create filename_cimke.txt
                base_name = os.path.splitext(filename)[0]
                output_filename = f"{base_name}_cimke.txt"
                output_path = os.path.join(TARGET_DIR, output_filename)
                
                # Write tags comma separated
                # Tags may already have # prefix, so check before adding
                # Also deduplicate tags (case-insensitive)
                formatted_tags = []
                seen_tags = set()
                for t in tags_list:
                    t = t.strip()
                    tag_lower = t.lower()
                    if tag_lower in seen_tags:
                        continue
                    seen_tags.add(tag_lower)
                    
                    if t.startswith('#'):
                        formatted_tags.append(t)
                    else:
                        formatted_tags.append(f"#{t}")
                
                content_to_write = ", ".join(formatted_tags)



                
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(content_to_write)
                
                print(f"  Generated {output_filename} with {len(tags_list)} tags.")
            else:
                print(f"  No tags found in {filename}.")
                
        except Exception as e:
            print(f"Error processing {filename}: {e}")

if __name__ == "__main__":
    main()
