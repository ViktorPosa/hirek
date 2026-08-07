"""
LEÍRÁS:
Kép letöltő és feltöltő (ImgBB) modul.
Párhuzamosan letölti a cikkekhez tartozó képeket, majd feltölti őket az ImgBB képtárhelyre.
Frissíti a JSON adatfájlokat a végleges kép URL-ekkel. Kezeli a rátakorlátokat és a biztonsági mentést.

BEMENET:
- Output/YYYY-MM-DD/data.json fájlok
- ImgBB API kulcs (környezeti változó vagy argumentum)

KIMENET:
- Frissített data.json (ImgBB linkekkel)
- Output/YYYY-MM-DD/Images/ mappa (átmeneti tárolás)
"""


import os
import json
import requests
import re
import unicodedata
import datetime
from bs4 import BeautifulSoup
import argparse
import time
import random
import sys
import base64
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pipeline_logger import log_pipeline_error

# Set console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'
MAX_WORKERS = 10  # Reduced from 20 — CDNs rate-limit aggressive parallel fetching

# Git Image Repository Settings
# ⚠️ REPLACE THESE WITH YOUR ACTUAL GITHUB DETAILS ⚠️
IMAGE_GITHUB_USER = "Derushir"     # e.g., "gipszjakab"
IMAGE_GITHUB_REPO = "Kepek"       # e.g., "kepek"
IMAGE_REPO_DIR = os.path.join(os.path.dirname(__file__), 'ImageRepo')

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
]

# Thread-safe print lock
print_lock = threading.Lock()

# Historical image index: sourceLink -> raw.githubusercontent.com URL
# Built once at startup by _build_historical_image_index()
_HISTORICAL_IMAGE_INDEX = None
_HISTORICAL_INDEX_LOCK = threading.Lock()


def _build_historical_image_index():
    """
    Scan historical data files (last 14 days) and build a mapping:
    sourceLink -> github image URL.
    Allows skipping image downloads for articles already processed on a previous day.
    Always sets the global (even on failure) to prevent repeated rebuild attempts.
    """
    global _HISTORICAL_IMAGE_INDEX
    if _HISTORICAL_IMAGE_INDEX is not None:
        return _HISTORICAL_IMAGE_INDEX

    import glob
    import datetime as _dt
    index = {}
    file_count = 0
    cutoff = (_dt.date.today() - _dt.timedelta(days=14)).strftime('%Y-%m-%d')

    try:
        patterns = ['Output/*/data.json', 'Output/*/data_i4.json', 'Output/*/data_i5.json']
        for pattern in patterns:
            for data_file in sorted(glob.glob(pattern)):
                date_part = os.path.basename(os.path.dirname(data_file))
                if date_part < cutoff:
                    continue
                try:
                    with open(data_file, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    file_count += 1
                    for item in items:
                        img = item.get('image', '')
                        src = item.get('sourceLink', '')
                        if src and img and 'raw.githubusercontent.com' in img and re.search(r'/\d{4}-\d{2}-\d{2}/', img):
                            index[src.strip()] = img
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        # Always assign — even empty dict — so get_historical_image() never triggers a rebuild loop
        _HISTORICAL_IMAGE_INDEX = index

    print(f"  [ImageDownloader] Built historical image index: {len(index)} articles from {file_count} files (last 14 days)")
    return index


def get_historical_image(source_link):
    """
    Check if this article's sourceLink already has a github image
    in any historical data file. Returns the github URL or None.
    """
    global _HISTORICAL_IMAGE_INDEX
    if _HISTORICAL_IMAGE_INDEX is None:
        with _HISTORICAL_INDEX_LOCK:
            if _HISTORICAL_IMAGE_INDEX is None:
                _build_historical_image_index()
    if not source_link:
        return None
    return _HISTORICAL_IMAGE_INDEX.get(source_link.strip())

def slugify(value, allow_unicode=False):
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def clean_url(url):
    if not url: return ""
    return url.strip().rstrip('.').rstrip(',').rstrip(';')

def get_headers(referer=None, is_image=False):
    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept-Language': 'hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'no-cache',
    }
    if is_image:
        headers['Accept'] = 'image/avif,image/webp,image/*,*/*;q=0.8'
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    if referer:
        headers['Referer'] = referer
    return headers

def copy_to_git_repo(image_path, date_folder):
    """
    Copies an image to the local ImageRepo clone and returns a raw GitHub URL.
    """
    repo_date_dir = os.path.join(IMAGE_REPO_DIR, date_folder, 'news')
    os.makedirs(repo_date_dir, exist_ok=True)
    
    filename = os.path.basename(image_path)
    dest_path = os.path.join(repo_date_dir, filename)
    
    # Resize to max 800px width before saving statically
    try:
        from PIL import Image
        from io import BytesIO
        with open(image_path, "rb") as file:
            image_data = file.read()
            
        img = Image.open(BytesIO(image_data))
        max_width = 800
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            fmt = img.format or ('PNG' if image_path.lower().endswith('.png') else 'JPEG')
            save_kwargs = {'quality': 85} if fmt == 'JPEG' else {}
            img.save(dest_path, format=fmt, **save_kwargs)
            print(f"    📐 Resized and copied {img.width}→{max_width}px to Git Repo")
        else:
            import shutil
            shutil.copy2(image_path, dest_path)
            print(f"    📥 Copied image directly to Git Repo")
    except Exception as resize_err:
        print(f"    ⚠️ Resize skipped, copying directly: {resize_err}")
        import shutil
        shutil.copy2(image_path, dest_path)

    raw_url = f"https://raw.githubusercontent.com/{IMAGE_GITHUB_USER}/{IMAGE_GITHUB_REPO}/main/{date_folder}/news/{filename}"
    return raw_url

def download_image(url, save_dir, filename_base, referer=None, max_size_mb=5):
    """Download image with size limit."""
    try:
        url = clean_url(url)
        if not url: return None, None
        
        headers = get_headers(referer=referer, is_image=True)
        
        # First, do a HEAD request to check file size
        try:
            head_response = requests.head(url, headers=headers, timeout=(5, 10), allow_redirects=True)
            content_length = int(head_response.headers.get('content-length', 0))
            max_bytes = max_size_mb * 1024 * 1024
            if content_length > max_bytes:
                print(f"    Skipping: File too large ({content_length / 1024 / 1024:.1f}MB > {max_size_mb}MB)")
                return None, None
        except:
            pass  # If HEAD fails, proceed with GET and check during download
        
        print(f"    Downloading: {url[:50]}... (Ref: {referer[:30] if referer else '-'})")
        
        response = requests.get(url, headers=headers, timeout=(5, 20), stream=True)
        response.raise_for_status()
        
        # Check content-length from GET response
        content_length = int(response.headers.get('content-length', 0))
        max_bytes = max_size_mb * 1024 * 1024
        if content_length > max_bytes:
            print(f"    Skipping: File too large ({content_length / 1024 / 1024:.1f}MB > {max_size_mb}MB)")
            return None, None
        
        content_type = response.headers.get('content-type', '').lower()
        if 'image/jpeg' in content_type or 'jpg' in url.lower(): ext = '.jpg'
        elif 'image/png' in content_type or 'png' in url.lower(): ext = '.png'
        elif 'image/webp' in content_type: ext = '.webp'
        elif 'image/gif' in content_type: ext = '.gif'
        elif 'image/svg' in content_type: ext = '.svg'
        else: ext = '.jpg'
            
        filename = f"{filename_base}{ext}"
        filepath = os.path.join(save_dir, filename)
        
        # Download with size limit check during streaming
        downloaded_size = 0
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                downloaded_size += len(chunk)
                if downloaded_size > max_bytes:
                    print(f"    Aborting: Download exceeded {max_size_mb}MB limit")
                    f.close()
                    os.remove(filepath)
                    return None, None
                f.write(chunk)
        return filename, filepath
    except Exception as e:
        msg = f"Download failed ({url}): {e}"
        print(f"    {msg}")
        log_pipeline_error(f"Image_Downloader: {msg}")
        return None, None

def scrape_image_from_url(url, retry_count=0):
    """
    Scrape the best image from a URL using multiple fallback strategies:
    1. og:image meta tag
    2. twitter:image meta tag
    3. meta thumbnail / link image_src tags
    4. Largest <img> element (min 300px width heuristic)
    
    Retries on HTTP 429/5xx errors up to 2 times with backoff.
    """
    MAX_SCRAPE_RETRIES = 2
    try:
        url = clean_url(url)
        if not url or not url.startswith('http'): return None
        with print_lock:
            print(f"    Scraping source: {url[:60]}...")
        time.sleep(random.uniform(0.3, 0.6))
        headers = get_headers()
        response = requests.get(url, headers=headers, timeout=(5, 15))
        
        # Retry on rate limit or server errors
        if response.status_code in (429, 500, 502, 503, 504) and retry_count < MAX_SCRAPE_RETRIES:
            with print_lock:
                print(f"    ⚠️ HTTP {response.status_code}, retrying in 2s... (attempt {retry_count+1})")
            time.sleep(2 + random.uniform(0, 1))
            return scrape_image_from_url(url, retry_count + 1)
        
        if response.status_code >= 400: return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        image_url = None
        
        # Strategy 1: og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'): 
            image_url = og_image['content']
        
        # Strategy 2: twitter:image
        if not image_url:
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if not twitter_image:
                twitter_image = soup.find('meta', attrs={'property': 'twitter:image'})
            if twitter_image and twitter_image.get('content'): 
                image_url = twitter_image['content']
        
        # Strategy 3: meta thumbnail / link image_src
        if not image_url:
            thumbnail = soup.find('meta', attrs={'name': 'thumbnail'})
            if thumbnail and thumbnail.get('content'):
                image_url = thumbnail['content']
        
        if not image_url:
            image_src = soup.find('link', attrs={'rel': 'image_src'})
            if image_src and image_src.get('href'):
                image_url = image_src['href']
        
        # Strategy 4: schema.org image
        if not image_url:
            schema_img = soup.find('meta', attrs={'itemprop': 'image'})
            if schema_img and schema_img.get('content'):
                image_url = schema_img['content']
        
        # Strategy 5: Largest <img> element with size heuristic
        if not image_url:
            candidates = []
            for img in soup.find_all('img'):
                src = img.get('src', '') or img.get('data-src', '') or img.get('data-lazy-src', '')
                if not src or src.startswith('data:'):
                    continue
                # Skip tiny images (icons, logos, tracking pixels)
                width = img.get('width', '')
                height = img.get('height', '')
                try:
                    w = int(str(width).replace('px', '').strip()) if width else 0
                    h = int(str(height).replace('px', '').strip()) if height else 0
                except (ValueError, TypeError):
                    w, h = 0, 0
                
                # Skip elements that look like ads or icons
                classes = ' '.join(img.get('class', []))
                alt = img.get('alt', '')
                if any(x in classes.lower() for x in ['logo', 'icon', 'avatar', 'ad-', 'advert', 'banner']):
                    continue
                if any(x in src.lower() for x in ['logo', 'icon', 'avatar', 'pixel', 'tracking', '1x1', 'spacer']):
                    continue
                
                # Score: prefer images with explicit large dimensions or article-context
                score = 0
                if w >= 300: score += 3
                elif w >= 200: score += 2
                elif w > 0: score += 1
                if h >= 200: score += 2
                if alt and len(alt) > 10: score += 1  # Has descriptive alt text
                if 'article' in classes.lower() or 'featured' in classes.lower() or 'hero' in classes.lower():
                    score += 3
                
                if score > 0 or (w == 0 and h == 0):  # Include unknown-size images as lowest priority
                    candidates.append((score, src))
            
            if candidates:
                # Sort by score descending, pick highest
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_src = candidates[0][1]
                image_url = best_src
        
        if image_url:
            # Fix for sg.hu malformed double URLs (e.g. https://sg.hu/https://media.sg.hu/...)
            if 'https://sg.hu/https://' in image_url:
                image_url = image_url.replace('https://sg.hu/', '')
            
            return urljoin(url, image_url)
        return None
    except requests.exceptions.Timeout:
        if retry_count < MAX_SCRAPE_RETRIES:
            time.sleep(2)
            return scrape_image_from_url(url, retry_count + 1)
        return None
    except Exception:
        return None


def process_single_item(args, skip_upload=False):
    """
    Process a single news item - download and copy to git repo. Thread-safe.
    
    Args:
        args: tuple(i, item, total, images_dir, folder_path, date_folder)
        skip_upload: If True, only download locally, do not copy to Git.
    """
    i, item, total, images_dir, folder_path, date_folder = args
    
    result = {
        'index': i,
        'updated': False,
        'uploaded': False,
        'new_image_url': None,
        'clear_image': False,
        'local_path': None
    }
    
    current_image = item.get('image', '')
    title = item.get('title', f"news_{i}")
    
    # 1. Check if already has a valid date-based github URL (not legacy hash folder)
    if 'raw.githubusercontent.com' in current_image and re.search(r'/\d{4}-\d{2}-\d{2}/', current_image):
        with print_lock:
            print(f"[{i+1}/{total}] OK (Already in Git Repo): {title[:30]}")
        return result
    
    # 1b. Check if this article's sourceLink already has a github image in historical data
    #     (prevents re-downloading images for old articles re-discovered by RSS)
    source_link = item.get('sourceLink', '')
    historical_img = get_historical_image(source_link)
    if historical_img:
        with print_lock:
            print(f"[{i+1}/{total}] ♻️ Reusing historical Git image: {title[:30]}")
        result['new_image_url'] = historical_img
        result['updated'] = True
        result['uploaded'] = True
        return result
    
    with print_lock:
        print(f"[{i+1}/{total}] Processing: {title[:30]}")
    
    local_path_rel = item.get('local_image_path', '')
    full_local_path = os.path.join(folder_path, local_path_rel) if local_path_rel else None
    
    fpath = None
    slug = slugify(title)[:60]
    
    # 2. Check if we have a valid local file already (by slug pattern)
    if full_local_path and os.path.exists(full_local_path):
        with print_lock:
            print(f"    [{i+1}] -> Using existing local file: {local_path_rel}")
        fpath = full_local_path
    else:
        # Check for any existing file with matching slug in images_dir
        existing_files = [f for f in os.listdir(images_dir) if f.startswith(slug)] if os.path.exists(images_dir) else []
        if existing_files:
            fpath = os.path.join(images_dir, existing_files[0])
            with print_lock:
                print(f"    [{i+1}] -> Found cached local file: {existing_files[0]}")
        else:
            # 3. Need to download
            source_url = clean_url(item.get('sourceLink', ''))
            download_success = False
            
            # Attempt 1: Existing URL
            if current_image and current_image.startswith('http'):
                target_url = current_image
                referer = source_url 
                fname, downloaded_path = download_image(target_url, images_dir, slug, referer)
                if downloaded_path:
                    fpath = downloaded_path
                    result['updated'] = True
                    download_success = True
                else:
                    with print_lock:
                        print(f"    [{i+1}] Existing image download failed. Trying scrape...")
            
            # Attempt 2: Scrape if not successful yet
            if not download_success and source_url:
                scraped = scrape_image_from_url(source_url)
                if scraped:
                    target_url = scraped
                    referer = source_url
                    fname, downloaded_path = download_image(target_url, images_dir, slug, referer)
                    if downloaded_path:
                        fpath = downloaded_path
                        result['updated'] = True
                        download_success = True
                    else:
                        with print_lock:
                            print(f"    [{i+1}] Scraped image download failed.")
                else:
                    with print_lock:
                        print(f"    [{i+1}] No image found via scraping.")

    # 4. Copy to Git Repo if we have a file
    upload_success = False
    
    if skip_upload and fpath:
        with print_lock:
             print(f"    [{i+1}] Downloaded locally only (Copy suspended): {os.path.basename(fpath)}")
        if folder_path and fpath.startswith(folder_path):
             result['local_path'] = os.path.relpath(fpath, folder_path)
             result['updated'] = True
        return result
        
    try:
        if fpath:
            raw_url = copy_to_git_repo(fpath, date_folder)
            if raw_url:
                result['new_image_url'] = raw_url
                result['uploaded'] = True
                result['updated'] = True
                upload_success = True
                # Delete local file from Output after successful copy
                try:
                    os.remove(fpath)
                except Exception:
                    pass
            else:
                with print_lock:
                    print(f"    [{i+1}] Copy to Git Repo failed.")
        elif not fpath:
            with print_lock:
                print(f"    [{i+1}] Skipping upload (No file)")
                
    except Exception as e:
        with print_lock:
            print(f"    [{i+1}] ⚠️ Unknown Error during Git Copy: {e}. Saving local only.")
        if folder_path and fpath and fpath.startswith(folder_path):
             result['local_path'] = os.path.relpath(fpath, folder_path)
             result['updated'] = True
        return result
    
    if not upload_success:
        if item.get('image') and 'raw.githubusercontent.com' not in item.get('image', ''):
            with print_lock:
                print(f"    [{i+1}] Keeping non-Github image link, or clearing it if broken.")
            # Clear it so front-end does not attempt to display broken links
            result['clear_image'] = True
            result['updated'] = True
    
    return result


def _process_data_file(data_path, images_dir, folder_path, date_folder, skip_upload=False):
    """Process a single data file for image downloading. Returns (updated_count, git_count)."""
    if not os.path.exists(data_path):
        return 0, 0
    
    filename = os.path.basename(data_path)
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_items = json.load(f)
        
        if not news_items:
            return 0, 0
        
        total = len(news_items)
        updated_count = 0
        git_count = 0
        
        # Count items that actually need processing
        needs_processing = sum(1 for item in news_items
                              if not (item.get('image', '') and 'raw.githubusercontent.com' in item.get('image', '')
                                      and re.search(r'/\d{4}-\d{2}-\d{2}/', item.get('image', ''))))
        if needs_processing == 0:
            print(f"  [{filename}] All {total} items already have Git images. Skipping.")
            return 0, 0
        
        print(f"  [{filename}] Processing {needs_processing}/{total} items needing images...")
        
        # Cleanup local_image_path first (sequential, quick) — but only when uploads are active
        if not skip_upload:
            for item in news_items:
                if 'local_image_path' in item:
                    del item['local_image_path']
                    updated_count += 1
        
        # Prepare tasks for parallel processing
        tasks = []
        for i, item in enumerate(news_items):
            tasks.append((i, item, total, images_dir, folder_path, date_folder))
        
        # Process in parallel with MAX_WORKERS threads
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_idx = {executor.submit(process_single_item, task, skip_upload=skip_upload): task[0] for task in tasks}
            
            for future in as_completed(future_to_idx):
                try:
                    result = future.result(timeout=120)  # 2 min timeout per item
                    results.append(result)
                except Exception as e:
                    idx = future_to_idx[future]
                    with print_lock:
                        print(f"    [{idx+1}] Error: {e}")
        
        # Apply results to news_items
        failed_indices = []
        for result in results:
            idx = result['index']
            if result['new_image_url']:
                news_items[idx]['image'] = result['new_image_url']
                git_count += 1
            elif result['clear_image']:
                news_items[idx]['image'] = ""
            elif not result['updated'] and not news_items[idx].get('image', '').strip():
                failed_indices.append(idx)
            if result.get('local_path'):
                news_items[idx]['local_image_path'] = result['local_path']
            if result['updated']:
                updated_count += 1

        # Retry failed items after a short delay
        if failed_indices:
            print(f"  [{filename}] ♻️ Retrying {len(failed_indices)} failed image downloads in 5s...")
            time.sleep(5)

            retry_tasks = [(i, news_items[i], total, images_dir, folder_path, date_folder) for i in failed_indices]
            retry_results = []
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(retry_tasks))) as executor:
                future_to_idx = {executor.submit(process_single_item, task, skip_upload=skip_upload): task[0] for task in retry_tasks}
                for future in as_completed(future_to_idx):
                    try:
                        result = future.result(timeout=120)
                        retry_results.append(result)
                    except Exception as e:
                        idx = future_to_idx[future]
                        with print_lock:
                            print(f"    [{idx+1}] Retry error: {e}")

            retry_success = 0
            for result in retry_results:
                idx = result['index']
                if result['new_image_url']:
                    news_items[idx]['image'] = result['new_image_url']
                    git_count += 1
                    retry_success += 1
                elif result['clear_image']:
                    news_items[idx]['image'] = ""
                if result.get('local_path'):
                    news_items[idx]['local_image_path'] = result['local_path']
                if result['updated']:
                    updated_count += 1

            print(f"  [{filename}] ♻️ Retry recovered {retry_success}/{len(failed_indices)} images.")

        if updated_count > 0:
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(news_items, f, ensure_ascii=False, indent=2)
            print(f"  [{filename}] Updated: {updated_count} items touched, {git_count} copied to Git Repo.")
        else:
            print(f"  [{filename}] No changes to save.")
        
        return updated_count, git_count
            
    except Exception as e:
        print(f"Error processing {filename}: {e}")
        import traceback
        traceback.print_exc()
        return 0, 0


def process_date_folder(date_folder, skip_upload=False):
    folder_path = os.path.join(BASE_OUTPUT_DIR, date_folder)
    images_dir = os.path.join(folder_path, 'Images')
    
    # Check if at least one data file exists
    data_files = ['data_i5.json', 'data_i4.json', 'data.json']  # Priority order: most important first
    has_any = any(os.path.exists(os.path.join(folder_path, f)) for f in data_files)
    if not has_any:
        return
    
    # Build historical image index once (before parallel threads start)
    _build_historical_image_index()
    
    upload_status = "No" if skip_upload else "Yes"
    print(f"\nProcessing {date_folder} with {MAX_WORKERS} parallel threads...")
    print(f"Copy to ImageRepo Git: {upload_status}")
    os.makedirs(images_dir, exist_ok=True)
    
    total_updated = 0
    total_git = 0
    
    for data_filename in data_files:
        data_path = os.path.join(folder_path, data_filename)
        updated, git = _process_data_file(data_path, images_dir, folder_path, date_folder, skip_upload)
        total_updated += updated
        total_git += git
    
    print(f"\n  📊 Total for {date_folder}: {total_updated} items updated, {total_git} copied to Git Repo.")


def cleanup_old_images():
    """Remove Images folders from previous days in Output to save disk space."""
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    if not os.path.exists(BASE_OUTPUT_DIR):
        return
    
    deleted_count = 0
    for folder in os.listdir(BASE_OUTPUT_DIR):
        folder_path = os.path.join(BASE_OUTPUT_DIR, folder)
        if re.match(r'\d{4}-\d{2}-\d{2}', folder) and folder != today:
            images_dir = os.path.join(folder_path, 'Images')
            if os.path.exists(images_dir):
                import shutil
                try:
                    shutil.rmtree(images_dir)
                    deleted_count += 1
                    print(f"  Deleted old Output images: {images_dir}")
                except Exception as e:
                    print(f"  Failed to delete {images_dir}: {e}")
    
    if deleted_count > 0:
        print(f"Cleaned up {deleted_count} old Images folders in Output.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help="YYYY-MM-DD")
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--no-cleanup', action='store_true', help="Skip cleanup of old images")
    parser.add_argument('--skip-upload', action='store_true', help="Download images locally but skip Git Repo copy")
    args = parser.parse_args()
    
    if args.skip_upload:
        print("📥 Copy to Git Repo disabled: images will be downloaded locally only.")
    
    # Determine target folder(s)
    if args.date:
        folders = [args.date]
    elif args.all:
        if os.path.exists(BASE_OUTPUT_DIR):
            folders = sorted([f for f in os.listdir(BASE_OUTPUT_DIR) if re.match(r'\d{4}-\d{2}-\d{2}', f)])
        else:
            folders = []
    else:
        # Pipeline usage
        env_date_dir = os.environ.get('DAILY_OUTPUT_DIR')
        if env_date_dir:
            folders = [os.path.basename(env_date_dir)]
        else:
            folders = [datetime.date.today().strftime('%Y-%m-%d')]
    
    for folder in folders:
        process_date_folder(folder, skip_upload=args.skip_upload)
    
    # Cleanup old images unless disabled
    if not args.no_cleanup:
        cleanup_old_images()


if __name__ == "__main__":
    main()
