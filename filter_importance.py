import json
import os
import datetime
import argparse

def str_to_bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def filter_importance():
    parser = argparse.ArgumentParser(description="Split data.json based on importance")
    parser.add_argument("--date", type=str, help="Date to process (YYYY-MM-DD)", default=None)
    parser.add_argument("--dry-run", type=str_to_bool, default=False, help="Don't write files, just print summary")
    args = parser.parse_args()

    # Determine date
    if args.date:
        today_str = args.date
        folder = os.path.join(os.getcwd(), 'Output', today_str)
    else:
        # Try to find today's folder based on environment or date
        if 'DAILY_OUTPUT_DIR' in os.environ:
             # Extract date from path if possible, or blindly trust env
             # Env is usually absolute path like .../Output/2026-02-05
             folder = os.environ['DAILY_OUTPUT_DIR']
        else:
             today = datetime.date.today()
             today_str = today.strftime('%Y-%m-%d')
             folder = os.path.join(os.getcwd(), 'Output', today_str)

    data_path = os.path.join(folder, 'data.json')
    print(f"📂 Processing importance splitting in: {folder}")
    
    if not os.path.exists(data_path):
        print(f"❌ File not found: {data_path}")
        return

    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            items = json.load(f)
    except Exception as e:
        print(f"❌ Error loading data.json: {e}")
        return

    print(f"  Total items: {len(items)}")

    i4_list = []
    i5_list = []
    remaining_list = []

    for item in items:
        imp = item.get('importance', 3)
        # Ensure int
        try:
            imp = int(imp)
        except:
            imp = 3
            
        if imp == 5:
            i5_list.append(item)
        elif imp == 4:
            i4_list.append(item)
        else:
            remaining_list.append(item)

    print(f"  Importance 5 (Highest): {len(i5_list)}")
    print(f"  Importance 4 (High):    {len(i4_list)}")
    print(f"  Importance 1-3 (Rest):  {len(remaining_list)}")

    if args.dry_run:
        print("  [Dry Run] Skipping write.")
        # print("  Sample I5:", [i['title'] for i in i5_list[:3]])
        return

    # SAVE FILES
    # Helper to merge with existing data
    def merge_and_save(filename, new_items):
        path = os.path.join(folder, filename)
        existing = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception as e:
                print(f"  ⚠️ Warning loading existing {filename}: {e}")
        
        # Deduplicate based on sourceLink
        seen_links = set()
        combined = []
        
        # Add existing items first
        for item in existing:
            link = item.get('sourceLink') or item.get('url') or item.get('link')
            if link:
                seen_links.add(link)
            combined.append(item)
            
        # Add new items if not seen
        added_count = 0
        for item in new_items:
            link = item.get('sourceLink') or item.get('url') or item.get('link')
            if link and link in seen_links:
                continue
            
            combined.append(item)
            if link:
                seen_links.add(link)
            added_count += 1
            
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(combined, f, ensure_ascii=False, indent=2)
            print(f"  ✅ Saved {filename} (Total: {len(combined)}, New: {added_count})")
        except Exception as e:
            print(f"  ❌ Error saving {filename}: {e}")

    # 1. data_i5.json
    merge_and_save('data_i5.json', i5_list)

    # 2. data_i4.json
    merge_and_save('data_i4.json', i4_list)

    # 3. data.json (Original replaced with remaining)
    # WARNING: This destroys the original complete list in this file. 
    # But user requested "az eredetiből törli" (delete from original).
    try:
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(remaining_list, f, ensure_ascii=False, indent=2)
        print(f"  ✅ Updated data.json ({len(remaining_list)} items)")
    except Exception as e:
        print(f"  ❌ Error updating data.json: {e}")

if __name__ == "__main__":
    filter_importance()
