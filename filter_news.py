import os
import re
from history_manager import HistoryManager

def filter_news(root_dir, history):
    negative_keywords = [
        "Trump", "politics", "death", "murder", "war", "Orban Viktor", 
        "Magyar Peter", "Meszaros Lőrinc", "crysis", "catastrophe", 
        "tragedy", "rejtvény", "háború", "betegség", "vírus", "politika",
         "ukrán", "ukrajna", "Orbán Viktor", "Mészáros Lőrinc", "Magyar Péter",
          "halál", "meghalt", "halott", "eltűnt", "katasztrófa", "tragédia", "börtön",
           "gyilkosság", "Putyin", "Putin", "Oroszország", "Pintér Sándor", "tűz ütött ki", 
    ]
    
    # Iterate through all subdirectories in the root output directory
    for root, dirs, files in os.walk(root_dir):
        if 'Tartalom' in root:
            for file in files:
                if file.endswith(".txt") and not file.endswith("_cimke.txt"):
                    file_path = os.path.join(root, file)
                    process_file(file_path, negative_keywords, history)

def extract_url_from_block(block):
    """Extract URL from a news block using [Forráslink] field."""
    match = re.search(r'\[Forráslink\][:\s]*(https?://\S+)', block, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Try alternate format
    match = re.search(r'\*\*\[Forráslink\]:\*\*\s*(https?://\S+)', block, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

def process_file(file_path, keywords, history):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return

    # Split content into news blocks
    blocks = []
    current_block = []
    
    lines = content.split('\n')
    for line in lines:
        # Check for block start patterns
        if (line.strip().startswith("[Hírszekció]") or 
            line.strip().startswith("**[Szekció]")) and current_block:
            blocks.append("\n".join(current_block))
            current_block = []
        current_block.append(line)
    
    if current_block:
        blocks.append("\n".join(current_block))

    filtered_blocks = []
    removed_count = 0
    
    for block in blocks:
        # Check if any keyword matches
        block_lower = block.lower()
        found_negative = False
        matched_keyword = None
        
        for keyword in keywords:
            if keyword.lower() in block_lower:
                found_negative = True
                matched_keyword = keyword
                print(f"Removing news in {os.path.basename(file_path)} due to keyword: {keyword}")
                break
        
        if not found_negative:
            filtered_blocks.append(block)
        else:
            removed_count += 1
            # Log to history.json with reason
            url = extract_url_from_block(block)
            if url:
                history.mark_filtered(url, "news_filter", f"Content contains keyword: {matched_keyword}")

    if removed_count > 0:
        new_content = "\n".join(filtered_blocks)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated {file_path}: Removed {removed_count} items.")
        except Exception as e:
            print(f"Error writing {file_path}: {e}")

if __name__ == "__main__":
    # Assuming the script is in d:\Python\Nemrossz3 and Output is a subdirectory
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Output")
    print(f"Scanning directory: {output_dir}")
    history = HistoryManager()
    filter_news(output_dir, history)
