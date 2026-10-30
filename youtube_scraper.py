#!/usr/bin/env python3
"""
YouTube Scraper — monitors YouTube creators for new videos (last 24h),
summarizes them in Hungarian via free Gemini API, and appends to data.json.
"""

import os
import sys
import json
import re
import datetime
import feedparser
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

CREATORS_FILE = os.path.join('Input', 'youtube_creators.txt')
CHANNEL_CACHE_FILE = os.path.join('Input', 'youtube_channel_cache.json')

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)
    os.makedirs(DAILY_OUTPUT_DIR, exist_ok=True)

YOUTUBE_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
MAX_AGE_HOURS = 24
BATCH_SIZE = 10

SUMMARY_PROMPT = """Szerep: Tapasztalt magyar újságíró vagy, aki YouTube videókról készít olyan részletes TLDR összefoglalókat, hogy az olvasónak NE KELLJEN megnéznie a videót a tartalom megértéséhez.

Feladat: Foglald össze az alábbi YouTube videók TÉNYLEGES TARTALMÁT. NE ajánlót írj, NE teasert, hanem írd le konkrétan, MI HANGZIK EL a videóban. Ha a videó egy toplistát tartalmaz, sorold fel a konkrét elemeket. Ha véleményt mond, írd le mit gondol és miért. Ha bemutat valamit, írd le a lényeget számokkal, nevekkel, konkrétumokkal.

ANTI-CLICKBAIT SZABÁLY: Az olvasó a te összefoglalódból MINDENT megtudjon, ami a videóban elhangzik. Tiltott: "a videóból kiderül", "érdemes megnézni", "részletek a videóban", "meglepő fordulat".

KIMENET FORMÁTUM (KIZÁRÓLAG nyers JSON, markdown keret NÉLKÜL!):

[
  {{
    "section": "szorakozas",
    "title": "Magyar nyelvű, informatív, lényegre törő cím",
    "content": "5-8 mondatos TLDR összefoglaló a videó tényleges tartalmáról. Konkrét nevek, számok, állítások, vélemények. A végén: A videó itt tekinthető meg: [YouTube link]",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10"],
    "importance": 2,
    "image": "",
    "sourceLink": "A PONTOS YouTube URL",
    "author": "A csatorna neve"
  }}
]

SZABÁLYOK:
- A section MINDIG "szorakozas" legyen!
- A sourceLink KÖTELEZŐEN a pontos YouTube videó URL legyen, VÁLTOZTATÁS NÉLKÜL!
- A tags pontosan 10 releváns címke legyen, # nélkül, kisbetűvel!
- Az importance értéke 1-5 között, egy átlagos magyar néző szempontjából.
- A content mező végén MINDIG legyen a videó linkje: "A videó itt tekinthető meg: [link]"
- A content NEM lehet ajánló vagy teaser! Konkrétan írd le, MI van a videóban!
- Ha toplista: sorold fel az elemeket! Ha vélemény: írd le mit gondol és miért!
- Ékezetes karakterek UTF-8 kódolásban.
- Az idézőjeleket escape-eld a content mezőben!
- NE használj markdown keretet (```json)!
- no newline inside strings!

Feldolgozandó videók:
{videos}"""


def load_channel_cache():
    if os.path.exists(CHANNEL_CACHE_FILE):
        with open(CHANNEL_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_channel_cache(cache):
    with open(CHANNEL_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def resolve_channel_id(handle_url):
    """Resolve a YouTube @handle URL to a channel ID using curl_cffi."""
    from curl_cffi import requests

    cache = load_channel_cache()
    if handle_url in cache:
        return cache[handle_url]

    print(f"  Resolving channel ID for {handle_url}...")
    try:
        # Normalize URL to www.youtube.com
        resolved_url = handle_url.replace('://youtube.com/', '://www.youtube.com/')
        if not resolved_url.startswith('https://'):
            resolved_url = resolved_url.replace('http://', 'https://')
        socs_cookie = 'CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AxGgJlbiACGgYIgJnSmQY'
        resp = requests.get(resolved_url, impersonate='chrome',
                            timeout=(10, 15), cookies={'SOCS': socs_cookie})

        match = re.search(r'"externalId":"(UC[^"]+)"', resp.text)
        if match:
            channel_id = match.group(1)
            cache[handle_url] = channel_id
            save_channel_cache(cache)
            print(f"    → {channel_id}")
            return channel_id

        match = re.search(r'"channelId":"(UC[^"]+)"', resp.text)
        if match:
            channel_id = match.group(1)
            cache[handle_url] = channel_id
            save_channel_cache(cache)
            print(f"    → {channel_id}")
            return channel_id

        print(f"    ⚠️ Could not extract channel ID from {handle_url}")
        return None
    except Exception as e:
        print(f"    ⚠️ Error resolving {handle_url}: {e}")
        return None


def fetch_recent_videos(channel_id, channel_url, max_age_hours=MAX_AGE_HOURS):
    """Fetch videos from a channel's RSS feed published within max_age_hours."""
    rss_url = YOUTUBE_RSS_URL.format(channel_id=channel_id)
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        print(f"    No entries in feed for {channel_id}")
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=max_age_hours)
    recent = []

    for entry in feed.entries:
        published = datetime.datetime(*entry.published_parsed[:6],
                                       tzinfo=datetime.timezone.utc)
        if published >= cutoff:
            thumbnail = ''
            if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                thumbnail = entry.media_thumbnail[0].get('url', '')

            views = ''
            if hasattr(entry, 'media_statistics'):
                views = entry.media_statistics.get('views', '')

            recent.append({
                'title': entry.title,
                'link': entry.link,
                'published': published.isoformat(),
                'thumbnail': thumbnail,
                'views': views,
                'author': feed.feed.get('title', ''),
                'channel_url': channel_url,
            })

    return recent


def summarize_videos(videos):
    """Send a batch of videos to free Gemini API for Hungarian summarization."""
    import gemini_api_pool

    videos_text = ""
    for i, v in enumerate(videos):
        videos_text += f"--- VIDEÓ {i+1} ---\n"
        videos_text += f"LINK: {v['link']}\n"
        videos_text += f"CÍM: {v['title']}\n"
        videos_text += f"CSATORNA: {v['author']}\n"
        videos_text += f"MEGTEKINTÉSEK: {v['views']}\n"
        videos_text += "-------------------\n\n"

    prompt = SUMMARY_PROMPT.replace("{videos}", videos_text)
    print(f"  Summarizing {len(videos)} video(s) via Gemini API...")

    result_text, success = gemini_api_pool.generate_with_free_api(prompt)

    if not success or not result_text:
        print("  ⚠️ Gemini API call failed")
        return None

    # Parse JSON from response
    text = result_text.strip()
    # Strip markdown fences if present
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)

    try:
        items = json.loads(text)
        if isinstance(items, list):
            return items
    except json.JSONDecodeError:
        # Try to extract JSON array from response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                items = json.loads(match.group())
                if isinstance(items, list):
                    return items
            except json.JSONDecodeError:
                pass

    print(f"  ⚠️ Could not parse Gemini response as JSON")
    return None


def enrich_with_metadata(summarized_items, videos):
    """Fill in image, sourceLink matching, and processed_by fields."""
    video_map = {v['link']: v for v in videos}

    for item in summarized_items:
        source = item.get('sourceLink', '')
        video = video_map.get(source)

        item['section'] = 'szorakozas'
        item['processed_by'] = 'gemini-3.1-flash-lite'

        if video:
            if not item.get('image'):
                item['image'] = video['thumbnail']
            if not item.get('originalTitle'):
                item['originalTitle'] = video['title']
            if not item.get('author'):
                item['author'] = video['author']
        else:
            if not item.get('originalTitle'):
                item['originalTitle'] = item.get('title', '')

        if not item.get('image'):
            item['image'] = ''
        if 'importance' not in item:
            item['importance'] = 2
        if not isinstance(item.get('tags'), list):
            item['tags'] = []

    return summarized_items


def main():
    parser = argparse.ArgumentParser(description="YouTube Scraper — monitors creators for new videos")
    parser.add_argument('--hours', type=int, default=MAX_AGE_HOURS,
                        help=f"Max video age in hours (default: {MAX_AGE_HOURS})")
    parser.add_argument('--dry-run', action='store_true',
                        help="Only fetch videos, don't summarize or save")
    args = parser.parse_args()

    print(f"📺 YouTube Scraper — checking for videos from the last {args.hours}h")

    if not os.path.exists(CREATORS_FILE):
        print(f"  ⚠️ {CREATORS_FILE} not found. Nothing to do.")
        return

    with open(CREATORS_FILE, 'r', encoding='utf-8') as f:
        creator_urls = [line.strip() for line in f
                        if line.strip() and not line.startswith('#')]

    if not creator_urls:
        print("  No creators configured.")
        return

    print(f"  Found {len(creator_urls)} creator(s) to check")

    # Resolve channel IDs and fetch recent videos
    all_videos = []
    for url in creator_urls:
        channel_id = resolve_channel_id(url)
        if not channel_id:
            continue

        videos = fetch_recent_videos(channel_id, url, max_age_hours=args.hours)
        print(f"  {url}: {len(videos)} new video(s)")
        all_videos.extend(videos)

    if not all_videos:
        print("  No new videos found. Done.")
        return

    print(f"\n  📋 Total new videos: {len(all_videos)}")

    if args.dry_run:
        for v in all_videos:
            print(f"    - [{v['author']}] {v['title']} ({v['views']} views)")
        return

    # Summarize in batches
    all_summaries = []
    for batch_start in range(0, len(all_videos), BATCH_SIZE):
        batch = all_videos[batch_start:batch_start + BATCH_SIZE]
        summaries = summarize_videos(batch)
        if summaries:
            enriched = enrich_with_metadata(summaries, batch)
            all_summaries.extend(enriched)
            print(f"  ✅ Batch {batch_start // BATCH_SIZE + 1}: {len(enriched)} video(s) summarized")
        else:
            print(f"  ⚠️ Batch {batch_start // BATCH_SIZE + 1}: summarization failed")

    if not all_summaries:
        print("  ⚠️ No videos were successfully summarized.")
        return

    # Append to data.json
    data_path = os.path.join(DAILY_OUTPUT_DIR, 'data.json')
    existing = []
    if os.path.exists(data_path):
        with open(data_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)

    # Deduplicate: don't add videos already in data.json
    existing_links = {item.get('sourceLink', '') for item in existing}
    new_items = [s for s in all_summaries if s.get('sourceLink', '') not in existing_links]

    if not new_items:
        print("  All videos already in data.json. Done.")
        return

    existing.extend(new_items)
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"\n  🎉 Added {len(new_items)} YouTube video(s) to {data_path}")


if __name__ == "__main__":
    main()
