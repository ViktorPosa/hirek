import os
import re

TARGET_DIR = os.path.join('Output', '2026-01-02', 'Tartalom')

def main():
    if not os.path.exists(TARGET_DIR):
        print(f"Directory {TARGET_DIR} not found.")
        return

    # 1. Rename Tech.txt -> tech.txt
    tech_path = os.path.join(TARGET_DIR, 'Tech.txt')
    tech_new_path = os.path.join(TARGET_DIR, 'tech.txt')
    if os.path.exists(tech_path):
        os.rename(tech_path, tech_new_path)
        print("Renamed Tech.txt -> tech.txt")
    
    # 2. Fix formatting in all .txt files
    files = [f for f in os.listdir(TARGET_DIR) if f.endswith('.txt')]
    for filename in files:
        path = os.path.join(TARGET_DIR, filename)
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Replace [Header]: with [Header]
            # Headers: Hírszekció, Cím, Tagek, Tartalom, Forráslink, Hír szerzője
            # Also {{kép linkje}}: -> {{kép linkje}}
            
            # Use regex for robust replacement ignoring extra spaces
            # \1 captures the header name
            new_content = re.sub(r'^\[(Hírszekció|Cím|Tagek|Tartalom|Forráslink|Hír szerzője)\]:\s*', r'[\1] ', content, flags=re.MULTILINE)
            new_content = re.sub(r'^\{\{kép linkje\}\}:\s*', r'{{kép linkje}} ', new_content, flags=re.MULTILINE)
            
            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Fixed formatting in {filename}")
            else:
                print(f"No changes needed in {filename}")
                
        except Exception as e:
            print(f"Error processing {filename}: {e}")

if __name__ == "__main__":
    main()
