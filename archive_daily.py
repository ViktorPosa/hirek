import os
import shutil
import datetime

OUTPUT_DIR = 'Output'
TARTALOM_DIR = os.path.join(OUTPUT_DIR, 'Tartalom')

def main():
    # 1. Get today's date
    today = datetime.date.today().strftime('%Y-%m-%d')
    target_daily_dir = os.path.join(OUTPUT_DIR, today)
    target_tartalom_dir = os.path.join(target_daily_dir, 'Tartalom')

    # 2. Create directories
    if not os.path.exists(target_daily_dir):
        os.makedirs(target_daily_dir)
        print(f"Created directory: {target_daily_dir}")
    
    if not os.path.exists(target_tartalom_dir):
        os.makedirs(target_tartalom_dir)
        print(f"Created directory: {target_tartalom_dir}")

    # 3. Move files from Output/ root
    # We only move .txt files and the specific files we generated, avoiding moving directories recursively into themselves
    files_to_move = [f for f in os.listdir(OUTPUT_DIR) if os.path.isfile(os.path.join(OUTPUT_DIR, f))]
    
    for filename in files_to_move:
        # Skip the script itself if it were in Output (it's in root, but good practice)
        if filename.endswith('.py'):
            continue
            
        src = os.path.join(OUTPUT_DIR, filename)
        dst = os.path.join(target_daily_dir, filename)
        
        try:
            shutil.move(src, dst)
            print(f"Moved {filename} -> {target_daily_dir}")
        except Exception as e:
            print(f"Error moving {filename}: {e}")

    # 4. Move files from Output/Tartalom/
    if os.path.exists(TARTALOM_DIR):
        tartalom_files = [f for f in os.listdir(TARTALOM_DIR) if os.path.isfile(os.path.join(TARTALOM_DIR, f))]
        
        for filename in tartalom_files:
            src = os.path.join(TARTALOM_DIR, filename)
            dst = os.path.join(target_tartalom_dir, filename)
            
            try:
                shutil.move(src, dst)
                print(f"Moved Tartalom/{filename} -> {target_tartalom_dir}")
            except Exception as e:
                print(f"Error moving {filename}: {e}")

    print("\nArchiving complete.")

if __name__ == "__main__":
    main()
