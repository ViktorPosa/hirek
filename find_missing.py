import os
import re

OUTPUT_DIR = os.path.join('Output')
TARTALOM_DIR = os.path.join('Output', 'Tartalom')
ORIGINAL_FILE = os.path.join('Output', 'output.txt')

def get_original_links():
    links = set()
    try:
        with open(ORIGINAL_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Format: [Category][Link]
                if '][' in line:
                    try:
                        link = line.split('][')[1].replace(']', '')
                        links.add(link)
                    except:
                        pass
    except Exception as e:
        print(f"Error reading output.txt: {e}")
    return links

def get_processed_links():
    links = set()
    files = [f for f in os.listdir(TARTALOM_DIR) if f.endswith('.txt')]
    for filename in files:
        path = os.path.join(TARTALOM_DIR, filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                # Find all [Forráslink]: url
                # Regex needs to be robust
                matches = re.findall(r'\[Forráslink\]:\s*(.*?)\s*(\n|$)', content)
                for match in matches:
                    link = match[0].strip()
                    links.add(link)
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return links

def main():
    original = get_original_links()
    processed = get_processed_links()

    print(f"Original links count: {len(original)}")
    print(f"Processed links count: {len(processed)}")

    missing = original - processed
    print(f"Missing links count: {len(missing)}")
    
    extra = processed - original
    if extra:
         print(f"Extra links (duplicates or phantom): {len(extra)}")

    if missing:
        with open(os.path.join(OUTPUT_DIR, 'missing_links.txt'), 'w', encoding='utf-8') as f:
            for link in missing:
                f.write(link + '\n')
        print(f"Saved missing links to {os.path.join(OUTPUT_DIR, 'missing_links.txt')}")

if __name__ == "__main__":
    main()
