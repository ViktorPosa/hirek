import os
import re

def filter_news(root_dir):
    negative_keywords = [
        "Trump", "politics", "death", "murder", "war", "Orban Viktor", 
        "Magyar Peter", "Meszaros Lőrinc", "crysis", "catastrophe", 
        "tragedy", "rejtvény", "háború", "betegség", "vírus"
    ]
    
    # Iterate through all subdirectories in the root output directory
    for root, dirs, files in os.walk(root_dir):
        if 'Tartalom' in root:
            for file in files:
                if file.endswith(".txt") and not file.endswith("_cimke.txt"):
                    file_path = os.path.join(root, file)
                    process_file(file_path, negative_keywords)

def process_file(file_path, keywords):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return

    # Split content into news blocks
    # Using the structure [Hírszekció] as the start of a block
    # We need to be careful with the split to keep the delimiter
    
    # Pattern to match the start of a news item
    # Since the file format seems to be strictly formatted with [Hírszekció] at the start
    # We can perform a split but we need to retain the delimiter. 
    # Or better, we can iterate through lines and build blocks.
    
    blocks = []
    current_block = []
    
    lines = content.split('\n')
    for line in lines:
        if line.strip().startswith("[Hírszekció]:") and current_block:
            blocks.append("\n".join(current_block))
            current_block = []
        current_block.append(line)
    
    if current_block:
        blocks.append("\n".join(current_block))

    filtered_blocks = []
    removed_count = 0
    
    for block in blocks:
        # Check if any keyword matches
        # Using case-insensitive search
        block_lower = block.lower()
        found_negative = False
        for keyword in keywords:
            if keyword.lower() in block_lower:
                found_negative = True
                print(f"Removing news in {os.path.basename(file_path)} due to keyword: {keyword}")
                # Optional: Print snippet for verification
                # print(block[:100] + "...")
                break
        
        if not found_negative:
            filtered_blocks.append(block)
        else:
            removed_count += 1

    if removed_count > 0:
        new_content = "\n".join(filtered_blocks)
        # Ensure correct spacing between blocks if needed, usually the split kept newlines?
        # The split was by line, and joined by newline.
        # However, the structure usually separates blocks with empty lines or "---"? 
        # Looking at the sample file, there is no explicit separator line like "---" consistently used between blocks 
        # except maybe at line 73 in the sample `belfold_kulfold.txt` there is `---`.
        # Wait, line 73: `---`
        # Let's check how many `---` are there.
        # If I simply join by `\n`, I might lose the separator if it was part of `current_block`.
        # `current_block` collects all lines.
        # If `[Hírszekció]:` starts a new block, the previous block ends.
        # If there are empty lines or `---` BEFORE `[Hírszekció]:`, they belong to the PREVIOUS block.
        # So appending `\n` join is fine.
        
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
    filter_news(output_dir)
