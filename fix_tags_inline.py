import os
import re

TARGET_DIR = os.path.join('Output', '2026-01-02', 'Tartalom')

def add_hashtags(match):
    # match.group(0) is the whole line
    # match.group(1) is the content after "[Tagek] "
    content = match.group(1)
    if not content:
        return match.group(0)
    
    tags = [t.strip() for t in content.split(',')]
    new_tags = []
    for tag in tags:
        if not tag.startswith('#'):
            new_tags.append(f"#{tag}")
        else:
            new_tags.append(tag)
            
    return f"[Tagek] {', '.join(new_tags)}"

def main():
    if not os.path.exists(TARGET_DIR):
        print(f"Directory {TARGET_DIR} not found.")
        return

    files = [f for f in os.listdir(TARGET_DIR) if f.endswith('.txt') and '_cimke.txt' not in f]
    for filename in files:
        path = os.path.join(TARGET_DIR, filename)
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Regex to find [Tagek] lines
            # Assumes [Tagek] (without colon due to previous fix)
            new_content = re.sub(r'^\[Tagek\]\s*(.*)', add_hashtags, content, flags=re.MULTILINE)
            
            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Added hashtags in {filename}")
            else:
                print(f"No changes in {filename}")
                
        except Exception as e:
            print(f"Error processing {filename}: {e}")

if __name__ == "__main__":
    main()
