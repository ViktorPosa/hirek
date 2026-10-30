import os
import feedparser
import requests
import json
from tqdm import tqdm
import concurrent.futures
from history_manager import HistoryManager
import datetime
import argparse
from urllib.parse import urlparse
import link_filter  # Import link filtering logic


def is_valid_url(url):
    """Reject empty, non-http(s), netloc-less, or bogus (example.com) URLs."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.netloc or len(parsed.netloc) < 5:
        return False
    if 'example.com' in url.lower():
        return False
    return True

"""
LEÍRÁS:
RSS feedek letöltését végző szkript (EGYSZERŰSÍTETT).
Csak linkeket gyűjt, NEM végez AI elemzést - azt a summarizer csinálja.

BEMENET:
- RSS feed URL-ek (Input/input.txt)

KIMENET:
- Output/[Dátum]/links.txt: A nyers linklista
"""


# --- CONFIGURATION ---
INPUT_FILE = os.path.join('Input', 'input.txt')

# Use env var or default to today's date
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)
    if not os.path.exists(DAILY_OUTPUT_DIR):
        os.makedirs(DAILY_OUTPUT_DIR)

OUTPUT_FILE = os.path.join(DAILY_OUTPUT_DIR, 'links.txt')

MAX_WORKERS = 20  # Parallel feed fetching

def parse_input_file(filepath):
    """Parses the input file for Feeds."""
    config = {
        "feeds": []
    }
    
    current_section = None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if line == "FEEDS:":
                current_section = "feeds"
            elif current_section == "feeds" and line.startswith("http"):
                config["feeds"].append(line)
                
    except FileNotFoundError:
        print(f"Error: {filepath} not found.")
        exit(1)
        
    # Also load dynamically generated offline feeds if they exist
    generated_feeds_file = os.path.join(DAILY_OUTPUT_DIR, 'generated_feeds.txt')
    if os.path.exists(generated_feeds_file):
        with open(generated_feeds_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    config["feeds"].append(line)

    # Deduplicate feeds while preserving order
    seen = set()
    deduped = []
    for f in config["feeds"]:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    config["feeds"] = deduped
            
    return config

def normalize_url(url):
    """Normalizes a URL by removing trailing slashes and query parameters."""
    if url.endswith('/'):
        url = url[:-1]
    if '?' in url:
        url = url.split('?')[0]
    return url

def load_recent_links(days=3):
    """Loads all links from the last N days' data.json files for duplicate checking."""
    recent_links = set()
    today = datetime.date.today()
    base_dir = 'Output'
    
    for i in range(days):
        date = today - datetime.timedelta(days=i)
        date_str = date.strftime('%Y-%m-%d')
        data_path = os.path.join(base_dir, date_str, 'data.json')
        
        if os.path.exists(data_path):
            try:
                with open(data_path, 'r', encoding='utf-8') as f:
                    news_list = json.load(f)
                    for item in news_list:
                        link = item.get('sourceLink', '')
                        if link:
                            recent_links.add(normalize_url(link))
            except Exception as e:
                print(f"  Warning: Could not load {data_path}: {e}")
    
    return recent_links

import threading

_thread_local = threading.local()

def _get_cffi_session():
    if not hasattr(_thread_local, "session"):
        from curl_cffi import requests as cffi_requests
        _thread_local.session = cffi_requests.Session(impersonate="chrome")
    return _thread_local.session

def _fetch_via_curl(url, timeout=40):
    """Backup fetch using the system `curl` — a different network stack, a real
    browser User-Agent and a longer deadline. Recovers feeds whose servers are
    simply slow (curl_cffi's 15s read timeout expires) or that block curl_cffi.
    Returns the response bytes; raises on failure."""
    import subprocess
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
    r = subprocess.run(
        ["curl", "-sSL", "--compressed", "--max-time", str(timeout),
         "-A", ua,
         "-H", "Accept: application/rss+xml, application/xml, text/xml, */*",
         url],
        capture_output=True, timeout=timeout + 5,
    )
    if r.returncode != 0 or not r.stdout:
        detail = r.stderr.decode("utf-8", "replace")[:120].strip()
        raise RuntimeError(f"curl rc={r.returncode}: {detail}")
    return r.stdout


_NON_FEED_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".ico", ".gif", ".webp", ".css", ".js")


def _looks_like_non_feed(url):
    """True for URLs that are clearly NOT news feeds but get auto-discovered as
    such (favicons/logos, oembed endpoints, comment feeds). Fetching these just
    wastes a request and pollutes the failure list."""
    u = url.split("?")[0].lower()
    if u.endswith(_NON_FEED_SUFFIXES):
        return True
    if "/wp-json/" in u or "oembed" in u:
        return True
    if u.rstrip("/").endswith("/comments/feed"):
        return True
    return False


def fetch_feed_items(url, read_timeout=15, curl_fallback=False):
    """Fetches and parses a single RSS feed.

    read_timeout   — curl_cffi read timeout in seconds (connect stays 10s).
    curl_fallback  — if the curl_cffi fetch fails, retry once via the system
                     `curl` with a longer deadline. Used by the escalating retry
                     pass to recover slow / bot-blocking servers.
    """
    try:
        if url.startswith("http"):
            if _looks_like_non_feed(url):
                # Not a real feed (favicon/oembed/comments) — skip silently so it
                # isn't counted as a failure or routed to the fallback.
                return [], None
            content = None
            try:
                session = _get_cffi_session()
                # curl_cffi impersonates Chrome's TLS fingerprint to bypass anti-bot.
                resp = session.get(url, timeout=(10, read_timeout))
                resp.raise_for_status()
                content = resp.content
            except Exception:
                if not curl_fallback:
                    raise
                # Backup path: plain system curl, longer deadline, browser UA.
                content = _fetch_via_curl(url, timeout=max(read_timeout, 40))
            feed = feedparser.parse(content)
        else:
            # It's a local file path
            with open(url, 'rb') as f:
                feed = feedparser.parse(f.read())
             
        items = []
        for entry in feed.entries[:15]:  # Limit to top 15 per feed
            link = entry.get('link', '')
            if link and not is_valid_url(link):
                print(f"   ⏭️ Skipping invalid URL: {link[:60]}")
                continue
            if link:
                # Extract categories/tags
                categories = []
                if 'tags' in entry:
                    for tag in entry.tags:
                        if 'term' in tag and tag.term:
                            categories.append(tag.term)
                elif 'category' in entry and entry.category:
                    categories.append(entry.category)
                
                content = ''
                if 'content' in entry:
                    content = entry.content[0].value
                elif 'description' in entry:
                    content = entry.description
                    
                title = entry.get('title', '')
                items.append({'url': link, 'categories': categories, 'title': title, 'content': content})
        return items, None
    except Exception as e:
        return [], f"Error fetching {url}: {e}"

from google_rss_resolver import resolve_google_news_urls_batch
from urllib.parse import urlparse

def _feed_url_to_homepage(feed_url):
    """Extract homepage base URL from a feed URL for rss_creator fallback."""
    parsed = urlparse(feed_url)
    return f"{parsed.scheme}://{parsed.netloc}"

def _fetch_and_collect(feeds, raw_items_out, errors_out, label="", read_timeout=15, curl_fallback=False):
    """Fetch a list of feeds in parallel, appending results to raw_items_out and errors_out.
    Returns list of feed URLs that failed.

    read_timeout / curl_fallback are forwarded to fetch_feed_items so the retry
    pass can escalate to a longer timeout + system-curl backup."""
    if not feeds:
        return []
    failed_feeds = []
    prefix = f"[{label}] " if label else ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_feed_items, url, read_timeout, curl_fallback): url for url in feeds}
        results_iter = tqdm(
            concurrent.futures.as_completed(futures),
            total=len(feeds),
            unit="feed",
            desc=f"{prefix}Fetching"
        )
        for future in results_iter:
            url = futures[future]
            items, error = future.result()
            if error:
                tqdm.write(error)
                errors_out.append(error)
                failed_feeds.append(url)
            raw_items_out.extend(items)
    return failed_feeds


def _process_raw_items(raw_items, history, rss_meta, recent_links, existing_links_set):
    """
    Process raw RSS items: resolve Google URLs, filter, deduplicate.
    Returns (new_links, updated_rss_meta) — only truly new links not in existing_links_set.
    """
    if not raw_items:
        return [], rss_meta

    all_links_raw = [item['url'] for item in raw_items]

    # --- GOOGLE RSS RESOLVER ---
    error_log_path = os.path.join(DAILY_OUTPUT_DIR, 'pipeline_errors.log')
    resolved_links = resolve_google_news_urls_batch(
        all_links_raw,
        max_workers=20,
        show_progress=True,
        error_log_path=error_log_path
    )

    filtered_links = []
    for i, resolved_url in enumerate(resolved_links):
        meta_cats = raw_items[i].get('categories', [])
        meta_title = raw_items[i].get('title', '')
        meta_content = raw_items[i].get('content', '')

        clean = normalize_url(resolved_url)

        # Update rss_meta
        if clean in rss_meta:
            if isinstance(rss_meta[clean], list):
                existing_cats = set(rss_meta[clean])
                rss_meta[clean] = {'categories': list(existing_cats), 'title': meta_title, 'content': meta_content}
            else:
                existing_cats = set(rss_meta[clean].get('categories', []))
                existing_cats.update(meta_cats)
                rss_meta[clean]['categories'] = list(existing_cats)
                if not rss_meta[clean].get('title') and meta_title:
                    rss_meta[clean]['title'] = meta_title
                if not rss_meta[clean].get('content') and meta_content:
                    rss_meta[clean]['content'] = meta_content
        elif meta_cats or meta_title or meta_content:
            rss_meta[clean] = {
                'categories': list(set(meta_cats)),
                'title': meta_title,
                'content': meta_content
            }

        # Link filter
        is_bad, keyword = link_filter.is_blacklisted(resolved_url)
        if is_bad:
            print(f"  🛑 [FILTERED] '{keyword}' in: {resolved_url[:80]}...")
            history.mark_filtered(resolved_url, "link_filter", f"URL contains blacklisted keyword: {keyword}")
            continue

        filtered_links.append(resolved_url)

    # Deduplicate within batch
    unique_batch = list(set(filtered_links))

    # Filter against recent data.json
    new_links = []
    for link in unique_batch:
        norm = normalize_url(link)
        if norm in recent_links:
            continue
        if history.is_known(link):
            continue
        if link in existing_links_set:
            continue
        new_links.append(link)

    return new_links, rss_meta


def _save_outputs(all_final_links, rss_meta):
    """Save links.txt and rss_meta.json."""
    # Save RSS Metadata
    rss_meta_path = os.path.join(DAILY_OUTPUT_DIR, 'rss_meta.json')
    try:
        with open(rss_meta_path, 'w', encoding='utf-8') as f:
            json.dump(rss_meta, f, ensure_ascii=False, indent=2)
        print(f"Saved RSS metadata for {len(rss_meta)} URLs to {rss_meta_path}")
    except Exception as e:
        print(f"Error saving rss_meta.json: {e}")

    # Save links
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_final_links))
        print(f"\n✅ Success! Collected {len(all_final_links)} links.")
        print(f"Results saved to {OUTPUT_FILE}")
    except Exception as e:
        print(f"Error writing output file: {e}")


def main():
    parser = argparse.ArgumentParser(description="Collect RSS feed links")
    parser.add_argument("--parallel-rss", action="store_true",
                        help="Streaming mode: process built-in feeds immediately, "
                             "then poll generated_feeds.txt for new feeds from rss_creator")
    args = parser.parse_args()

    print(f"📰 RSS Link Collector")

    if args.parallel_rss:
        main_parallel_rss()
    else:
        main_sequential()


def main_sequential():
    """Original sequential mode — waits for all feeds (including generated) to exist."""
    print(f"Reading {INPUT_FILE}...")
    config = parse_input_file(INPUT_FILE)

    if not config["feeds"]:
        print("Error: No feeds found in input.txt")
        return

    print(f"Found {len(config['feeds'])} feeds. Fetching...")

    raw_items = []
    errors = []

    # Initialize History early for logging filters
    history = HistoryManager()

    # Fetch feeds in parallel
    failed_feeds = _fetch_and_collect(config['feeds'], raw_items, errors)

    print(f"Initial raw links parsed: {len(raw_items)}")

    # Retry failed feeds after 30s
    if failed_feeds:
        import time as _time
        print(f"\n♻️ Retrying {len(failed_feeds)} failed feeds in 30s...")
        _time.sleep(30)
        retry_errors = []
        still_failed = _fetch_and_collect(failed_feeds, raw_items, retry_errors, label="Retry",
                                          read_timeout=35, curl_fallback=True)
        errors.extend(retry_errors)

        # Drop initial-pass errors for feeds the backup recovered, so they aren't
        # reported as failures (they succeeded on the escalated retry).
        recovered = set(failed_feeds) - set(still_failed)
        if recovered:
            errors[:] = [e for e in errors if not any(f"Error fetching {u}:" in e for u in recovered)]
            print(f"  ✅ Backup recovered {len(recovered)} feed(s) — excluded from failure log.")

        if still_failed:
            homepages = list(set(_feed_url_to_homepage(u) for u in still_failed))
            fallback_path = os.path.join(DAILY_OUTPUT_DIR, 'failed_feeds_for_rss_creator.txt')
            try:
                with open(fallback_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(homepages) + '\n')
                print(f"⚠️ {len(still_failed)} feeds still failing → wrote {len(homepages)} homepages to {fallback_path}")
            except Exception as e:
                print(f"⚠️ Could not write fallback file: {e}")
        else:
            print(f"✅ Retry recovered all {len(failed_feeds)} failed feeds")

    # Save fetch errors to log file
    if errors:
        error_log_path = os.path.join(DAILY_OUTPUT_DIR, 'pipeline_errors.log')
        try:
            with open(error_log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n--- RSS Fetch Errors at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                f.write('\n'.join(errors) + '\n')
            print(f"Logged {len(errors)} fetch errors to {error_log_path}")
        except Exception as e:
            print(f"Could not save error log: {e}")

    # Load dedup sources
    print("Loading recent data.json links for duplicate check...")
    recent_links = load_recent_links(days=3)
    print(f"  Found {len(recent_links)} links in recent data.json files")

    # Load existing output for resume
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                existing_links = set(line.strip() for line in f if line.strip())
            print(f"Loaded {len(existing_links)} existing links from links.txt for resume")
        except Exception as e:
            print(f"Warning: Could not load existing output: {e}")

    rss_meta = {}
    new_links, rss_meta = _process_raw_items(raw_items, history, rss_meta, recent_links, existing_links)

    # Merge
    final_links = list(existing_links) + new_links
    print(f"New links added: {len(new_links)}")
    print(f"Total links in output: {len(final_links)}")

    _save_outputs(final_links, rss_meta)


def main_parallel_rss():
    """
    Parallel/streaming mode — bucket pattern:
    1. Immediately process built-in feeds from input.txt
    2. Poll generated_feeds.txt for new feeds from rss_creator
    3. Process each new batch as it appears
    4. Finish when rss_creator signals completion (via signal file)
    """
    import time as _time

    GENERATED_FEEDS_FILE = os.path.join(DAILY_OUTPUT_DIR, 'generated_feeds.txt')
    SIGNAL_FILE = os.path.join(DAILY_OUTPUT_DIR, 'rss_creator_done.signal')
    POLL_INTERVAL = 3  # seconds between checks for new feeds

    print(f"🔄 Parallel RSS mode: processing built-in feeds while rss_creator runs...")

    # --- Phase 1: Process built-in feeds immediately ---
    print(f"\n📡 Phase 1: Processing built-in feeds from {INPUT_FILE}...")
    builtin_feeds = []
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        current_section = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line == "FEEDS:":
                current_section = "feeds"
            elif current_section == "feeds" and line.startswith("http"):
                builtin_feeds.append(line)
    except FileNotFoundError:
        print(f"Error: {INPUT_FILE} not found.")

    history = HistoryManager()

    print("Loading recent data.json links for duplicate check...")
    recent_links = load_recent_links(days=3)
    print(f"  Found {len(recent_links)} links in recent data.json files")

    # Load existing output for resume
    existing_links_set = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                existing_links_set = set(line.strip() for line in f if line.strip())
            print(f"Loaded {len(existing_links_set)} existing links from links.txt for resume")
        except Exception as e:
            print(f"Warning: Could not load existing output: {e}")

    rss_meta = {}
    all_final_links = list(existing_links_set)
    all_errors = []
    total_new = 0

    # Fetch built-in feeds
    failed_feeds = []
    if builtin_feeds:
        print(f"  Found {len(builtin_feeds)} built-in feeds. Fetching...")
        raw_items = []
        failed_feeds = _fetch_and_collect(builtin_feeds, raw_items, all_errors, label="Built-in")
        print(f"  Built-in feeds: {len(raw_items)} raw items parsed")

        new_links, rss_meta = _process_raw_items(raw_items, history, rss_meta, recent_links, existing_links_set)
        for link in new_links:
            if link not in existing_links_set:
                all_final_links.append(link)
                existing_links_set.add(link)
                total_new += 1
        print(f"  ✅ Built-in feeds: {len(new_links)} new links added")

        # --- Retry failed feeds after 30s ---
        if failed_feeds:
            print(f"\n  ♻️ Retrying {len(failed_feeds)} failed feeds in 30s...")
            _time.sleep(30)
            retry_raw = []
            retry_errors = []
            still_failed = _fetch_and_collect(failed_feeds, retry_raw, retry_errors, label="Retry",
                                              read_timeout=35, curl_fallback=True)

            # Drop initial-pass errors for feeds the backup recovered, so they
            # aren't reported as failures (they succeeded on the escalated retry).
            recovered = set(failed_feeds) - set(still_failed)
            if recovered:
                all_errors[:] = [e for e in all_errors if not any(f"Error fetching {u}:" in e for u in recovered)]
                print(f"  ✅ Backup recovered {len(recovered)} feed(s) — excluded from failure log.")

            if retry_raw:
                print(f"  Retry recovered {len(retry_raw)} raw items from {len(failed_feeds) - len(still_failed)} feeds")
                retry_links, rss_meta = _process_raw_items(retry_raw, history, rss_meta, recent_links, existing_links_set)
                for link in retry_links:
                    if link not in existing_links_set:
                        all_final_links.append(link)
                        existing_links_set.add(link)
                        total_new += 1
                print(f"  ✅ Retry: {len(retry_links)} new links added")
            else:
                print(f"  Retry: no new items recovered")

            # --- Pass still-failing feeds to rss_creator ---
            if still_failed:
                homepages = list(set(_feed_url_to_homepage(u) for u in still_failed))
                fallback_path = os.path.join(DAILY_OUTPUT_DIR, 'failed_feeds_for_rss_creator.txt')
                try:
                    with open(fallback_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(homepages) + '\n')
                    print(f"  ⚠️ {len(still_failed)} feeds still failing → wrote {len(homepages)} homepages to {fallback_path}")
                except Exception as e:
                    print(f"  ⚠️ Could not write fallback file: {e}")

        # Save intermediate results so downstream steps could start early if needed
        _save_outputs(all_final_links, rss_meta)
    else:
        print("  No built-in feeds found.")

    # Tell rss_creator we've finished deciding which built-in feeds failed, so it
    # can promptly consume failed_feeds_for_rss_creator.txt (or skip if none) —
    # instead of blindly waiting. The fallback file (if any) is already written.
    try:
        open(os.path.join(DAILY_OUTPUT_DIR, 'failed_feeds_ready.signal'), 'w').close()
    except Exception as e:
        print(f"  ⚠️ Could not write failed-feeds ready signal: {e}")

    # --- Phase 2: Poll for generated feeds from rss_creator ---
    print(f"\n🪣 Phase 2: Polling for generated feeds (bucket mode)...")
    processed_feeds = set(builtin_feeds)
    poll_count = 0
    max_idle_polls = 200  # safety cap: ~10 minutes of polling with 3s interval

    while True:
        # Check for new generated feeds
        new_feeds = []
        if os.path.exists(GENERATED_FEEDS_FILE):
            try:
                with open(GENERATED_FEEDS_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        feed_path = line.strip()
                        if feed_path and feed_path not in processed_feeds:
                            new_feeds.append(feed_path)
            except Exception as e:
                print(f"  ⚠️ Error reading generated_feeds.txt: {e}")

        if new_feeds:
            poll_count = 0  # reset idle counter
            print(f"  🪣 Found {len(new_feeds)} new generated feed(s) in bucket!")
            for f in new_feeds:
                processed_feeds.add(f)

            # Fetch and process the new feeds
            raw_items = []
            batch_errors = []
            _fetch_and_collect(new_feeds, raw_items, batch_errors, label="Generated")
            all_errors.extend(batch_errors)

            if raw_items:
                print(f"  Processing {len(raw_items)} items from generated feeds...")
                new_links, rss_meta = _process_raw_items(
                    raw_items, history, rss_meta, recent_links, existing_links_set
                )
                batch_new = 0
                for link in new_links:
                    if link not in existing_links_set:
                        all_final_links.append(link)
                        existing_links_set.add(link)
                        batch_new += 1
                        total_new += 1
                print(f"  ✅ Batch: {batch_new} new links added (total new: {total_new})")

                # Save intermediate results after each batch
                _save_outputs(all_final_links, rss_meta)

        # Check if rss_creator is done
        creator_done = os.path.exists(SIGNAL_FILE)

        if creator_done:
            # Do one final sweep to catch any last feeds
            final_feeds = []
            if os.path.exists(GENERATED_FEEDS_FILE):
                try:
                    with open(GENERATED_FEEDS_FILE, 'r', encoding='utf-8') as f:
                        for line in f:
                            feed_path = line.strip()
                            if feed_path and feed_path not in processed_feeds:
                                final_feeds.append(feed_path)
                except Exception:
                    pass

            if final_feeds:
                print(f"  🪣 Final sweep: {len(final_feeds)} remaining feed(s)")
                for fp in final_feeds:
                    processed_feeds.add(fp)
                raw_items = []
                batch_errors = []
                _fetch_and_collect(final_feeds, raw_items, batch_errors, label="Final")
                all_errors.extend(batch_errors)
                if raw_items:
                    new_links, rss_meta = _process_raw_items(
                        raw_items, history, rss_meta, recent_links, existing_links_set
                    )
                    for link in new_links:
                        if link not in existing_links_set:
                            all_final_links.append(link)
                            existing_links_set.add(link)
                            total_new += 1

            print(f"\n✅ RSS Creator finished. Processed {len(processed_feeds)} generated feeds total.")
            break

        # Not done yet — wait and poll again
        poll_count += 1
        if poll_count >= max_idle_polls:
            print(f"  ⚠️ Max polling time exceeded ({max_idle_polls * POLL_INTERVAL}s). "
                  f"Finishing with {len(processed_feeds)} generated feeds processed.")
            break

        _time.sleep(POLL_INTERVAL)

    # Save fetch errors
    if all_errors:
        error_log_path = os.path.join(DAILY_OUTPUT_DIR, 'pipeline_errors.log')
        try:
            with open(error_log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n--- RSS Fetch Errors at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                f.write('\n'.join(all_errors) + '\n')
            print(f"Logged {len(all_errors)} fetch errors to {error_log_path}")
        except Exception as e:
            print(f"Could not save error log: {e}")

    # Final save
    print(f"\n📊 Final results: {len(all_final_links)} total links ({total_new} new)")
    _save_outputs(all_final_links, rss_meta)

    # Clean up signal file
    try:
        if os.path.exists(SIGNAL_FILE):
            os.remove(SIGNAL_FILE)
    except Exception:
        pass

if __name__ == "__main__":
    main()
