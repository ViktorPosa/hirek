"""
Image Downloader & Uploader (ImgBB)

Ez a script:
1. Végigmegy a data.json elemein.
2. Megkeresi a képet (meglévő URL vagy scraping).
3. Letölti a képet egy ideiglenes helyre (Output/Images/).
4. FELTÖLTI az ImgBB-re (névtelenül vagy API kulccsal).
5. Frissíti a data.json 'image' mezőjét a direkt linkre.
6. (Opcionális) Megtartja vagy törli a helyi fájlt (jelenleg megtartja cache-ként, de gitignore védi).

A script az 'IMGBB_API_KEY' környezeti változót keresi, vagy a parancssorból várja.
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

# Set console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'
MAX_WORKERS = 20  # Number of parallel threads for image scraping/downloading
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
]

# Thread-safe print lock
print_lock = threading.Lock()

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

def upload_to_imgbb(image_path, api_key, expiration=None):
    """
    Uploads an image to ImgBB anonymously and returns the direct link.
    """
    url = "https://api.imgbb.com/1/upload"
    try:
        print(f"    Uploading to ImgBB...")
        with open(image_path, "rb") as file:
            payload = {
                "key": api_key,
                "image": base64.b64encode(file.read()),
            }
            if expiration:
                payload["expiration"] = expiration

            response = requests.post(url, data=payload, timeout=60)
            
            if response.status_code != 200:
                print(f"    ImgBB Upload Error: {response.status_code} - {response.text}")
                return None

            json_data = response.json()
            if json_data["status"] == 200:
                direct_link = json_data["data"]["url"]
                print(f"    ✅ Upload Successful! Link: {direct_link}")
                return direct_link
            else:
                print(f"    ❌ ImgBB logic error: {json_data.get('error', {}).get('message')}")
                return None
    except Exception as e:
        print(f"    ❌ Failed to upload: {e}")
        return None

def download_image(url, save_dir, filename_base, referer=None):
    try:
        url = clean_url(url)
        if not url: return None
        
        headers = get_headers(referer=referer, is_image=True)
        print(f"    Downloading: {url[:50]}... (Ref: {referer[:30] if referer else '-'})")
        
        response = requests.get(url, headers=headers, timeout=20, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        if 'image/jpeg' in content_type or 'jpg' in url.lower(): ext = '.jpg'
        elif 'image/png' in content_type or 'png' in url.lower(): ext = '.png'
        elif 'image/webp' in content_type: ext = '.webp'
        elif 'image/gif' in content_type: ext = '.gif'
        elif 'image/svg' in content_type: ext = '.svg'
        else: ext = '.jpg'
            
        filename = f"{filename_base}{ext}"
        filepath = os.path.join(save_dir, filename)
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename, filepath
    except Exception as e:
        print(f"    Download failed ({url}): {e}")
        return None, None

def scrape_image_from_url(url):
    try:
        url = clean_url(url)
        if not url or not url.startswith('http'): return None
        with print_lock:
            print(f"    Scraping source: {url[:60]}...")
        time.sleep(random.uniform(0.3, 0.6))
        headers = get_headers()
        response = requests.get(url, headers=headers, timeout=10)
        # Handle simple errors, but don't crash
        if response.status_code >= 400: return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        image_url = None
        
        # Try og:image first
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'): 
            image_url = og_image['content']
        else:
            # Fallback to twitter:image
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'): 
                image_url = twitter_image['content']
        
        if image_url:
            # Fix for sg.hu malformed double URLs (e.g. https://sg.hu/https://media.sg.hu/...)
            if 'https://sg.hu/https://' in image_url:
                image_url = image_url.replace('https://sg.hu/', '')
            
            return urljoin(url, image_url)
        return None
    except:
        return None

def process_single_item(args):
    """Process a single news item - download and upload image. Thread-safe."""
    i, item, total, images_dir, folder_path, api_key = args
    
    result = {
        'index': i,
        'updated': False,
        'uploaded': False,
        'new_image_url': None,
        'clear_image': False
    }
    
    current_image = item.get('image', '')
    title = item.get('title', f"news_{i}")
    
    # 1. Check if already ImgBB
    if 'ibb.co' in current_image or 'imgbb.com' in current_image:
        with print_lock:
            print(f"[{i+1}/{total}] OK (Already ImgBB): {title[:30]}")
        return result
    
    with print_lock:
        print(f"[{i+1}/{total}] Processing: {title[:30]}")
    
    local_path_rel = item.get('local_image_path', '')
    full_local_path = os.path.join(folder_path, local_path_rel) if local_path_rel else None
    
    fpath = None
    
    # 2. Check if we have a valid local file already
    if full_local_path and os.path.exists(full_local_path):
        with print_lock:
            print(f"    [{i+1}] -> Using existing local file: {local_path_rel}")
        fpath = full_local_path
    else:
        # 3. Need to download
        slug = slugify(title)[:60]
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
                if not download_success:
                    with print_lock:
                        print(f"    [{i+1}] No image found via scraping.")

    # 4. Upload to ImgBB if we have a file
    upload_success = False
    if fpath and api_key:
        imgbb_url = upload_to_imgbb(fpath, api_key)
        if imgbb_url:
            result['new_image_url'] = imgbb_url
            result['uploaded'] = True
            result['updated'] = True
            upload_success = True
        else:
            with print_lock:
                print(f"    [{i+1}] ImgBB Upload failed.")
    elif not api_key:
        with print_lock:
            print(f"    [{i+1}] Skipping upload (No API Key)")
    elif not fpath:
        with print_lock:
            print(f"    [{i+1}] Skipping upload (No file)")
    
    # CRITICAL: If not ImgBB, clear the image field to avoid external hotlinks
    if not upload_success and api_key:
        if item.get('image') and 'ibb.co' not in item.get('image', ''):
            with print_lock:
                print(f"    [{i+1}] Clearing non-ImgBB image link due to failure.")
            result['clear_image'] = True
            result['updated'] = True
    
    return result


def process_date_folder(date_folder, api_key):
    folder_path = os.path.join(BASE_OUTPUT_DIR, date_folder)
    data_path = os.path.join(folder_path, 'data.json')
    images_dir = os.path.join(folder_path, 'Images')
    
    if not os.path.exists(data_path): return
    print(f"\nProcessing {date_folder} with {MAX_WORKERS} parallel threads...")
    print(f"API Key present: {'Yes' if api_key else 'No'}")
    os.makedirs(images_dir, exist_ok=True)
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_items = json.load(f)
        
        total = len(news_items)
        updated_count = 0
        imgbb_count = 0
        
        # Cleanup local_image_path first (sequential, quick)
        for item in news_items:
            if 'local_image_path' in item:
                del item['local_image_path']
                updated_count += 1
        
        # Prepare tasks for parallel processing
        tasks = []
        for i, item in enumerate(news_items):
            tasks.append((i, item, total, images_dir, folder_path, api_key))
        
        # Process in parallel with MAX_WORKERS threads
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_idx = {executor.submit(process_single_item, task): task[0] for task in tasks}
            
            for future in as_completed(future_to_idx):
                try:
                    result = future.result(timeout=120)  # 2 min timeout per item
                    results.append(result)
                except Exception as e:
                    idx = future_to_idx[future]
                    with print_lock:
                        print(f"    [{idx+1}] Error: {e}")
        
        # Apply results to news_items
        for result in results:
            idx = result['index']
            if result['new_image_url']:
                news_items[idx]['image'] = result['new_image_url']
                imgbb_count += 1
            if result['clear_image']:
                news_items[idx]['image'] = ""
            if result['updated']:
                updated_count += 1
        
        if updated_count > 0:
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(news_items, f, ensure_ascii=False, indent=2)
            print(f"  Updated data.json: {updated_count} items touched, {imgbb_count} uploaded to ImgBB.")
        else:
            print("  No changes to save.")
            
    except Exception as e:
        print(f"Error processing {date_folder}: {e}")
        import traceback
        traceback.print_exc()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help="YYYY-MM-DD")
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--key', type=str, help="ImgBB API Key")
    args = parser.parse_args()
    
    # Get API Key
    api_key = args.key or os.environ.get('IMGBB_API_KEY')
    
    if not api_key:
        print("WARNING: No ImgBB API Key provided. Images will be downloaded locally but NOT uploaded.")
    
    if args.date:
        process_date_folder(args.date, api_key)
    elif args.all:
        if os.path.exists(BASE_OUTPUT_DIR):
            folders = sorted([f for f in os.listdir(BASE_OUTPUT_DIR) if re.match(r'\d{4}-\d{2}-\d{2}', f)])
            for folder in folders:
                process_date_folder(folder, api_key)
    else:
        # Pipeline usage
        today = datetime.date.today().strftime('%Y-%m-%d')
        env_date_dir = os.environ.get('DAILY_OUTPUT_DIR')
        if env_date_dir:
            date_folder = os.path.basename(env_date_dir)
            process_date_folder(date_folder, api_key)
        else:
            process_date_folder(today, api_key)

if __name__ == "__main__":
    main()
