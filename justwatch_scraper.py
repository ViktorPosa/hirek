"""
JustWatch SkyShowtime Scraper
Scrapes the top 10 trending titles from JustWatch's SkyShowtime page
and saves their poster images as data_justwatch.json in the daily Output folder.
"""

import os
import json
import re
import datetime
from datetime import timezone
import logging

from curl_cffi import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Streaming providers to scrape
PROVIDERS = {
    "skyshowtime": "https://www.justwatch.com/hu/szolgaltato/skyshowtime?sort_by=trending_7_day",
    "netflix": "https://www.justwatch.com/hu/szolgaltato/netflix?sort_by=trending_7_day",
    "hbo-max": "https://www.justwatch.com/hu/szolgaltato/hbo-max?sort_by=trending_7_day"
}

# Max image size (longest side) — JustWatch supports s166, s332, s592 etc.
# s166 keeps the longest side at ~166px which is under the 200px limit
IMAGE_SIZE = "s166"


def setup_output_dir():
    """Determine the daily output directory."""
    daily_out = os.environ.get('DAILY_OUTPUT_DIR')
    if not daily_out:
        today = datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d')
        daily_out = os.path.join(os.getcwd(), 'Output', today)
    os.makedirs(daily_out, exist_ok=True)
    return daily_out


def scrape_justwatch(url, provider_name):
    """Scrape a JustWatch trending page and return top 10 items with poster URLs."""
    logging.info(f"Scraping JustWatch '{provider_name}': {url}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'hu-HU,hu;q=0.9,en;q=0.8',
    }

    try:
        resp = requests.get(url, headers=headers, impersonate="chrome110", timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"Failed to fetch JustWatch page: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")

    items = []

    # JustWatch renders title cards inside <a> tags that link to /hu/film/... or /hu/tv-sorozat/...
    # Each card has a <picture> with an <img> for the poster
    # We look for all title card elements
    title_cards = soup.select('a[href*="/hu/film/"], a[href*="/hu/tv-sorozat/"]')

    seen_titles = set()

    for card in title_cards:
        if len(items) >= 10:
            break

        # Extract title from aria-label, alt text, or inner text
        title = card.get('aria-label', '').strip()
        if not title:
            img = card.find('img')
            if img:
                title = img.get('alt', '').strip()
        if not title:
            title = card.get_text(strip=True)

        if not title or title.lower() in seen_titles:
            continue

        # Extract image URL
        img_tag = card.find('img')
        if not img_tag:
            # Try picture > source
            picture = card.find('picture')
            if picture:
                source = picture.find('source')
                if source:
                    img_url = source.get('srcset', '').split(',')[0].strip().split(' ')[0]
                else:
                    continue
            else:
                continue
        else:
            img_url = img_tag.get('src', '') or img_tag.get('data-src', '')

        if not img_url or 'justwatch' not in img_url:
            continue

        # Resize the image URL to our target size
        # JustWatch URLs look like: https://images.justwatch.com/poster/XXXXXX/s332/filename.jpg
        # We replace the sXXX part with our target size
        img_url = re.sub(r'/s\d+/', f'/{IMAGE_SIZE}/', img_url)

        # Ensure https
        if img_url.startswith('//'):
            img_url = 'https:' + img_url

        seen_titles.add(title.lower())
        items.append({
            "title": title,
            "image_url": img_url
        })
        logging.info(f"  [{len(items)}] {title}")

    if not items:
        logging.warning("No items found on JustWatch page. The page structure may have changed.")

    return items


def main():
    daily_out = setup_output_dir()
    output_file = os.path.join(daily_out, 'data_justwatch.json')

    # Check if already generated today
    if os.path.exists(output_file):
        logging.info(f"data_justwatch.json already exists for today. Overwriting...")

    results = {}
    for provider, url in PROVIDERS.items():
        items = scrape_justwatch(url, provider)
        if items:
            results[provider] = items
        else:
            logging.warning(f"Failed to scrape or no items found for {provider}.")

    if results:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        total_items = sum(len(items) for items in results.values())
        logging.info(f"✅ Saved {total_items} items across {len(results)} providers to {output_file}")
    else:
        logging.warning("No items to save.")


if __name__ == "__main__":
    main()
