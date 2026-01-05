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
from urllib.parse import urlparse

# Set console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

# --- CONFIGURATION ---
BASE_OUTPUT_DIR = 'Output'
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
]

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
        print(f"    Scraping source: {url}")
        time.sleep(random.uniform(0.5, 1.0))
        headers = get_headers()
        response = requests.get(url, headers=headers, timeout=10)
        # Handle simple errors, but don't crash
        if response.status_code >= 400: return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'): return og_image['content']
        twitter_image = soup.find('meta', name='twitter:image')
        if twitter_image and twitter_image.get('content'): return twitter_image['content']
        return None
    except:
        return None

def process_date_folder(date_folder, api_key):
    folder_path = os.path.join(BASE_OUTPUT_DIR, date_folder)
    data_path = os.path.join(folder_path, 'data.json')
    images_dir = os.path.join(folder_path, 'Images')
    
    if not os.path.exists(data_path): return
    print(f"\nProcessing {date_folder}...")
    print(f"API Key present: {'Yes' if api_key else 'No'}")
    os.makedirs(images_dir, exist_ok=True)
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_items = json.load(f)
        
        updated_count = 0
        imgbb_count = 0
        
        for i, item in enumerate(news_items):
            current_image = item.get('image', '')
            title = item.get('title', f"news_{i}")
            
            # 1. Check if already ImgBB
            if 'ibb.co' in current_image or 'imgbb.com' in current_image:
                print(f"[{i+1}/{len(news_items)}] OK (Already ImgBB): {title[:30]}")
                continue

            print(f"[{i+1}/{len(news_items)}] Processing: {title[:30]}")
            
            local_path_rel = item.get('local_image_path', '')
            full_local_path = os.path.join(folder_path, local_path_rel) if local_path_rel else None
            
            fpath = None
            
            # 2. Check if we have a valid local file already
            if full_local_path and os.path.exists(full_local_path):
                print(f"    -> Using existing local file: {local_path_rel}")
                fpath = full_local_path
            else:
                # 3. Need to download
                slug = slugify(title)[:60]
                source_url = clean_url(item.get('sourceLink', ''))
                target_url = None
                referer = None
                
                if current_image and current_image.startswith('http'):
                    target_url = current_image
                    referer = source_url
                elif source_url:
                    scraped = scrape_image_from_url(source_url)
                    if scraped:
                        target_url = scraped
                        referer = source_url
                
                if target_url:
                    fname, downloaded_path = download_image(target_url, images_dir, slug, referer)
                    if downloaded_path:
                        fpath = downloaded_path
                        # Save local path
                        item['local_image_path'] = f"Images/{fname}"
                        updated_count += 1 # Mark as updated to save local path at least
                else:
                    print(f"    WARNING: No image source found.")

            # 4. Upload to ImgBB if we have a file
            if fpath and api_key:
                imgbb_url = upload_to_imgbb(fpath, api_key)
                if imgbb_url:
                    item['image'] = imgbb_url
                    imgbb_count += 1
                    updated_count += 1
            elif not api_key:
                print("    Skipping upload (No API Key)")
            elif not fpath:
                print("    Skipping upload (No file)")
        
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
