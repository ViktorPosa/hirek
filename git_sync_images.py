import os
import shutil
import datetime
import subprocess
import json
import re

# Config
IMAGE_REPO_DIR = os.path.join(os.path.dirname(__file__), 'ImageRepo')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'Output')
DAYS_TO_KEEP = 7

def scrub_json_images(date_str):
    """Removes image links from data.json and data_toplist.json for a given pruned date."""
    daily_out = os.path.join(OUTPUT_DIR, date_str)
    if not os.path.isdir(daily_out):
        return

    json_files = ['data.json', 'data_toplist.json']
    for jf in json_files:
        filepath = os.path.join(daily_out, jf)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                changed = False
                for item in data:
                    if item.get('image'):
                        item['image'] = ""
                        changed = True
                    if item.get('image_links'):
                        item['image_links'] = []
                        changed = True
                    
                    # Remove markdown images: ![alt](url)
                    content = item.get('content', '')
                    if '![' in content:
                        new_content = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', content)
                        if new_content != content:
                            item['content'] = new_content
                            changed = True

                if changed:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"    🧹 Scrubbed dead image links from {jf}")
            except Exception as e:
                print(f"    ⚠️ Failed to scrub {jf}: {e}")

def _collect_active_image_paths():
    """Scan recent data.json files to find all image URLs pointing to ImageRepo folders."""
    active = {}  # Maps (date_folder, filename) -> list of (data_json_path, item_index)
    today = datetime.date.today()
    for i in range(DAYS_TO_KEEP + 1):
        date_str = (today - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        daily_out = os.path.join(OUTPUT_DIR, date_str)
        for jf in ['data.json', 'data_toplist.json', 'data_i4.json', 'data_i5.json']:
            filepath = os.path.join(daily_out, jf)
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for idx, item in enumerate(data):
                        img_url = item.get('image', '')
                        if not img_url or 'raw.githubusercontent.com' not in img_url:
                            continue
                        # Extract date folder and filename from URL
                        # Pattern: .../main/<date_folder>/news/<filename>
                        parts = img_url.split('/main/')
                        if len(parts) == 2:
                            rest = parts[1]  # e.g. "2026-03-25/news/some-image.jpg"
                            segments = rest.split('/')
                            if len(segments) >= 3 and segments[1] == 'news':
                                folder = segments[0]
                                filename = segments[2]
                                key = (folder, filename)
                                if key not in active:
                                    active[key] = []
                                active[key].append((filepath, idx))
                except Exception:
                    pass
    return active

def prune_old_images():
    """Deletes daily folders older than DAYS_TO_KEEP in the ImageRepo.
    
    Before deleting, checks if any images in the folder are still referenced
    by current data.json files. If so, copies them to today's folder and
    updates the JSON references.
    """
    if not os.path.exists(IMAGE_REPO_DIR):
        print("    ⚠️ ImageRepo directory not found. Pruning skipped.")
        return

    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    deleted_count = 0

    # Collect all active image references from recent data files
    active_refs = _collect_active_image_paths()
    rescued_count = 0

    print(f"    Checking for ImageRepo folders older than {DAYS_TO_KEEP} days...")
    for item in os.listdir(IMAGE_REPO_DIR):
        item_path = os.path.join(IMAGE_REPO_DIR, item)
        
        # We only care about YYYY-MM-DD folders
        if os.path.isdir(item_path):
            try:
                folder_date = datetime.datetime.strptime(item, "%Y-%m-%d").date()
                age_days = (today - folder_date).days

                if age_days > DAYS_TO_KEEP:
                    # Before deleting, rescue any images still referenced by current data
                    for (ref_folder, ref_filename), ref_locations in active_refs.items():
                        if ref_folder != item:
                            continue
                        old_img = os.path.join(item_path, 'news', ref_filename)
                        if os.path.exists(old_img):
                            # Copy to today's folder
                            today_news_dir = os.path.join(IMAGE_REPO_DIR, today_str, 'news')
                            os.makedirs(today_news_dir, exist_ok=True)
                            dest_img = os.path.join(today_news_dir, ref_filename)
                            if not os.path.exists(dest_img):
                                shutil.copy2(old_img, dest_img)
                                rescued_count += 1
                                print(f"    🔄 Rescued image {ref_filename} from {item} → {today_str}")
                            
                            # Update JSON references to point to today's folder
                            for (json_path, item_idx) in ref_locations:
                                try:
                                    with open(json_path, 'r', encoding='utf-8') as f:
                                        data = json.load(f)
                                    old_url = data[item_idx].get('image', '')
                                    if old_url and item in old_url:
                                        new_url = old_url.replace(f'/{item}/', f'/{today_str}/')
                                        data[item_idx]['image'] = new_url
                                        with open(json_path, 'w', encoding='utf-8') as f:
                                            json.dump(data, f, ensure_ascii=False, indent=2)
                                except Exception:
                                    pass

                    print(f"    🗑️  Deleting old image folder: {item} (Age: {age_days} days)")
                    shutil.rmtree(item_path)
                    scrub_json_images(item)  # Clean up the JSON files too
                    deleted_count += 1
            except ValueError:
                # Not a YYYY-MM-DD folder, e.g. .git, ignore it
                pass

    if rescued_count > 0:
        print(f"    🔄 Rescued {rescued_count} still-referenced images before pruning.")
    if deleted_count > 0:
        print(f"    ✅ Cleaned up {deleted_count} old folders from ImageRepo.")
    else:
        print("    ✅ No old folders needed pruning.")

def sync_git():
    """Commits and pushes changes in the ImageRepo in batches to avoid timeout."""
    if not os.path.exists(IMAGE_REPO_DIR):
        print("    ⚠️ ImageRepo directory not found. Git sync skipped.")
        return

    print("    🔄 Syncing images to remote GitHub repository...")
    try:
        # First handle any deleted/modified tracked files
        subprocess.run(["git", "add", "-u"], cwd=IMAGE_REPO_DIR, check=True, capture_output=True)
        
        # Check for untracked (new) files
        status = subprocess.run(["git", "status", "--porcelain"], cwd=IMAGE_REPO_DIR, check=True, capture_output=True, text=True)
        lines = [l for l in status.stdout.strip().split('\n') if l.strip()] if status.stdout.strip() else []
        
        if not lines:
            print("    ✅ No changes to sync. ImageRepo is up to date.")
            return

        # Separate untracked files (new images) from modified/deleted
        untracked = [l[3:] for l in lines if l.startswith('??')]
        staged = [l for l in lines if not l.startswith('??')]
        
        # Commit any staged changes (deletions, modifications) first
        if staged:
            subprocess.run(["git", "add", "-u"], cwd=IMAGE_REPO_DIR, check=True, capture_output=True)
            commit_msg = f"Auto-update: images for {datetime.date.today()}"
            try:
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=IMAGE_REPO_DIR, check=True, capture_output=True)
            except subprocess.CalledProcessError:
                pass  # Nothing to commit

        # Batch add and push untracked files in chunks of 200
        BATCH_SIZE = 200
        batch_num = 1
        total_batches = (len(untracked) + BATCH_SIZE - 1) // BATCH_SIZE if untracked else 0
        
        for i in range(0, len(untracked), BATCH_SIZE):
            batch = untracked[i:i + BATCH_SIZE]
            print(f"    📦 Batch {batch_num}/{total_batches}: Adding {len(batch)} files...")
            
            # git add the batch
            subprocess.run(["git", "add"] + batch, cwd=IMAGE_REPO_DIR, check=True, capture_output=True)
            
            # Commit
            commit_msg = f"Auto-update: images for {datetime.date.today()} (batch {batch_num})"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=IMAGE_REPO_DIR, check=True, capture_output=True)
            
            # Push with retry
            push_ok = False
            for attempt in range(3):
                try:
                    subprocess.run(["git", "push"], cwd=IMAGE_REPO_DIR, check=True, capture_output=True, timeout=120)
                    push_ok = True
                    print(f"    ✅ Batch {batch_num} pushed successfully.")
                    break
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as push_err:
                    print(f"    ⚠️ Batch {batch_num} push attempt {attempt+1} failed: {push_err}")
                    if attempt < 2:
                        import time as _time
                        _time.sleep(5)
            
            if not push_ok:
                print(f"    ❌ Batch {batch_num} failed after 3 attempts. Remaining batches skipped.")
                return
            
            batch_num += 1

        # If no untracked files, just push the staged changes
        if not untracked and staged:
            try:
                subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=IMAGE_REPO_DIR, check=False, capture_output=True)
                subprocess.run(["git", "push"], cwd=IMAGE_REPO_DIR, check=True, capture_output=True, timeout=120)
                print("    ✅ Pushed staged changes.")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                print(f"    ❌ Push failed: {e}")
                return
        
        print("    ✅ ImageRepo sync completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"    ❌ Git command failed: {e}")
        if e.stderr:
            print(f"       {e.stderr.decode('utf-8').strip()}")
        if e.stdout:
            print(f"       {e.stdout.decode('utf-8').strip()}")
    except Exception as e:
        print(f"    ❌ Unexpected error during Git sync: {e}")

def main():
    print("=== Image Repository Sync & Prune ===")
    prune_old_images()
    sync_git()
    print("=== Done ===")

if __name__ == "__main__":
    main()
