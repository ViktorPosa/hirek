"""
Image Downloader Script

Ez a script végigmegy a megadott (vagy mai) napi mappán (Output/YYYY-MM-DD),
betölti a data.json-t, és letölti a hírekhez tartozó képeket az Images mappába.
Ha nincs kép URL, megpróbálja lescrapelni a forrásoldalról (og:image).
Frissíti a data.json-t a lokális elérési úttal (local_image_path).

Fejlett védelem-megkerülés: Használja a cikk URL-jét Referer-ként.
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
from urllib.parse import urlparse

# Set console encoding to UTF-8 to avoid charmap errors on Windows
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
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
]

def slugify(value, allow_unicode=False):
    """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def clean_url(url):
    """Cleans up URL by removing trailing dots or garbage."""
    if not url:
        return ""
    return url.strip().rstrip('.').rstrip(',').rstrip(';')

def get_headers(referer=None, is_image=False):
    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept-Language': 'hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    if is_image:
        headers['Accept'] = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        headers['Sec-Fetch-Dest'] = 'image'
        headers['Sec-Fetch-Mode'] = 'no-cors'
        headers['Sec-Fetch-Site'] = 'cross-site'
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        headers['Sec-Fetch-Dest'] = 'document'
        headers['Sec-Fetch-Mode'] = 'navigate'
        headers['Sec-Fetch-Site'] = 'none' # Direct navigation
        headers['Sec-Fetch-User'] = '?1'

    if referer:
        headers['Referer'] = referer
        # Add Origin if applicable (mostly for POST, but can help)
        try:
            parsed = urlparse(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            # headers['Origin'] = origin # Usually not needed for GET
        except:
            pass
            
    return headers

def scrape_image_from_url(url):
    """Próbál képet kinyerni az oldal metaadataiból (og:image)."""
    try:
        url = clean_url(url)
        if not url or not url.startswith('http'):
            return None
            
        print(f"    Scraping image from: {url}")
        # Use random delay
        time.sleep(random.uniform(0.5, 1.5))
        
        headers = get_headers() # No referer for main page or google? 
        # For scraping, acting like a direct visitor is best.
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 1. Próba: og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
            
        # 2. Próba: twitter:image
        twitter_image = soup.find('meta', name='twitter:image')
        if twitter_image and twitter_image.get('content'):
            return twitter_image['content']
            
        # 3. Próba: link rel="image_src" (old standard)
        link_image = soup.find('link', rel='image_src')
        if link_image and link_image.get('href'):
            return link_image['href']
            
        # 4. JSON-LD Structured Data
        # (Simplified check)
        
        return None
        
    except Exception as e:
        print(f"    Scraping failed: {e}")
        return None

def download_image(url, save_dir, filename_base, referer=None):
    """Letölt egy képet és elmenti. Visszaadja a fájlnevet."""
    try:
        url = clean_url(url)
        if not url: 
            return None
            
        # Determine strict Referer
        # If we have a scraped URL, the referer should be the article page.
        # If we have a direct CDN link from feed, maybe referer should be the main site or the article?
        # Safe bet: Article URL (referer param)
        
        headers = get_headers(referer=referer, is_image=True)
        
        print(f"    Downloading: {url[:60]}... (Ref: {referer[:40] if referer else 'None'})")
        
        # Stream download
        response = requests.get(url, headers=headers, timeout=20, stream=True)
        response.raise_for_status()
        
        # Kiterjesztés meghatározása
        content_type = response.headers.get('content-type', '').lower()
        if 'image/jpeg' in content_type or 'jpg' in url.lower():
            ext = '.jpg'
        elif 'image/png' in content_type or 'png' in url.lower():
            ext = '.png'
        elif 'image/webp' in content_type or 'webp' in url.lower():
            ext = '.webp'
        elif 'image/gif' in content_type:
            ext = '.gif'
        elif 'image/svg' in content_type:
            ext = '.svg'
        else:
            ext = '.jpg' # Fallback
            
        filename = f"{filename_base}{ext}"
        filepath = os.path.join(save_dir, filename)
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        return filename
        
    except Exception as e:
        print(f"    Download failed ({url}): {e}")
        return None

def process_date_folder(date_folder):
    """Feldolgozza az adott napi mappát."""
    folder_path = os.path.join(BASE_OUTPUT_DIR, date_folder)
    data_path = os.path.join(folder_path, 'data.json')
    images_dir = os.path.join(folder_path, 'Images')
    
    if not os.path.exists(data_path):
        print(f"Skipping {date_folder}: data.json not found")
        return
        
    print(f"\nProcessing {date_folder}...")
    
    # Create Images directory if not exists
    os.makedirs(images_dir, exist_ok=True)
    
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            news_items = json.load(f)
            
        updated_count = 0
        
        for i, item in enumerate(news_items):
            title = item.get('title', f"news_{i}")
            slug = slugify(title)[:60] # Limit length
            
            # Check if local image already exists and is valid
            local_path = item.get('local_image_path', '')
            if local_path and os.path.exists(os.path.join(folder_path, local_path)):
                # Already downloaded
                continue
            
            image_url = clean_url(item.get('image', ''))
            source_url = clean_url(item.get('sourceLink', ''))
            
            final_image_filename = None
            
            # 1. Try existing image URL
            if image_url and image_url.startswith('http'):
                # Use source_url as referer
                final_image_filename = download_image(image_url, images_dir, slug, referer=source_url)
            
            # 2. If failed/missing, try scraping
            if not final_image_filename and source_url:
                print(f"  Image not found/failed, scraping source...")
                scraped_url = scrape_image_from_url(source_url)
                if scraped_url:
                    # Try downloading scraped image, using source_url as referer
                    final_image_filename = download_image(scraped_url, images_dir, slug, referer=source_url)
                    
                    if final_image_filename:
                        item['image'] = scraped_url # Update to what we found
            
            # 3. Save local path
            if final_image_filename:
                # Relative path from date folder
                item['local_image_path'] = f"Images/{final_image_filename}"
                updated_count += 1
                time.sleep(random.uniform(0.2, 0.5))
            else:
                print(f"  WARNING: Could not find image for '{title[:30]}...'")
        
        # Save back updated json
        if updated_count > 0:
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(news_items, f, ensure_ascii=False, indent=2)
            print(f"  Updated data.json with {updated_count} new images.")
        else:
            print("  No new images downloaded (all up to date or failed).")
            
    except Exception as e:
        print(f"Error processing {date_folder}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Download images for news items")
    parser.add_argument('--date', type=str, help="Specific date (YYYY-MM-DD) to process")
    parser.add_argument('--all', action='store_true', help="Process all date folders")
    args = parser.parse_args()
    
    if args.date:
        process_date_folder(args.date)
    elif args.all:
        # Process all folders
        if os.path.exists(BASE_OUTPUT_DIR):
            folders = sorted([f for f in os.listdir(BASE_OUTPUT_DIR) if re.match(r'\d{4}-\d{2}-\d{2}', f)])
            for folder in folders:
                process_date_folder(folder)
    else:
        # Default: Process today (or whatever DAILY_OUTPUT_DIR points to, or just today's date)
        today = datetime.date.today().strftime('%Y-%m-%d')
        # Check if environment variable is set (from pipeline)
        env_date_dir = os.environ.get('DAILY_OUTPUT_DIR')
        if env_date_dir:
            date_folder = os.path.basename(env_date_dir)
            process_date_folder(date_folder)
        else:
            process_date_folder(today)

if __name__ == "__main__":
    main()
