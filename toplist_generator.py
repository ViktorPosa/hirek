import os
import json
import uuid
import datetime
from datetime import timezone
import logging
import argparse
import time
from urllib.parse import urlparse
import shutil

from curl_cffi import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Git Image Repository Settings
# ⚠️ REPLACE THESE WITH YOUR ACTUAL GITHUB DETAILS ⚠️
IMAGE_GITHUB_USER = "Derushir"     # e.g., "gipszjakab"
IMAGE_GITHUB_REPO = "Kepek"       # e.g., "kepek"
IMAGE_REPO_DIR = os.path.join(os.path.dirname(__file__), 'ImageRepo')

def get_bgg_driver():
    """Starts a headless Selenium driver specifically for rendering BGG"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # User agent helps avoid some basic blocks
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver
    except Exception as e:
        logging.error(f"Failed to start local BGG Selenium driver: {e}")
        return None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def setup_output_dirs():
    """Set up daily directories for toplist outputs and the local git image repo"""
    today_str = datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    daily_out = os.environ.get('DAILY_OUTPUT_DIR')
    if not daily_out:
        daily_out = os.path.join(os.getcwd(), 'Output', today_str)
    else:
        today_str = os.path.basename(daily_out)
        
    os.makedirs(daily_out, exist_ok=True)
    
    # We no longer save to Output/.../toplist_images. We save to ImageRepo/.../toplist
    images_dir = os.path.join(IMAGE_REPO_DIR, today_str, 'toplist')
    os.makedirs(images_dir, exist_ok=True)
    
    return daily_out, images_dir, today_str

def cleanup_old_images(today_dir):
    """Deletes old toplist_images from Output/ (Migration cleanup)"""
    try:
        output_dir = os.path.dirname(today_dir)
        today_name = os.path.basename(today_dir)
        
        for d in os.listdir(output_dir):
            if d.startswith('202') and d != today_name:
                old_dir = os.path.join(output_dir, d)
                if os.path.isdir(old_dir):
                    old_images = os.path.join(old_dir, 'toplist_images')
                    if os.path.exists(old_images):
                        shutil.rmtree(old_images)
                        logging.info(f"Deleted old output images folder: {old_images}")
    except Exception as e:
        logging.warning(f"Could not clean up old data: {e}")

def download_image(img_url, output_dir, prefix="img", max_longest_side=200, today_str=None):
    if not img_url:
        return ""
        
    if img_url.startswith('//'):
        img_url = "https:" + img_url
    elif img_url.startswith('/'):
        # Usually we only process absolute URLs or we assume domain. Let's return as is if unknown.
        pass
        
    try:
        # Give it a unique filename but keep extension if possible
        ext = ".jpg"
        if ".png" in img_url.lower(): ext = ".png"
        elif ".webp" in img_url.lower(): ext = ".webp"
        
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(output_dir, filename)
        
        resp = requests.get(img_url, impersonate="chrome110", timeout=10)
        resp.raise_for_status()
        
        image_data = resp.content
        
        # Resize image if requested
        if max_longest_side:
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(image_data))
                
                # Calculate new dimensions keeping aspect ratio
                longest = max(img.width, img.height)
                if longest > max_longest_side:
                    ratio = max_longest_side / longest
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    
                    # Convert to RGB if necessary (e.g., RGBA png to JPEG)
                    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                        bg = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'RGBA':
                            bg.paste(img, mask=img.split()[3])
                        else:
                            bg.paste(img)
                        img = bg
                        ext = ".jpg" # Force jpg if we converted transparency to white bg
                        filename = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
                        filepath = os.path.join(output_dir, filename)
                        
                    img = img.resize(new_size, Image.LANCZOS)
                    
                    # Save resized
                    fmt = img.format or ('PNG' if ext == '.png' else 'JPEG')
                    save_kwargs = {'quality': 85} if fmt == 'JPEG' else {}
                    img.save(filepath, format=fmt, **save_kwargs)
                else:
                    # Write original if already small enough
                    with open(filepath, 'wb') as f:
                        f.write(image_data)
            except Exception as resize_err:
                logging.warning(f"Failed to resize image, saving original: {resize_err}")
                with open(filepath, 'wb') as f:
                    f.write(image_data)
        else:
            with open(filepath, 'wb') as f:
                f.write(image_data)
            
        # Construct raw URL
        # https://raw.githubusercontent.com/<User>/<Repo>/main/<date>/toplist/<filename>
        if not today_str:
            # Safely extract date from output_dir (e.g. .../ImageRepo/2026-03-22/toplist)
            today_str = os.path.basename(os.path.dirname(output_dir))
            
        raw_url = f"https://raw.githubusercontent.com/{IMAGE_GITHUB_USER}/{IMAGE_GITHUB_REPO}/main/{today_str}/toplist/{filename}"
        return raw_url
    except Exception as e:
        logging.error(f"Failed to download image {img_url}: {e}")
        return ""

def create_article_json(title, url, items):
    """Formats the scraped lists into the standard JSON structure used by the app"""
    # Create the markdown content string
    content_lines = []
    image_links = []
    
    for idx, item in enumerate(items, 1):
        content_lines.append(f"{idx}. **{item['title']}**")
        if item.get('subtitle'):
            content_lines.append(f"*{item['subtitle']}*")
        if item.get('description'):
            content_lines.append(item['description'])
        if item.get('image_path'):
            # Standard markdown image relative so github renders it perfectly next to data.json
            content_lines.append(f"![{item['title']}]({item['image_path']})")
            image_links.append(item['image_path'])
        else:
            image_links.append("")
        content_lines.append("") # blank line separator
        
    full_content = "\n".join(content_lines)
    
    # Use first available item image as the article cover
    cover_image = ""
    for link in image_links:
        if link:
            cover_image = link
            break
    
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "content": full_content,
        "category": "SZÓRAKOZÁS", 
        "tags": [
            "toplista", "rangsor", "kedvencek", "ajánló", "top10",
            "kultúra", "szórakozás", "trend", "népszerű", "válogatás",
            urlparse(url).netloc.replace('www.', '')
        ],
        "importance": 4,   # User specifically requested this to be 4
        "image": cover_image, 
        "image_links": image_links,
        "sourceLink": url,
        "author": urlparse(url).netloc.replace('www.', ''),
        "processed_by": "toplist_generator",
        "originalTitle": title
    }


def scrape_bgg(url, images_dir):
    """Scrape BGG lists. Uses their clean JSON APIs directly to bypass Cloudflare HTML blocks."""
    logging.info(f"Scraping BGG via API: {url}")
    
    articles = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    
    try:
        items = []
        if "hotness" in url:
            api_url = "https://api.geekdo.com/api/hotness"
            title_str = "BoardGameGeek Hotness"
            resp = requests.get(api_url, headers=headers, impersonate="chrome110", timeout=15)
            resp.raise_for_status()
            data = resp.json().get('items', [])
            
            for item in data[:25]: # limit to 25 like most toplists
                name_str = item.get('name', 'Unknown')
                desc_str = item.get('description', '')
                # Try to get the highest resolution image available in the payload
                img_sets = item.get('images', {})
                img_url = img_sets.get('mediacard', {}).get('src@2x', '')
                if not img_url:
                    img_url = img_sets.get('mediacard', {}).get('src', '')
                if not img_url:
                    img_url = img_sets.get('square100', {}).get('src@2x', '')
                if not img_url:
                    img_url = img_sets.get('square100', {}).get('src', '')
                if not img_url:
                    img_url = item.get('imageurl', '')
                
                local_img = download_image(img_url, images_dir, prefix="bgg", max_longest_side=200)
                
                items.append({
                    "title": name_str,
                    "subtitle": str(item.get('yearpublished', '')),
                    "description": desc_str,
                    "image_path": local_img
                })
                
        elif "trends/mostplayed" in url or "trends/bestsellers" in url:
            # We redirect both conceptually to mostplayed since there is no raw bestsellers API exposed simply without auth
            api_url = "https://api.geekdo.com/api/trends/plays?interval=month"
            title_str = "BoardGameGeek Most Played" if "mostplayed" in url else "BoardGameGeek Bestsellers (Fallback to Plays)"
            resp = requests.get(api_url, headers=headers, impersonate="chrome110", timeout=15)
            resp.raise_for_status()
            data = resp.json().get('items', [])
            
            for d in data[:25]:
                inner = d.get('item', {})
                name_str = inner.get('name', 'Unknown')
                
                # Try to get the highest resolution image available in the payload
                img_sets = inner.get('imageSets', {})
                img_url = img_sets.get('mediacard', {}).get('src@2x', '')
                if not img_url:
                    img_url = img_sets.get('mediacard', {}).get('src', '')
                if not img_url:
                    img_url = img_sets.get('square100', {}).get('src@2x', '')
                if not img_url:
                    img_url = img_sets.get('square100', {}).get('src', '')
                
                desc_str = d.get('description', '')
                local_img = download_image(img_url, images_dir, prefix="bgg", max_longest_side=200)
                
                items.append({
                    "title": name_str,
                    "subtitle": f"{d.get('appearances', '')} months on the list",
                    "description": desc_str,
                    "image_path": local_img
                })

        if items:
            articles.append(create_article_json(title_str, url, items))
            
    except Exception as e:
         logging.error(f"Error scraping BGG API {url}: {e}")
            
    return articles

def append_to_data_json(articles, daily_out):
    if not articles:
        return
        
    # Write toplists to their own file
    data_file = os.path.join(daily_out, 'data_toplist.json')
    existing_data = []
    
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read {data_file}: {e}")
            
    # Filter out previously generated toplists to avoid duplicates on re-runs
    filtered_data = [item for item in existing_data if item.get('processed_by') != 'toplist_generator']
    
    # Append new
    filtered_data.extend(articles)
    
    try:
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, ensure_ascii=False, indent=2)
        logging.info(f"Successfully wrote {len(articles)} toplist articles to data_toplist.json")
    except Exception as e:
        logging.error(f"Failed to write to {data_file}: {e}")
    
    # Also clean up any toplist entries from data.json (migration)
    main_data_file = os.path.join(daily_out, 'data.json')
    if os.path.exists(main_data_file):
        try:
            with open(main_data_file, 'r', encoding='utf-8') as f:
                main_data = json.load(f)
            cleaned = [item for item in main_data if item.get('processed_by') != 'toplist_generator']
            if len(cleaned) < len(main_data):
                with open(main_data_file, 'w', encoding='utf-8') as f:
                    json.dump(cleaned, f, ensure_ascii=False, indent=2)
                logging.info(f"Removed {len(main_data) - len(cleaned)} toplist entries from data.json")
        except Exception as e:
            logging.error(f"Failed to clean data.json: {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate JSON articles from configured toplist sites.")
    parser.add_argument("--force", action="store_true", help="Run even if already ran today.")
    args = parser.parse_args()

    daily_out, images_dir, today_str = setup_output_dirs()
    done_file = os.path.join(daily_out, '.toplists_done')
    
    if os.path.exists(done_file) and not args.force:
        logging.info("Toplists already generated today. Skipping. (Use --force to override)")
        return
        
    cleanup_old_images(daily_out)
    
    input_file = os.path.join("Input", "toplists.txt")
    if not os.path.exists(input_file):
        logging.warning(f"Could not find {input_file}. Cannot generate toplists.")
        return
        
    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
    all_articles = []
    for url in urls:
        if "boardgamegeek.com" in url:
            all_articles.extend(scrape_bgg(url, images_dir))
        else:
            logging.warning(f"No configured scraper for URL: {url}")
            
    if all_articles:
        append_to_data_json(all_articles, daily_out)
        
        # Mark as done
        with open(done_file, 'w') as f:
            f.write(datetime.datetime.now(timezone.utc).isoformat())
    else:
        logging.warning("No toplist articles were generated.")

if __name__ == "__main__":
    main()
