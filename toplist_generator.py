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
            today_str = datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d')
            
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

def scrape_mozipremierek(url, images_dir):
    logging.info(f"Scraping Mozipremierek: {url}")
    resp = requests.get(url, impersonate="chrome110", timeout=15)
    resp.raise_for_status()
    
    soup = BeautifulSoup(resp.content, "html.parser")
    blocks = soup.find_all("section", class_="item-list-section")
    
    articles = []
    
    for block in blocks:
        title_h2 = block.find("h2")
        if not title_h2:
            continue
            
        block_title = title_h2.get_text(strip=True)
        # We only want specific blocks usually
        if "Ezen a héten a mozikban" not in block_title and "Jövő héten érkezik" not in block_title:
             continue
             
        items = []
        # In Mozipremierek, list items are inside a `ul` with class `movie-list` and contain `li`
        list_container = block.find("ul", class_=lambda c: c and "movie-list" in c)
        if list_container:
            movie_items = list_container.find_all("li")
        else:
            movie_items = block.find_all("li", class_="list-group-item") or block.find_all("div", class_="list-group-item")
        
        for m in movie_items:
             # Title is usually the <a> with class="movie" and its aria-label or inner text
             name_elem = m.find("a", class_="movie")
             if name_elem:
                 name_str = name_elem.get('aria-label') or name_elem.get_text(strip=True)
             else:
                 name_elem = m.find("h4")
                 name_str = name_elem.get_text(strip=True) if name_elem else ""
                 
             if not name_str: continue

             # Extract details from the javascript injection or text blocks
             desc_str = ""
             date_str = ""
             
             # Fallback: Extract all raw text inside item-content span
             content_span = m.find("span", class_="item-content")
             if content_span:
                 # Clean up the script tag mess if any
                 for s in content_span.find_all("script"): s.extract()
                 # Get raw text
                 raw_text = content_span.get_text(" | ", strip=True)
                 parts = raw_text.split(" | ")
                 if len(parts) >= 2:
                     date_str = parts[0] + " " + parts[1]
                 if len(parts) >= 3:
                     desc_str = " | ".join(parts[2:])
             
             # Image
             img_elem = m.find("img", class_="poster-view-img") or m.find("img")
             img_url = img_elem['src'] if img_elem and 'src' in img_elem.attrs else None
             if img_elem and img_elem.has_attr('data-src'): # lazy loading
                 img_url = img_elem['data-src']
                 
             if img_url and not img_url.startswith('http'):
                 img_url = "https://mozipremierek.hu" + img_url
                 
             local_img = download_image(img_url, images_dir, prefix="mozi")
             
             items.append({
                 "title": name_str,
                 "subtitle": date_str,
                 "description": desc_str,
                 "image_path": local_img
             })
             
        if items:
            articles.append(create_article_json(block_title, url, items))
            
    return articles

def scrape_netflix(url, images_dir):
    """Scrape whats-on-netflix.com - follows the latest article from each category
    and extracts individual movie/show titles, images and descriptions from within."""
    logging.info(f"Scraping What's on Netflix: {url}")
    articles = []
    
    req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # Keywords to find the right weekly roundup article (skip region/genre-specific ones)
    SKIP_KEYWORDS = ['uk', 'canada', 'australia', 'k-drama', 'k-dramas', 'indian', 'anime', 
                     'reality show', 'renewed', 'renewals', 'cancelled', 'podcast']
    
    categories = [
        {
            "list_url": "https://www.whats-on-netflix.com/whats-new/",
            "title": "Netflix: New This Week",
            "find_article": "whats-new",
            "match_keywords": ["new releases", "new on netflix", "top 10"],
        },
        {
            "list_url": "https://www.whats-on-netflix.com/coming-soon/",
            "title": "Netflix: Coming Soon",
            "find_article": "article-list",
            "match_keywords": ["what's coming", "coming to netflix this week", "new on netflix"],
        }
    ]
    
    def _get_img_url(element):
        """Extract real image URL from an element, handling lazy loading."""
        if not element:
            return ""
        img = element.find('img') if element.name != 'img' else element
        if not img:
            return ""
        src = img.get('data-lazy-src') or img.get('data-src') or img.get('src', '')
        if 'data:image/svg' in src:
            src = img.get('data-lazy-src', '')
        return src
    
    def _search_netflix_image(title_text):
        """Search whats-on-netflix.com for a title and return the first article thumbnail."""
        import re, time, urllib.parse
        # Clean title: remove year in parentheses, 'Netflix Original', 'Season X', etc.
        clean = re.sub(r'\s*\(\d{4}\)\s*', ' ', title_text)
        clean = re.sub(r'\s*\(Season \d+\)\s*', ' ', clean)
        clean = re.sub(r'Netflix Original', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'Limited Series', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'–.*$', '', clean)  # Remove everything after dash
        clean = clean.strip()
        if len(clean) < 2:
            return ""
        try:
            search_url = f"https://www.whats-on-netflix.com/?s={urllib.parse.quote_plus(clean)}"
            resp = requests.get(search_url, headers=req_headers, timeout=8)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.content, 'html.parser')
            first_art = soup.find('article')
            if first_art:
                img = first_art.find('img')
                if img:
                    src = img.get('data-lazy-src') or img.get('data-src') or img.get('src', '')
                    if src and 'data:image/svg' not in src:
                        return src
            time.sleep(0.3)  # Rate limit
        except Exception as e:
            logging.debug(f"Image search failed for '{clean}': {e}")
        return ""
    
    def _find_latest_article_url(soup, cat):
        """Find the URL of the latest relevant weekly roundup article."""
        if cat["find_article"] == "whats-new":
            # Carousel items on whats-new page
            carousel = soup.find('section', class_='wn-recap-carousel')
            if carousel:
                track = carousel.find('div', class_='wn-recap-track')
                if track:
                    for link in track.find_all('a', href=True):
                        title = link.get_text(strip=True).lower()
                        if any(kw in title for kw in cat["match_keywords"]):
                            if not any(sk in title for sk in SKIP_KEYWORDS):
                                return link['href']
        else:
            # Article tags on coming-soon / leaving-soon pages
            for art in soup.find_all('article', limit=15):
                h2 = art.find('h2')
                if not h2:
                    continue
                a_tag = h2.find('a')
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True).lower()
                if any(kw in title for kw in cat["match_keywords"]):
                    if not any(sk in title for sk in SKIP_KEYWORDS):
                        return a_tag['href']
        return None
    
    def _extract_items_from_article(article_soup):
        """Extract individual movie/show items from within an article page."""
        entry = article_soup.find('div', class_='entry')
        if not entry:
            return []
        
        items_found = []
        seen_titles = set()
        
        # Strategy 1: Extract featured h4 items (individual movie titles, not date headers)
        for h4 in entry.find_all('h4'):
            text = h4.get_text(strip=True)
            # Normalize curly apostrophes to straight ones for matching
            text_normalized = text.lower().replace('\u2018', "'").replace('\u2019', "'")
            # Skip date headers like "Coming to Netflix on Monday" or "What's Leaving on Netflix March 2nd"
            if any(skip in text_normalized for skip in ['coming to netflix on', "what's leaving on", 
                    "what's leaving netflix", 'leaving netflix on', 'leaving netflix',
                    'movies leaving', 'series leaving', 
                    'movies added', 'series added', 'tv series added',
                    'most popular', 'full list', 'new movies added', 'new tv series added']):
                continue
            if len(text) < 3 or text.lower() in seen_titles:
                continue
            seen_titles.add(text.lower())
            
            # Find image and description in nearby siblings
            img_url = ""
            desc = ""
            sib = h4.find_next_sibling()
            depth = 0
            while sib and sib.name not in ['h3', 'h4'] and depth < 5:
                if not img_url:
                    found_img = _get_img_url(sib)
                    if found_img:
                        img_url = found_img
                if not desc and sib.name == 'p':
                    p_text = sib.get_text(strip=True)
                    if p_text and len(p_text) > 10 and not p_text.startswith('Coming to'):
                        # Take first sentence only
                        first_sentence = p_text.split('. ')[0]
                        desc = first_sentence[:150]
                sib = sib.find_next_sibling()
                depth += 1
            
            items_found.append({
                "title": text,
                "img_url": img_url,
                "description": desc
            })
        
        # Strategy 2: Collect items from <ul><li> lists and <strong> tags (daily lists)
        for li in entry.find_all('li'):
            text = li.get_text(strip=True)
            if len(text) < 3 or text.lower() in seen_titles:
                continue
            seen_titles.add(text.lower())
            items_found.append({
                "title": text,
                "img_url": "",
                "description": ""
            })
        
        # Strategy 3: Collect <strong> items inside <p> (used in coming-soon daily lists)
        for p in entry.find_all('p'):
            for strong in p.find_all('strong'):
                text = strong.get_text(strip=True)
                # Skip generic labels
                if any(skip in text.lower() for skip in ['coming to netflix', "what's leaving",
                        'welcome', 'picture credit', 'march', 'april', 'february', 'january',
                        'full list', 'note:', 'also']):
                    continue
                if len(text) < 3 or text.lower() in seen_titles:
                    continue
                # Looks like a movie/show title if it has a year in parentheses or "Netflix Original"
                if '(' in text or 'Netflix' in text or 'Season' in text:
                    seen_titles.add(text.lower())
                    items_found.append({
                        "title": text,
                        "img_url": "",
                        "description": ""
                    })
        
        return items_found
    
    for cat in categories:
        try:
            # Step 1: Get the category listing page
            resp = requests.get(cat["list_url"], headers=req_headers, timeout=15)
            resp.raise_for_status()
            list_soup = BeautifulSoup(resp.content, "html.parser")
            
            # Step 2: Find the latest relevant article URL
            article_url = _find_latest_article_url(list_soup, cat)
            if not article_url:
                logging.warning(f"  Netflix '{cat['title']}': Could not find latest article URL")
                continue
            
            logging.info(f"  Netflix '{cat['title']}': Following article -> {article_url}")
            
            # Step 3: Fetch the article page
            resp2 = requests.get(article_url, headers=req_headers, timeout=15)
            resp2.raise_for_status()
            article_soup = BeautifulSoup(resp2.content, "html.parser")
            
            # Step 4: Extract individual items
            raw_items = _extract_items_from_article(article_soup)
            
            if not raw_items:
                logging.warning(f"  Netflix '{cat['title']}': No items found in article")
                continue
            
            # Step 5: Download images and build toplist items (limit to 25)
            items = []
            for item in raw_items[:25]:
                img_url = item["img_url"]
                
                # If no image from article, search whats-on-netflix.com for one
                if not img_url:
                    img_url = _search_netflix_image(item["title"])
                
                local_img = ""
                if img_url:
                    local_img = download_image(img_url, images_dir, prefix="netflix", max_longest_side=200)
                
                items.append({
                    "title": item["title"],
                    "subtitle": "",
                    "description": item["description"],
                    "image_path": local_img
                })
            
            if items:
                articles.append(create_article_json(cat["title"], article_url, items))
                logging.info(f"  Netflix '{cat['title']}': {len(items)} items scraped")
            else:
                logging.warning(f"  Netflix '{cat['title']}': No valid items after processing")
                
        except Exception as e:
            logging.error(f"Error scraping Netflix category '{cat['title']}': {e}")
    
    return articles

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
        if "mozipremierek.hu" in url:
            all_articles.extend(scrape_mozipremierek(url, images_dir))
        elif "boardgamegeek.com" in url:
            all_articles.extend(scrape_bgg(url, images_dir))
        elif "whats-on-netflix.com" in url:
            all_articles.extend(scrape_netflix(url, images_dir))
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
