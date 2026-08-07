import os
from urllib.parse import urlparse, urlunparse
# Fix for "malloc: *** error for object ... pointer being freed was not allocated"
os.environ['GRPC_ENABLE_FORK_SUPPORT'] = '0'
os.environ['GRPC_POLL_STRATEGY'] = 'poll'

import requests
import json
import re
import base64
from typing import Tuple, List, Any, Dict, Optional
from tqdm import tqdm
import time
import datetime
import concurrent.futures
import threading
import queue
from history_manager import HistoryManager
import argparse
import gemini_api_client as gemini_client
import geminipro_client
import gemini_chat_client
import perplexity_client
import g4f_client
import deeperseek_client
import backend_orchestrator
import subprocess
import article_downloader
import pipeline_menu
import gemini_api_pool
from pipeline_logger import log_pipeline_error

# Debug Globals
DEBUG_PAIRING_MODE = False
DEBUG_CONTEXT = threading.local()

# Timeout wrapper for backend calls
BACKEND_TIMEOUT = 180  # 3 minutes max per backend call
SELENIUM_TIMEOUT = 540  # 9 minutes for selenium — must be > gemini_selenium_client.RESPONSE_WAIT_TIMEOUT (480s)

_UTM_PARAMS = frozenset([
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_referrer", "fbclid", "gclid", "msclkid", "yclid",
    "ref", "referer", "source", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
])

def normalize_url(u):
    """
    Normalize URL for robust matching:
    - Strip scheme (http/https) for protocol-agnostic comparison
    - Strip www.
    - Strip trailing slash
    - Strip fragments (#...)
    - Strip UTM and tracking query params
    """
    if not u: return ""
    u = u.strip().replace(" ", "")

    try:
        p = urlparse(u)
        netloc = p.netloc.lower()

        # Strip www.
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = (p.path or "").rstrip("/")

        # Strip tracking-only query params; keep meaningful ones
        from urllib.parse import parse_qs, urlencode
        qs = parse_qs(p.query, keep_blank_values=True)
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in _UTM_PARAMS}
        clean_query = urlencode(clean_qs, doseq=True)

        # scheme removal (empty string 1st arg) + fragment removal (empty string last arg)
        return urlunparse(("", netloc, path, p.params, clean_query, ""))
    except Exception:
        return u.rstrip("/").lower()

def call_with_timeout(func, timeout=BACKEND_TIMEOUT):
    """
    Call a function with timeout protection.
    Returns (result, success) tuple.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func)
    try:
        result = future.result(timeout=timeout)
        executor.shutdown(wait=False)
        return result, True
    except concurrent.futures.TimeoutError:
        print(f"      ⏰ TIMEOUT ({timeout}s) - backend call took too long")
        log_pipeline_error(f"Summarizer: TIMEOUT ({timeout}s) - backend call took too long")
        future.cancel()
        executor.shutdown(wait=False)
        return (None, None), False
    except Exception as e:
        print(f"      ❌ Backend call error: {e}")
        log_pipeline_error(f"Summarizer: Backend call error: {e}")
        future.cancel()
        executor.shutdown(wait=False)
        return (None, None), False


"""
LEÍRÁS:
A kategorizált hírek tartalmának letöltése, összefoglalása és strukturált JSON formátumba mentése.
Többféle AI motort (Gemini, Perplexity, DeeperSeek, G4F) támogat, hibatűréssel és újrapróbálkozással.
Képeket tölt le és kezel, valamint frissíti a 'fooldal' szekciót.

BEMENET:
- Output/[Dátum]/[kategoria].txt fájlok (linkek listája)
- Cikk tartalmak (article_downloader által letöltve)
- Prompt sablon (Input/summarize.txt)

KIMENET:
- Output/[Dátum]/data.json: A végleges, összefoglalt hírek adatbázisa
- Letöltött képek (Output/[Dátum]/Images mappa)
- Frissített HistoryManager (összefoglalt státusz)
"""

# --- CONFIGURATION ---

def pair_news_to_batch(news_list, batch, backend_name="Unknown", link_manager=None):
    """
    Robustly pairs LLM-generated news items with original batch items.
    strategy:
    1. Exact URL match
    2. Normalized URL match (scheme/www/slash/UTM agnostic)
    3. Fuzzy match (substring) if len > 10
    4. Positional fallback: if output count == batch count, map by index

    If no match found:
    - KEEPS item (Falback)
    - Sets pairingFailed=True
    - Logs warning
    """
    # Create lookup maps
    batch_map = {}
    batch_urls = []
    for item in batch:
        raw = item.get('url', '').strip()
        norm = normalize_url(raw)
        if raw:
            batch_map[raw] = item
            batch_map[raw.rstrip("/")] = item
        if norm:
            batch_map[norm] = item
        batch_urls.append((raw, norm, item))
    
    final_news_list = []
    # Track which batch items were already claimed by positional fallback
    positional_used = set()

    for news_idx, news_item in enumerate(news_list):
        llm_link = news_item.get('sourceLink', '').strip()
        norm_llm = normalize_url(llm_link)

        # 1. Exact & Normalized Match
        original_item = batch_map.get(llm_link) or batch_map.get(norm_llm)

        # 2. Relaxed Substring Match
        if not original_item and norm_llm and len(norm_llm) >= 10:
            for b_url, b_norm, b_item in batch_urls:
                if not b_norm: continue
                if (norm_llm in b_norm) or (b_norm in norm_llm):
                    original_item = b_item
                    break

        # 3. Positional Fallback: if output count == batch count, assume position → position
        if not original_item and len(news_list) == len(batch) and news_idx < len(batch_urls):
            _, _, pos_item = batch_urls[news_idx]
            pos_url = pos_item.get('url', '')
            if pos_url and news_idx not in positional_used:
                original_item = pos_item
                positional_used.add(news_idx)
                print(f"      [{backend_name}] ℹ️ POSITIONAL MATCH used for item {news_idx+1}: '{llm_link}' → '{pos_url}'")

        if original_item:
            # Found match! Enforce strict URL from our system
            news_item['sourceLink'] = original_item['url']
            news_item['originalTitle'] = original_item.get('originalTitle', '')
            inject_rss_categories(news_item, original_item['url'])
            clean_generic_tags(news_item)
            inject_domain_tag(news_item, original_item['url'])
            final_news_list.append(news_item)
            if link_manager:
                link_manager.report_pairing_success()
        else:
            msg = f"[{backend_name}] ⚠️ PAIRING FAIL: '{llm_link}' not found. KEEPING ITEM (Fallback)."
            print(f"      {msg}")
            log_pipeline_error(f"Summarizer: {msg}")
            
            # DEBUG MODE FAIL-STOP
            if DEBUG_PAIRING_MODE:
                print(f"      [DEBUG] Pairing Failed in Debug Mode! Dumping context and stopping...")
                try:
                    debug_file = os.path.join(OUTPUT_DIR, "debug_pairing.txt")
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(f"PART 1: FULL PROMPT\n{'='*50}\n")
                        f.write((getattr(DEBUG_CONTEXT, 'prompt', None) or "NO PROMPT CAPTURED") + "\n\n")
                        f.write(f"PART 2: RAW RESPONSE\n{'='*50}\n")
                        f.write((str(getattr(DEBUG_CONTEXT, 'response', None)) or "NO RESPONSE") + "\n\n")
                        f.write(f"PART 3: PAIRING ERROR CONTEXT\n{'='*50}\n")
                        f.write(f"Failed Link from LLM: {llm_link}\n")
                        f.write(f"Parsed Item JSON: {json.dumps(news_item, ensure_ascii=False, indent=2)}\n")
                        f.write(f"Available Batch Links ({len(batch_urls)}):\n")
                        for b_url, _, _ in batch_urls:
                            f.write(f" - {b_url}\n")
                    print(f"      [DEBUG] Saved debug info to {debug_file}")
                except Exception as e:
                    print(f"      [DEBUG] Failed to save debug info: {e}")
                
                print("      [DEBUG] Stopping pipeline execution immediately.")
                os._exit(1) # Hard exit to stop everything

            print(f"      [{backend_name}] 🔍 Available Batch URLs (Normalized):")
            for b_url, b_norm, _ in batch_urls:
                 print(f"          - {b_url} ({b_norm[:30]}...)")
            
            # FALLBACK: Keep the item with the LLM's link
            news_item['originalTitle'] = 'Unknown - Pairing Failed'
            news_item['pairingFailed'] = True
            final_news_list.append(news_item)
            # We still report failure for stats tracking
            if link_manager:
                link_manager.report_pairing_failure(llm_link)
            
            # DEBUG: Log full failure context to file
            try:
                # Use global OUTPUT_DIR
                debug_file = os.path.join(OUTPUT_DIR, "debug_pairing_failures.log")
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                with open(debug_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[{timestamp}] [{backend_name}] PAIRING FAIL (Kept as fallback)\n")
                    f.write(f"LLM Link: '{llm_link}'\n")
                    f.write(f"LLM Response Item: {json.dumps(news_item, ensure_ascii=False)}\n")
                    f.write(f"Available Batch Links ({len(batch_urls)}):\n")
                    for b_url, b_norm, _ in batch_urls:
                            f.write(f" - {b_url}\n")
                    f.write("-" * 50 + "\n")
                print(f"      [DEBUG] Logged failure context to {debug_file}")
            except Exception as e:
                print(f"      ⚠️ Failed to write debug log: {e}")

    return final_news_list

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR

# Gemini API is used as default
BATCH_SIZE = 10
MAX_RETRIES = 3
MAX_WORKERS = 3  # Parallel processing threads (reduced for API rate limits)

# --- RSS META CACHE ---
RSS_META_CACHE = None

def get_rss_categories(url):
    """Retrieves RSS categories for a given URL from the cache."""
    global RSS_META_CACHE
    if RSS_META_CACHE is None:
        rss_path = os.path.join(DAILY_OUTPUT_DIR, 'rss_meta.json')
        if os.path.exists(rss_path):
            try:
                with open(rss_path, 'r', encoding='utf-8') as f:
                    RSS_META_CACHE = json.load(f)
                print(f"Loaded RSS Metadata cache: {len(RSS_META_CACHE)} entries")
            except Exception as e:
                print(f"Failed to load rss_meta.json: {e}")
                log_pipeline_error(f"Summarizer: Failed to load rss_meta.json: {e}")
                RSS_META_CACHE = {}
        else:
            RSS_META_CACHE = {}
            
    # Normalize URL for lookup
    from urllib.parse import urlparse
    def norm(u):
        if not u: return ""
        u = u.strip()
        if u.endswith('/'): u = u[:-1]
        return u
        
    url_norm = norm(url)
    
    # Try exact match
    meta = None
    if url_norm in RSS_META_CACHE:
        meta = RSS_META_CACHE[url_norm]
    
    if meta is not None:
        if isinstance(meta, dict):
            return meta.get('categories', [])
        elif isinstance(meta, list):
            # Backwards compatibility
            return meta
            
    return []

def get_rss_title(url):
    """Retrieves original RSS title if available."""
    # Ensure cache is loaded
    get_rss_categories(url)
    
    global RSS_META_CACHE
    if not RSS_META_CACHE: return ""
    
    from urllib.parse import urlparse
    def norm(u):
        if not u: return ""
        u = u.strip()
        if u.endswith('/'): u = u[:-1]
        return u
        
    url_norm = norm(url)
    meta = RSS_META_CACHE.get(url_norm)
    if isinstance(meta, dict):
        return meta.get('title', '')
    return ""

def get_rss_content(url):
    """Retrieves full RSS HTML content if available (useful fallback for 403 blocks)."""
    get_rss_categories(url)
    
    global RSS_META_CACHE
    if not RSS_META_CACHE: return ""
    
    from urllib.parse import urlparse
    def norm(u):
        if not u: return ""
        u = u.strip()
        if u.endswith('/'): u = u[:-1]
        return u
        
    url_norm = norm(url)
    meta = RSS_META_CACHE.get(url_norm)
    if isinstance(meta, dict):
        return meta.get('content', '')
    return ""

def inject_rss_categories(news_item, url):
    """Prepends RSS categories to the news item's tags."""
    rss_cats = get_rss_categories(url)
    if rss_cats:
        current_tags = news_item.get('tags', [])
        # Normalize current tags to avoid duplicates (case insensitive check?)
        # current_tags are already lowercased in validate_news_item
        
        # Add RSS categories (prepend)
        # We perform a simple check to avoid exact duplicates
        curr_set = set(current_tags)
        to_add = []
        for cat in rss_cats:
            cat_clean = cat.lower().strip()
            if cat_clean not in curr_set:
                to_add.append(cat_clean)
                curr_set.add(cat_clean)
        
        if to_add:
            # Prepend new tags
            news_item['tags'] = to_add + current_tags
            # print(f"DEBUG: Injected tags {to_add} for {url}")

def inject_domain_tag(news_item, url):
    """Extracts the SLD (Second Level Domain) from the URL and prepends it to tags."""
    if not url: return
    
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain: return
        
        # Remove www.
        if domain.startswith('www.'):
            domain = domain[4:]
            
        # Extract SLD (e.g. 'telex.hu' -> 'telex', 'ign.com' -> 'ign')
        parts = domain.split('.')
        if len(parts) >= 2:
            # Heuristic: Take the part before the last part (TLD)
            # Exception: co.uk, com.au etc? For simple 'telex.hu', parts[0] is fine.
            # But for 'hu.ign.com' -> 'ign'? 
            # Let's take the SLD logic: usually the part before TLD.
            # Simple approach: split by dot, take the part before the TLD if 2 parts.
            # If 3 parts (hu.ign.com), take middle?
            # Better specific logic:
            # 1. 'telex.hu' -> 'telex'
            # 2. 'ign.com' -> 'ign'
            # 3. 'hu.ign.com' -> 'ign'
            # 4. 'index.hu' -> 'index'
            # 5. '444.hu' -> '444'
            # 6. 'neocoregames.com' -> 'neocoregames'
            
            # General heuristic: keys of interest are usually the 'brand' name.
            # If multiple subdomains, usually the TLD-1 is the brand.
            # e.g. hu.ign.com -> ign
            # e.g. maps.google.com -> google
            
            # Simple fallback: take parts[-2]
            sld = parts[-2]
            
            # Special case for 'hu.ign.com' -> parts[-2] is 'ign'. Correct.
            # Special case for 'co.uk'? 'bbc.co.uk' -> 'co'? Incorrect.
            # But for the listed feeds (mostly HU or .com), parts[-2] is usually safe.
            # Let's verify commonly known TLDs.
            
            common_multipart_tlds = {'co.uk', 'com.au', 'gov.uk'}
            if '.'.join(parts[-2:]) in common_multipart_tlds:
                 if len(parts) >= 3:
                     sld = parts[-3]
            
            tag = sld.lower()
            
            # Prepend uniqueness check
            current_tags = news_item.get('tags', [])
            if tag not in [t.lower() for t in current_tags]:
                news_item['tags'] = [tag] + current_tags
                # print(f"DEBUG: Injected domain tag '{tag}' for {url}")
                
    except Exception as e:
        print(f"Error extracting domain from {url}: {e}")
        log_pipeline_error(f"Summarizer: Error extracting domain from {url}: {e}")

# List of generic tags to remove
GENERIC_TAGS = {
    "hírek", "hír", "news", "friss hírek", "latest news", "breaking news", 
    "nap hírei", "mai hírek", "belföld", "külföld", "tech hírek", "sport hírek",
    "top news", "cimlap", "címlap", "aktualitasok", "aktualitások", "kiemelt",
    "feature", "featured", "cikkek", "articles", "blog", "posts"
}

def clean_generic_tags(news_item):
    """Removes generic tags from the news item."""
    if 'tags' not in news_item:
        return
        
    cleaned_tags = []
    seen = set()
    
    for tag in news_item['tags']:
        tag_lower = tag.lower().strip()
        if tag_lower not in GENERIC_TAGS and tag_lower not in seen:
            cleaned_tags.append(tag)
            seen.add(tag_lower)
            
    news_item['tags'] = cleaned_tags

# Valid section codes
VALID_SECTIONS = ['fooldal', 'tech', 'tudomany', 'belfold_kulfold', 'uzlet', 'szorakozas', 'eletmod', 'bulvar', 'sport', 'kripto', 'gamer']

# Required fields for each news item
REQUIRED_FIELDS = ['section', 'title', 'content', 'tags', 'image', 'sourceLink', 'author', 'importance']

# --- PRODUCER-CONSUMER ARCHITECTURE ---

class ArticleBuffer:
    """
    Thread-safe buffer for downloaded articles.
    Producer: DownloadWorkers push downloaded articles
    Consumer: SummarizerWorkers pull batches for processing
    """
    
    def __init__(self, max_size=500):
        self._queue = queue.Queue(maxsize=max_size)
        self._lock = threading.Lock()
        self._downloads_complete = threading.Event()
        self._total_downloaded = 0
        self._total_failed = 0
    
    def put(self, article_data):
        """Add a downloaded article to the buffer."""
        self._queue.put(article_data)
        with self._lock:
            self._total_downloaded += 1
    
    def get_batch(self, size=5, timeout=5):
        """
        Get a batch of articles for summarization.
        Returns list of articles (may be smaller than requested size).
        Returns empty list if timeout expires and queue is empty.
        """
        batch = []
        deadline = time.time() + timeout
        
        while len(batch) < size and time.time() < deadline:
            remaining_time = max(0.1, deadline - time.time())
            try:
                article = self._queue.get(timeout=min(1.0, remaining_time))
                batch.append(article)
                # self._queue.task_done() # Moved to explicit call by consumer
            except queue.Empty:
                # Check if downloads are complete
                if self._downloads_complete.is_set() and self._queue.empty():
                    break
                # Otherwise keep waiting
                continue
        
        return batch
    
    def mark_download_complete(self):
        """Signal that all downloads are finished."""
        self._downloads_complete.set()
    
    def is_complete(self):
        """Check if downloads are complete AND buffer is empty."""
        return self._downloads_complete.is_set() and self._queue.empty()
    
    def has_pending(self):
        """Check if there are pending items or downloads still running."""
        return not self._queue.empty() or not self._downloads_complete.is_set()
    
    def report_failure(self):
        """Increment failed download counter."""
        with self._lock:
            self._total_failed += 1
    
    def get_stats(self):
        """Get download statistics."""
        with self._lock:
            return {
                'downloaded': self._total_downloaded,
                'failed': self._total_failed,
                'pending': self._queue.qsize()
            }
    
    def task_done(self, n=1):
        """Mark n tasks as done."""
        for _ in range(n):
            self._queue.task_done()


class DownloadWorker(threading.Thread):
    """
    Worker thread for downloading articles.
    Pulls URLs from download_queue, downloads content, pushes to article_buffer.
    """
    
    def __init__(self, worker_id, download_queue, article_buffer, history, result_manager):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.download_queue = download_queue
        self.article_buffer = article_buffer
        self.history = history
        self.result_manager = result_manager
        self.name = f"DownloadWorker-{worker_id}"
    
    def run(self):
        while True:
            try:
                # Get URL from queue (blocks until available or timeout)
                try:
                    url = self.download_queue.get(timeout=2)
                except queue.Empty:
                    # Check if we should exit (no more work)
                    continue
                
                if url is None:  # Poison pill to stop worker
                    self.download_queue.task_done()
                    break
                
                # Skip URLs already processed in historical data (cross-day dedup)
                if self.result_manager.is_url_already_processed(url):
                    print(f"      [{self.name}] SKIP (already in historical data): {url[:60]}...")
                    self.history.update(url, status='FILTERED')
                    self.download_queue.task_done()
                    continue
                
                # Download the article
                try:
                    rss_content_fallback = get_rss_content(url)
                    data = article_downloader.download_article(url, rss_fallback=rss_content_fallback)
                    
                    # Handle QUEUED_FOR_PLAYWRIGHT return (403 sent to Playwright)
                    if data == 'QUEUED_FOR_PLAYWRIGHT':
                        # URL is already in Playwright queue, don't count as failure
                        self.download_queue.task_done()
                        continue

                    # Handle permanent 404 — mark in history so it's never retried
                    if data == '404_PERMANENT':
                        self.history.mark_processing_error(url, "404 Not Found — permanent skip")
                        self.history.update(url, status='NEGATIVE')
                        self.article_buffer.report_failure()
                        self.download_queue.task_done()
                        continue
                    
                    if data and data.get('text') and len(data['text'].strip()) > 50:
                        # Pre-check for duplicate titles
                        # Use RSS title first, if missing fallback to scraped HTML title
                        rss_title = get_rss_title(url)
                        article_title = rss_title if rss_title else data.get('title', '')
                        
                        if article_title and self.result_manager._is_similar_title(article_title):
                            print(f"      [{self.name}] SKIP (duplicate title): {article_title[:40]}...")
                            log_pipeline_error(f"Summarizer: [{self.name}] SKIP (duplicate title): {article_title[:40]}...")
                            self.history.update(url, status='FILTERED')
                        else:
                            # Store original title and add to buffer
                            data['originalTitle'] = article_title
                            if article_title:
                                self.result_manager._titles_index.add(
                                    self.result_manager._normalize_title(article_title)
                                )
                            self.article_buffer.put(data)
                    else:
                        # Download succeeded but content is empty/too short
                        self.history.mark_processing_error(url, "Empty or too short content")
                        self.article_buffer.report_failure()
                        
                except Exception as e:
                    print(f"   ❌ [{self.name}] Download failed {url[:50]}...: {str(e)[:30]}")
                    log_pipeline_error(f"Summarizer: [{self.name}] Download failed {url[:50]}...: {str(e)[:30]}")
                    self.history.mark_processing_error(url, str(e)[:100])
                    self.article_buffer.report_failure()
                
                self.download_queue.task_done()
                
            except Exception as e:
                print(f"   ⚠️ [{self.name}] Worker error: {e}")
                log_pipeline_error(f"Summarizer: [{self.name}] Worker error: {e}")


DOWNLOAD_WORKERS = 10  # Number of parallel download threads


def load_config():
    """Loads API Key and Prompt."""
    api_key = ""
    gemini_api_key = ""
    try:
        with open(os.path.join(INPUT_DIR, 'input.txt'), 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.replace("API_KEY=", "").strip()
                elif line.startswith("GEMINI_API_KEY="):
                    gemini_api_key = line.replace("GEMINI_API_KEY=", "").strip()
    except FileNotFoundError:
        print("Error: input.txt not found.")
        log_pipeline_error("Summarizer: Error: input.txt not found.")
        return None, None, None

    prompt_template = ""
    try:
        with open(os.path.join(INPUT_DIR, 'summarize.txt'), 'r', encoding='utf-8') as f:
            prompt_template = f.read().strip()
    except FileNotFoundError:
        print("Error: summarize.txt not found.")
        log_pipeline_error("Summarizer: Error: summarize.txt not found.")
        return None, None, None

    return api_key, prompt_template, gemini_api_key


def validate_news_item(item, idx):
    """Validates a single news item and attempts to fix common issues.
    
    Returns: (fixed_item, is_valid, errors)
    """
    errors = []
    fixed_item = dict(item)
    
    # Check required fields
    for field in REQUIRED_FIELDS:
        # Special handling for image - it is optional in prompt now
        if field == 'image':
            if field not in fixed_item or fixed_item.get(field) is None:
                fixed_item[field] = ""
            # Do NOT error if image is empty
        elif not fixed_item.get(field):
            # Try to provide defaults for other fields
            if field == 'tags':
                fixed_item['tags'] = []
            else:
                fixed_item[field] = ""
                errors.append(f"Item {idx}: Missing field '{field}'")

    # Validate section
    if 'section' in fixed_item:
        section = fixed_item['section']
        # Handle if section is a list (for fooldal)
        if isinstance(section, list):
            for s in section:
                if s not in VALID_SECTIONS:
                    errors.append(f"Item {idx}: Invalid section '{s}'")
        elif section not in VALID_SECTIONS:
            # Try to map common variations
            section_map = {
                'technológia': 'tech',
                'technology': 'tech',
                'tudmány': 'tudomany',
                'science': 'tudomany',
                'belföld': 'belfold_kulfold',
                'külföld': 'belfold_kulfold',
                'belfold': 'belfold_kulfold',
                'kulfold': 'belfold_kulfold',
                'üzlet': 'uzlet',
                'business': 'uzlet',
                'szórakozás': 'szorakozas',
                'entertainment': 'szorakozas',
                'szentatas': 'szorakozas',
                'életmód': 'eletmod',
                'lifestyle': 'eletmod',
                'bulvár': 'bulvar',
                'gaming': 'gamer',
                'játék': 'gamer',
                'videojatek': 'gamer',
                'videojátékok': 'gamer',
                'kriptovaluta': 'kripto',
                'crypto': 'kripto',
                'bitcoin': 'kripto',
                'blockchain': 'kripto',
                'blokklánc': 'kripto'
            }
            if isinstance(section, str) and section.lower() in section_map:
                fixed_item['section'] = section_map[section.lower()]
            else:
                errors.append(f"Item {idx}: Invalid section '{section}'")

    # Validate importance
    if 'importance' in fixed_item:
        try:
            val = int(fixed_item['importance'])
            if val < 1: val = 1
            if val > 5: val = 5
            fixed_item['importance'] = val
        except (ValueError, TypeError):
             fixed_item['importance'] = 3 # Default to average
    else:
        fixed_item['importance'] = 3 # Default if missing
    
    # Validate tags is a list
    if 'tags' in fixed_item and not isinstance(fixed_item['tags'], list):
        if isinstance(fixed_item['tags'], str):
            # Try to split by comma
            fixed_item['tags'] = [t.strip().strip('#') for t in fixed_item['tags'].split(',')]
        else:
            fixed_item['tags'] = []
            errors.append(f"Item {idx}: tags should be a list")
    
    # Clean up tags - remove # prefix if present AND normaliz (lowercase)
    if 'tags' in fixed_item and isinstance(fixed_item['tags'], list):
        # STRATEGY: Store strict lowercase for data efficiency and consistency.
        # Original casing is lost here, but `mostused_tags` would regenerate casing 
        # only if we track it. User said "normalize... so no difference".
        # This implies data identity is key.
        fixed_item['tags'] = [t.strip().strip('#').lower() for t in fixed_item['tags'] if t and t.strip()]
        
        # Override section for specific keywords (Requested by user: force kripto section)
        crypto_substrings = ('kript', 'crypt', 'bitcoin', 'ethereum', 'blokklánc', 'blockchain')
        if any(any(sub in tag for sub in crypto_substrings) for tag in fixed_item['tags']):
            fixed_item['section'] = 'kripto'
            
        # Override section for gaming keywords (Requested by user: force gamer section)
        gamer_exact = {'pc', 'ps4', 'ps5', 'xbox', 'nintendo', 'steam', 'epic_games'}
        gamer_substrings = ('gamer', 'gaming', 'videojáték', 'videojatek', 'konzol', 'playstation')
        if any(tag in gamer_exact or any(sub in tag for sub in gamer_substrings) for tag in fixed_item['tags']):
            fixed_item['section'] = 'gamer'
    
    # Validate URLs (skip image validation as it can be empty)
    for url_field in ['sourceLink']:
        if url_field in fixed_item and fixed_item[url_field]:
            url = fixed_item[url_field]
            if not url.startswith(('http://', 'https://')):
                errors.append(f"Item {idx}: Invalid URL in '{url_field}'")
    
    is_valid = len(errors) == 0
    return fixed_item, is_valid, errors



def add_model_info(items, model_name):
    """Adds the model name to each news item, and applies specific hardcoded tags."""
    forhim_domains = ['fhm.com', 'maxim.com', 'starity.hu', 'playboy.com', 'player.hu', 'raketa.hu', 'roadster.hu', 'instylemen.hu', 'firstclass.hu']
    for item in items:
        item['processed_by'] = model_name
        
        # Hardcode "ForHim" tag for specific domains
        link = item.get('sourceLink', '').lower()
        if any(d in link for d in forhim_domains):
            tags = item.get('tags', [])
            if isinstance(tags, list):
                # Only add if not already present (case-insensitive check)
                if not any(t.lower() == 'forhim' for t in tags):
                    tags.append('ForHim')
                    item['tags'] = tags
            else:
                item['tags'] = ['ForHim']
    return items


# ------------- CONFIG -------------
FILTERED_RE = re.compile(
    r'^\[(https?://[^\]]+)\]\s*kisz(?:ű|u)rve\.?\s*$',
    re.IGNORECASE | re.MULTILINE
)

B64_RE = re.compile(r'^[A-Za-z0-9+/=\s]+$')

SMART_QUOTES = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
}


# ------------- HELPERS -------------
def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s.strip())
    return s.strip()


def _extract_filtered_links(s: str):
    s = s or ""
    links = FILTERED_RE.findall(s)
    s = FILTERED_RE.sub("", s).strip()
    return s, links


def _replace_smart_quotes(s: str) -> str:
    for k, v in SMART_QUOTES.items():
        s = s.replace(k, v)
    return s


def _looks_like_base64_only(s: str) -> bool:
    if not s:
        return False
    t = s.strip()
    if "[" in t or "{" in t:
        return False
    if len(t) < 80:
        return False
    return bool(B64_RE.match(t))


def _maybe_decode_base64(s: str):
    if not _looks_like_base64_only(s):
        return None
    try:
        raw = base64.b64decode("".join(s.split()), validate=True)
        txt = raw.decode("utf-8", errors="replace").strip()
        if "[" in txt and (txt.startswith("[") or "[{" in txt or "[\n{" in txt):
            return txt
    except Exception:
        return None
    return None


def _has_any_json_payload(s: str) -> bool:
    """
    Strict: must contain an array start that could plausibly be JSON array of objects or empty array.
    This intentionally returns False for bracketed links like: [https://...](https://...)
    """
    if not s:
        return False
    if "[]" in s:
        return True
    # Require an array and at least one object marker.
    if "[" not in s or "]" not in s or "{" not in s:
        return False
    
    # Relaxed check: just look for the sequence [ ... { somewhere
    # We do this by finding the first '[' and checking if '{' appears after it (ignoring strings/whitespace ideally, 
    # but a simple 'exists after' check is usually enough for a heuristic).
    # The previous check was too specific about whitespace (e.g. didn't catch [\n  {).
    
    # Simple heuristic: is there a { after the first [ ?
    first_bracket = s.find("[")
    first_brace = s.find("{", first_bracket)
    return first_brace != -1


def _find_json_array_segment(s: str):
    """
    Finds first balanced JSON array segment where the next non-ws after '[' is '{' or ']'.
    String-aware: ignores brackets inside strings.
    """
    if not s:
        return None

    in_str = False
    esc = False
    i = 0

    while i < len(s):
        ch = s[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            i += 1
            continue

        if ch == "[":
            # next non-ws must be '{' or ']' (empty array)
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j >= len(s) or s[j] not in "{]":
                i += 1
                continue

            start = i
            depth = 0
            in_str2 = False
            esc2 = False
            k = i

            while k < len(s):
                c = s[k]
                if in_str2:
                    if esc2:
                        esc2 = False
                    elif c == "\\":
                        esc2 = True
                    elif c == '"':
                        in_str2 = False
                    k += 1
                    continue

                if c == '"':
                    in_str2 = True
                    k += 1
                    continue

                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        return s[start:k + 1]
                k += 1

            return None

        i += 1

    return None


def _fix_jsonish_minimally(s: str) -> str:
    """
    Minimal, safe-ish fixes. Does NOT try to be a general JSON5 parser.
    - normalizes line endings
    - replaces smart quotes
    - escapes raw newlines inside strings
    - fixes invalid backslashes inside strings
    - removes trailing commas outside strings
    - fixes missing comma between objects: }{ -> },{
    """
    if not s:
        return s

    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _replace_smart_quotes(s)

    # 1) Fix inside strings: raw newline + invalid backslash escapes
    out = []
    in_str = False
    esc = False
    i = 0

    while i < len(s):
        ch = s[i]

        if in_str:
            if esc:
                out.append(ch)
                esc = False
                i += 1
                continue

            if ch == "\\":
                nxt = s[i + 1] if i + 1 < len(s) else ""
                # If next is not a valid JSON escape, escape the backslash itself.
                if nxt and nxt not in ['"', "\\", "/", "b", "f", "n", "r", "t", "u"]:
                    out.append("\\\\")
                    i += 1
                    continue
                out.append("\\")
                esc = True
                i += 1
                continue

            if ch == "\n":
                out.append("\\n")
                i += 1
                continue

            if ch == '"':
                in_str = False
                out.append('"')
                i += 1
                continue

            out.append(ch)
            i += 1
            continue

        # not in string
        if ch == '"':
            in_str = True
            out.append('"')
            i += 1
            continue

        out.append(ch)
        i += 1

    fixed = "".join(out)

    # 2) Remove trailing commas before } or ] (outside strings)
    out2 = []
    in_str = False
    esc = False
    i = 0
    while i < len(fixed):
        ch = fixed[i]
        if in_str:
            out2.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            out2.append(ch)
            i += 1
            continue

        if ch == ",":
            j = i + 1
            while j < len(fixed) and fixed[j] in " \t\r\n":
                j += 1
            if j < len(fixed) and fixed[j] in "]}":
                i += 1
                continue

        out2.append(ch)
        i += 1

    fixed = "".join(out2)

    # 3) Fix missing commas between objects in arrays
    fixed = re.sub(r"}\s*{", "},{", fixed)

    return fixed.strip()


def _repair_at_position(s: str, err: json.JSONDecodeError) -> str:
    """
    Attempt to fix JSON at the exact error position reported by JSONDecodeError.
    Returns modified string, or original if no fix could be applied.
    """
    pos = err.pos
    msg = err.msg
    
    if pos is None or pos < 0 or pos > len(s):
        return s
    
    # --- "Expecting ',' delimiter" ---
    # Usually means a comma is missing between values/objects
    if "Expecting ',' delimiter" in msg:
        # Look backwards from pos to find what's there
        # Common case: }" followed by { or "  (missing comma between objects or values)
        before = s[:pos].rstrip()
        after = s[pos:]
        
        if before and after:
            # Insert comma before current position
            return before + ',' + after
    
    # --- "Expecting ':' delimiter" ---
    # Missing colon after a key
    if "Expecting ':' delimiter" in msg:
        before = s[:pos].rstrip()
        after = s[pos:]
        if before and after:
            return before + ':' + after
    
    # --- "Unterminated string" ---
    if "Unterminated string" in msg:
        # Find the opening quote and close the string before the next structural char
        # Strategy: insert a closing quote at position
        # Look forward from pos for a structural character
        search_end = min(pos + 200, len(s))
        for i in range(pos, search_end):
            if s[i] in ',:]}':
                return s[:i] + '"' + s[i:]
        # If nothing found, insert at pos
        return s[:pos] + '"' + s[pos:]
    
    # --- "Expecting value" ---
    # Empty value (e.g., "key": ,)
    if "Expecting value" in msg:
        ch_at = s[pos] if pos < len(s) else ''
        if ch_at == ',':
            # Double comma or trailing comma: remove the comma
            return s[:pos] + s[pos+1:]
        elif ch_at in '}]':
            # Trailing comma before closing bracket (already handled by _fix_jsonish, but just in case)
            # Look backwards for a comma to remove
            j = pos - 1
            while j >= 0 and s[j] in ' \t\r\n':
                j -= 1
            if j >= 0 and s[j] == ',':
                return s[:j] + s[j+1:]
        # Default: insert null
        return s[:pos] + 'null' + s[pos:]
    
    # --- "Extra data" ---
    if "Extra data" in msg:
        # Truncate everything after the valid JSON
        return s[:pos].rstrip()
    
    # --- "Expecting property name enclosed in double quotes" ---
    if "Expecting property name" in msg:
        ch_at = s[pos] if pos < len(s) else ''
        if ch_at == ',':
            # Extra comma (e.g., {,  or ,,)
            return s[:pos] + s[pos+1:]
        if ch_at == '}':
            # Trailing comma before } - look back and remove it
            j = pos - 1
            while j >= 0 and s[j] in ' \t\r\n':
                j -= 1
            if j >= 0 and s[j] == ',':
                return s[:j] + s[j+1:]
    
    # --- "Invalid \\escape" ---
    if "Invalid \\escape" in msg or "Invalid escape" in msg:
        if pos > 0 and pos < len(s):
            # Double-escape the backslash
            return s[:pos-1] + '\\\\' + s[pos:]
    
    # --- "Invalid control character" ---
    if "Invalid control character" in msg:
        if pos < len(s):
            ch = s[pos]
            if ch == '\n':
                return s[:pos] + '\\n' + s[pos+1:]
            elif ch == '\t':
                return s[:pos] + '\\t' + s[pos+1:]
            elif ch == '\r':
                return s[:pos] + '\\r' + s[pos+1:]
            else:
                # Remove the control character
                return s[:pos] + s[pos+1:]
    
    return s


def _extract_individual_objects(s: str):
    """
    Last-resort fallback: scan the text for individual balanced {...} objects,
    parse each one independently, and return whatever is valid.
    This handles:
      - Model 'extra speech' around JSON objects
      - One corrupted object in an otherwise valid array
      - Missing commas/brackets between objects
    Returns list of parsed dicts, or empty list if nothing found.
    """
    if not s:
        return []

    # Strip outer array brackets if present, to expose the objects
    stripped = s.strip()
    if stripped.startswith('['):
        stripped = stripped[1:]
    if stripped.endswith(']'):
        stripped = stripped[:-1]

    # Find all balanced {...} segments (string-aware)
    objects = []
    i = 0
    while i < len(stripped):
        ch = stripped[i]

        # Skip until we find a '{'
        if ch != '{':
            i += 1
            continue

        # Found '{' — find the matching '}'
        start = i
        depth = 0
        in_str = False
        esc = False
        k = i

        while k < len(stripped):
            c = stripped[k]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
                k += 1
                continue

            if c == '"':
                in_str = True
                k += 1
                continue

            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    # Found a balanced object
                    obj_str = stripped[start:k + 1]
                    objects.append(obj_str)
                    i = k + 1
                    break
            k += 1
        else:
            # Unmatched brace — skip it
            i += 1
            continue

    if not objects:
        return []

    # Try to parse each object individually
    recovered = []
    for obj_str in objects:
        # Apply minimal fixes before parsing
        fixed = _fix_jsonish_minimally(obj_str)
        try:
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                # Quick sanity check: must have at least title or content
                if parsed.get('title') or parsed.get('content') or parsed.get('sourceLink'):
                    recovered.append(parsed)
        except json.JSONDecodeError:
            # Try one more fix pass
            try:
                fixed2 = _fix_jsonish_minimally(fixed)
                parsed = json.loads(fixed2)
                if isinstance(parsed, dict) and (parsed.get('title') or parsed.get('content') or parsed.get('sourceLink')):
                    recovered.append(parsed)
            except json.JSONDecodeError:
                # This individual object is truly broken — skip it
                pass

    return recovered


def _try_parse_array(segment: str):
    """
    Returns (list_or_none, errors[])
    Multi-strategy JSON parsing:
      1. Raw parse
      2. _fix_jsonish_minimally (1x)
      3. _fix_jsonish_minimally (2x)
      4. Iterative error-location repair (up to 15 rounds)
    """
    errors = []
    candidates = [
        segment,
        _fix_jsonish_minimally(segment),
        _fix_jsonish_minimally(_fix_jsonish_minimally(segment)),
    ]

    last_err = None
    for pass_idx, cand in enumerate(candidates, 1):
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                return None, [f"Parsed JSON is not a list (pass {pass_idx})."]
            return parsed, errors
        except json.JSONDecodeError as e:
            last_err = e
            errors.append(f"json.loads failed (pass {pass_idx}): {str(e)[:160]}")
        except Exception as e:
            last_err = e
            errors.append(f"json.loads failed (pass {pass_idx}): {str(e)[:160]}")

    # --- ITERATIVE ERROR-LOCATION REPAIR ---
    # Start from the best candidate (double-fixed)
    current = candidates[-1]
    MAX_REPAIR_ITERATIONS = 15
    
    for iteration in range(MAX_REPAIR_ITERATIONS):
        try:
            parsed = json.loads(current)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                errors.append(f"Iterative repair (round {iteration+1}): parsed but not a list")
                break
            # Success!
            errors.append(f"✅ Iterative algorithmic repair succeeded after {iteration+1} round(s)")
            return parsed, errors
        except json.JSONDecodeError as e:
            prev = current
            current = _repair_at_position(current, e)
            if current == prev:
                # No change was made — can't fix this error
                errors.append(f"Iterative repair stuck at round {iteration+1}: {str(e)[:120]}")
                break
        except Exception as e:
            errors.append(f"Iterative repair unexpected error: {str(e)[:120]}")
            break

    errors.append(f"Final parse error: {last_err!r}")

    # --- LAST RESORT: Extract individual valid objects ---
    recovered = _extract_individual_objects(candidates[-1])
    if recovered:
        errors.append(f"🔧 Partial recovery: extracted {len(recovered)} valid object(s) from broken JSON")
        return recovered, errors

    return None, errors


# ------------- MAIN DROP-IN FUNCTION -------------
def validate_json_response(content: str, allow_filtered_only: bool = False):
    """
    Drop-in replacement for your existing validate_json_response().

    Returns:
      (validated_news, is_valid, errors, failed_links, filtered_links)
    """
    errors = []
    failed_links = []

    # 0) cleanup
    content = (content or "").strip()
    content = _strip_code_fences(content)

    # 1) extract filtered links first
    content, filtered_links = _extract_filtered_links(content)

    # 2) base64 decode if it looks like base64-only payload
    decoded = _maybe_decode_base64(content)
    if decoded:
        content = decoded

    # 3) HARD CHECK: if there is no JSON payload at all, drop
    if not _has_any_json_payload(content):
        errors.append("No JSON payload found in response.")
        return [], False, errors, failed_links, filtered_links

    # 4) find a JSON array segment (array-of-objects or empty array)
    segment = _find_json_array_segment(content)

    # If not found, try one more thing: sometimes the JSON is an escaped string.
    if segment is None:
        m = re.search(r'"(\s*\[\s*(?:\{|\])[\s\S]*\]\s*)"', content)
        if m:
            try:
                unescaped = json.loads(m.group(0))
                segment = _find_json_array_segment(unescaped)
            except Exception:
                segment = None

    if segment is None:
        # Last resort: try to extract individual objects directly from the raw text
        recovered = _extract_individual_objects(content)
        if recovered:
            errors.append(f"🔧 No balanced array found, but recovered {len(recovered)} individual object(s)")
            parsed = recovered
        else:
            errors.append("JSON payload exists but could not extract a balanced JSON array segment.")
            return [], False, errors, failed_links, filtered_links
    else:
        # 5) parse (multi-pass repair)
        parsed, parse_errors = _try_parse_array(segment)
        if parsed is None:
            errors.extend(parse_errors)
            return [], False, errors, failed_links, filtered_links

    # 6) validate/normalize items using your existing validate_news_item()
    validated_news = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            errors.append(f"Item {idx} is not an object")
            continue

        # This assumes you already have: validate_news_item(item, idx) in your codebase.[1]
        fixed_item, is_item_valid, item_errors = validate_news_item(item, idx)
        if item_errors:
            errors.extend(item_errors)

        # Keep item if it has the essentials after fixing (same spirit as your current code).[1]
        if fixed_item.get("title") and fixed_item.get("content"):
            validated_news.append(fixed_item)
        else:
            if isinstance(item, dict) and item.get("sourceLink"):
                failed_links.append(item.get("sourceLink"))

    # 7) is_valid decision (strict by default)
    is_valid = (len(validated_news) > 0) or (allow_filtered_only and len(filtered_links) > 0)

    return validated_news, is_valid, errors, failed_links, filtered_links


def _detect_missing_batch_items(batch_items, news_list, filtered_links):
    """
    Compares batch items against returned news items to detect which
    batch URLs are missing from the response (not returned by LLM).
    Returns list of missing URLs for reprocessing.
    """
    if not batch_items:
        return []
    
    # Also count filtered links as "accounted for"
    filtered_set = set()
    for link in filtered_links:
        if isinstance(link, str):
            filtered_set.add(link.strip())
            filtered_set.add(normalize_url(link.strip()))
    
    # Use the robust pairing algorithm to match returned items to batch items
    # We pass a copy of news_list because pair_news_to_batch modifies it in place
    import copy
    temp_news_list = copy.deepcopy(news_list)
    paired_news = pair_news_to_batch(temp_news_list, batch_items, "MissingDetector")
    
    # Collect all URLs that were successfully paired
    returned_urls = set()
    for item in paired_news:
        src = item.get('sourceLink', '').strip()
        if src:
            returned_urls.add(src)
            returned_urls.add(normalize_url(src))
            
    # Find batch items whose URL wasn't returned
    missing = []
    for item in batch_items:
        url = item.get('url', item.get('link', '')).strip()
        if not url:
            continue
        norm_url = normalize_url(url)
        
        # Check exact & normalized match against paired URLs
        if url in returned_urls or norm_url in returned_urls:
            continue
        if url in filtered_set or norm_url in filtered_set:
            continue
            
        missing.append(url)
    
    return missing


def process_batch(api_key, prompt_template, items, category_file, use_gemini=False, use_geminipro=False, use_perplexity=False, use_gemini_cookie=False, use_gemini_api=False, gemini_api_key=None, use_geminipro_cli=False, use_g4f=False, use_deeperseek=False, use_multi_model=False, use_gemini_selenium=False, use_free_gemini_api=False, key_index=None, use_lmstudio_local=False, use_lmstudio_remote=False):
    """Sends a batch of news items (dicts with text) to the API for summarization.
    
    Returns: (news_list, failed_links, filtered_links, model_used)
    """
    if not items:
        return [], [], [], None

    # Format articles for prompt
    articles_text = ""
    for i, item in enumerate(items):
        articles_text += f"--- CIKK {i+1} ---\n"
        articles_text += f"LINK: {item.get('url', item.get('link', 'N/A'))}\n"
        articles_text += f"CÍM: {item['title']}\n"
        articles_text += f"TARTALOM:\n{item['text']}\n"
        articles_text += "-------------------\n\n"

    # Replace placeholder or append
    if "{links}" in prompt_template:
        final_prompt = prompt_template.replace("{links}", articles_text)
    else:
        final_prompt = prompt_template + "\n\nFeldolgozandó Cikkek:\n" + articles_text

    # DEBUG: Save prompt context
    if DEBUG_PAIRING_MODE:
        DEBUG_CONTEXT.prompt = final_prompt

    if use_multi_model:
        model_label = "Multi-Model (Failover)"
    elif use_perplexity:
        model_label = "Perplexity Pro"
    elif use_g4f:
        model_label = "GPT4Free"
    elif use_deeperseek:
        model_label = "DeeperSeek"
    elif use_geminipro:
        model_label = "Gemini 3 Pro"
    elif use_geminipro_cli:
        model_label = "Gemini Chat API"
    elif use_free_gemini_api:
        model_label = "Free Gemini Pool (Flash 2.5)"
    elif use_lmstudio_local:
        model_label = "LM Studio Local"
    elif use_lmstudio_remote:
        model_label = "LM Studio Remote"
    elif use_gemini_cookie:
        model_label = "Gemini (Cookie)"
    elif use_gemini_api:
        model_label = "Gemini API"
    elif use_gemini: # Legacy flag
        model_label = "Gemini (Cookie)"
    else:
        model_label = "Gemini API"

    # Normalize flags for backward compatibility
    if use_gemini:
        use_gemini_cookie = True
        
    for attempt in range(MAX_RETRIES):
        try:
            print(f"      [Batch] Sending request to {model_label} (Timeout: 450s)... Attempt {attempt+1}/{MAX_RETRIES}")
            
            if use_multi_model:
                # Use orchestrator with automatic failover (already has timeout)
                orchestrator = backend_orchestrator.get_orchestrator()
                content, model_used = orchestrator.call_with_failover(final_prompt, use_pro=True)
            elif use_perplexity:
                (content, model_used), success = call_with_timeout(
                    lambda: perplexity_client.call_with_fallback(final_prompt, use_pro=True)
                )
                if not success:
                    content, model_used = None, None
            elif use_g4f:
                (content, model_used), success = call_with_timeout(
                    lambda: g4f_client.call_with_fallback(final_prompt, use_pro=False)
                )
                if not success:
                    content, model_used = None, None
            elif use_deeperseek:
                (content, model_used), success = call_with_timeout(
                    lambda: deeperseek_client.call_with_fallback(final_prompt, use_pro=False)
                )
                if not success:
                    content, model_used = None, None
            elif use_gemini_selenium:
                import gemini_selenium_client
                (content, model_used), success = call_with_timeout(
                    lambda: gemini_selenium_client.call_with_fallback(final_prompt, use_pro=False),
                    timeout=SELENIUM_TIMEOUT
                )
                if not success:
                    content, model_used = None, None
            elif use_free_gemini_api:
                # Free Gemini API Pool
                import gemini_api_pool
                def _call_free():
                    return gemini_api_pool.generate_with_free_api(final_prompt)
                (result, success), call_success = call_with_timeout(_call_free)
                if not call_success or not success:
                    content, model_used = None, None
                else:
                    content = result
                    model_used = gemini_api_pool.MODEL_NAME
            elif use_lmstudio_local:
                import lmstudio_client
                def _call_lm_local():
                    return lmstudio_client.call_lmstudio_local(final_prompt, use_pro=False)
                (result, model_found), call_success = call_with_timeout(_call_lm_local)
                if not call_success or not result:
                    content, model_used = None, None
                else:
                    content, model_used = result, model_found
            elif use_lmstudio_remote:
                import lmstudio_client
                def _call_lm_remote():
                    return lmstudio_client.call_lmstudio_remote(final_prompt, use_pro=False)
                (result, model_found), call_success = call_with_timeout(_call_lm_remote)
                if not call_success or not result:
                    content, model_used = None, None
                else:
                    content, model_used = result, model_found
            elif use_geminipro:
                (content, model_used), success = call_with_timeout(
                    lambda: geminipro_client.call_with_fallback(final_prompt, use_pro=True)
                )
                if not success:
                    content, model_used = None, None
            elif use_gemini_cookie:
                (content, model_used), success = call_with_timeout(
                    lambda: geminipro_client.call_with_fallback(final_prompt, use_pro=False)
                )
                if not success:
                    content, model_used = None, None
            elif use_gemini_api:
                def _call_paid():
                    return gemini_client.call_with_fallback(
                        prompt=final_prompt,
                        system_prompt=None,
                        gemini_api_key=gemini_api_key,
                        timeout=450,
                        key_index=key_index
                    )
                (content, model_used), call_success = call_with_timeout(_call_paid)
                if not call_success:
                    content, model_used = None, None
            elif use_geminipro_cli:
                def _call_cli():
                    return gemini_chat_client.call_with_fallback(final_prompt, use_pro=True, api_key=gemini_api_key)
                (content, model_used), call_success = call_with_timeout(_call_cli)
                if not call_success:
                    content, model_used = None, None
            else:
                def _call_else():
                    return gemini_client.call_with_fallback(
                        prompt=final_prompt,
                        system_prompt=None,
                        gemini_api_key=gemini_api_key,
                        timeout=450,
                        key_index=key_index
                    )
                (content, model_used), call_success = call_with_timeout(_call_else)
                if not call_success:
                    content, model_used = None, None
            
            if not content:
                print(f"      [Batch] No response from API (attempt {attempt+1}/{MAX_RETRIES})")
                log_pipeline_error(f"Summarizer: [Batch] No response from API (attempt {attempt+1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(10)
                continue
            
            print(f"      [Batch] Received {len(content)} chars from {model_used}.")
            
            # DEBUG: Save response context
            if DEBUG_PAIRING_MODE:
                DEBUG_CONTEXT.response = content

            
            # Validate JSON response
            news_list, is_valid, errors, failed_links, filtered_links = validate_json_response(content)
            
            if is_valid:
                # Add processed_by field to each news item
                news_list = add_model_info(news_list, model_used)
                    
                print(f"      [Batch] Valid JSON with {len(news_list)} news items, {len(filtered_links)} filtered (via {model_used}).")
                if errors:
                    for err in errors:
                        if '🔧' in str(err):
                            print(f"      [Batch] {err}")
                            log_pipeline_error(f"Summarizer: [Batch] {err}")
                    non_recovery = [e for e in errors if '🔧' not in str(e)]
                    if non_recovery:
                        print(f"      [Batch] Minor issues fixed: {len(non_recovery)}")
                        log_pipeline_error(f"Summarizer: [Batch] Minor issues fixed: {len(non_recovery)}")
                    
                # Log importance
                for item in news_list:
                    imp = item.get('importance', 3)
                    title = item.get('title', 'No Title')
                    print(f"      [IMPORTANCE: {imp}] {title[:60]}...")

                # Detect missing batch items (not returned by LLM)
                missing_links = _detect_missing_batch_items(items, news_list, filtered_links)
                if missing_links:
                    print(f"      [Batch] ⚠️ {len(missing_links)} item(s) missing from response → marked for reprocessing")
                    log_pipeline_error(f"Summarizer: [Batch] {len(missing_links)} item(s) missing from response → marked for reprocessing")
                    failed_links.extend(missing_links)
                    
                return news_list, failed_links, filtered_links, model_used
            else:
                print(f"      [Batch] Invalid JSON (attempt {attempt+1}/{MAX_RETRIES})")
                log_pipeline_error(f"Summarizer: [Batch] Invalid JSON (attempt {attempt+1}/{MAX_RETRIES})")
                for err in errors[:5]:
                    print(f"        - {err}")
                    log_pipeline_error(f"Summarizer: [Batch] Invalid JSON error: {err}")
                
                # Try to repair the JSON using Gemini (for any backend)
                # Check for "No JSON payload" error and skip repair if found
                # But if response is long (>=500 chars), still attempt repair
                skip_repair = False
                for err in errors:
                    if "No JSON payload found" in str(err):
                        if len(content) < 500:
                            print(f"      [Batch] ⏭️ Skipping repair: No JSON payload found (response too short).")
                            log_pipeline_error(f"Summarizer: [Batch] Skipping repair: No JSON payload found (response too short).")
                            skip_repair = True
                        else:
                            print(f"      [Batch] 🔧 No JSON payload, but response is {len(content)} chars - attempting repair...")
                            log_pipeline_error(f"Summarizer: [Batch] No JSON payload, but response is {len(content)} chars - attempting repair...")
                        break
                
                if skip_repair:
                    if attempt < MAX_RETRIES - 1:
                        print("      [Batch] Retrying...")
                        time.sleep(5)
                    continue

                if (use_geminipro or use_gemini_cookie or use_perplexity or use_geminipro_cli or use_deeperseek or use_multi_model) and content:
                    print(f"      [Batch] Attempting JSON repair...")
                    log_pipeline_error(f"Summarizer: [Batch] Attempting JSON repair...")
                    # Use gemini_client (API) with key rotation instead of geminipro (cookie)
                    repaired, repair_model, repair_success = gemini_client.repair_json_response(content)
                    
                    if repair_success and repaired:
                        # Validate the repaired JSON
                        news_list, is_valid, errors, failed_links, filtered_links = validate_json_response(repaired)
                        
                        if is_valid:
                            news_list = add_model_info(news_list, f"{model_used}+repair:{repair_model}")
                            
                            print(f"      [Batch] JSON repaired! {len(news_list)} items (via {repair_model})")
                            log_pipeline_error(f"Summarizer: [Batch] JSON repaired! {len(news_list)} items (via {repair_model})")
                            
                            # Log importance
                            for item in news_list:
                                imp = item.get('importance', 3)
                                title = item.get('title', 'No Title')
                                print(f"      [IMPORTANCE: {imp}] {title[:60]}...")

                            # Detect missing batch items
                            missing_links = _detect_missing_batch_items(items, news_list, filtered_links)
                            if missing_links:
                                print(f"      [Batch] ⚠️ {len(missing_links)} item(s) missing from repaired response → marked for reprocessing")
                                log_pipeline_error(f"Summarizer: [Batch] {len(missing_links)} item(s) missing from repaired response → marked for reprocessing")
                                failed_links.extend(missing_links)
                                
                            return news_list, failed_links, filtered_links, model_used
                        else:
                            print(f"      [Batch] Repaired JSON still invalid")
                            log_pipeline_error(f"Summarizer: [Batch] Repaired JSON still invalid")
                
                if attempt < MAX_RETRIES - 1:
                    print("      [Batch] Retrying...")
                    time.sleep(5)
                continue

        except Exception as ex:
            print(f"      [Batch] ERROR: {ex} (Attempt {attempt+1}/{MAX_RETRIES})")
            log_pipeline_error(f"Summarizer: [Batch] ERROR: {ex} (Attempt {attempt+1}/{MAX_RETRIES})")
            
        if attempt < MAX_RETRIES - 1:
            print("      [Batch] Retrying in 10 seconds...")
            time.sleep(10)
    
    # All retries failed with primary backend — try fallback backends
    print(f"      [Batch] ⚠️ Primary backend ({model_label}) exhausted {MAX_RETRIES} retries. Trying fallback backends...")
    log_pipeline_error(f"Summarizer: [Batch] Primary backend ({model_label}) exhausted retries. Trying fallbacks...")
    
    # Define fallback backends to try (skip if already the primary)
    fallback_configs = []
    if not use_free_gemini_api:
        try:
            import gemini_api_pool
            if gemini_api_pool.check_available():
                fallback_configs.append(("Free Gemini API", {'use_free_gemini_api': True}))
        except Exception:
            pass
    if not use_gemini_selenium:
        fallback_configs.append(("Gemini Selenium", {'use_gemini_selenium': True}))
    if not use_g4f:
        fallback_configs.append(("G4F", {'use_g4f': True}))
    
    for fb_label, fb_flags in fallback_configs:
        print(f"      [Batch] 🔄 Fallback attempt with {fb_label}...")
        log_pipeline_error(f"Summarizer: [Batch] Fallback attempt with {fb_label}")
        
        for fb_attempt in range(2):  # Max 2 tries per fallback
            try:
                fb_content = None
                fb_model = None
                
                if fb_flags.get('use_free_gemini_api'):
                    import gemini_api_pool
                    fb_content, fb_success = gemini_api_pool.generate_with_free_api(final_prompt)
                    fb_model = "gemini-3.1-flash-lite"
                    if not fb_success:
                        fb_content = None
                elif fb_flags.get('use_gemini_selenium'):
                    import gemini_selenium_client
                    (fb_content, fb_model), fb_success = call_with_timeout(
                        lambda: gemini_selenium_client.call_with_fallback(final_prompt, use_pro=False),
                        timeout=SELENIUM_TIMEOUT
                    )
                    if not fb_success:
                        fb_content = None
                elif fb_flags.get('use_g4f'):
                    (fb_content, fb_model), fb_success = call_with_timeout(
                        lambda: g4f_client.call_with_fallback(final_prompt, use_pro=False)
                    )
                    if not fb_success:
                        fb_content = None
                
                if not fb_content:
                    if fb_attempt < 1:
                        time.sleep(5)
                    continue
                
                news_list, is_valid, errors, failed_links, filtered_links = validate_json_response(fb_content)
                if is_valid and news_list:
                    news_list = add_model_info(news_list, f"{fb_model or fb_label}(fallback)")
                    print(f"      [Batch] ✅ Fallback {fb_label} succeeded! {len(news_list)} items")
                    log_pipeline_error(f"Summarizer: [Batch] Fallback {fb_label} succeeded! {len(news_list)} items")
                    
                    missing_links = _detect_missing_batch_items(items, news_list, filtered_links)
                    if missing_links:
                        failed_links.extend(missing_links)
                    
                    return news_list, failed_links, filtered_links, fb_model or fb_label
                else:
                    if fb_attempt < 1:
                        time.sleep(5)
            except Exception as fb_ex:
                print(f"      [Batch] Fallback {fb_label} error: {fb_ex}")
                log_pipeline_error(f"Summarizer: [Batch] Fallback {fb_label} error: {fb_ex}")
    
    # All fallbacks also failed
    print(f"      [Batch] ❌ All backends exhausted. Marking batch as failed.")
    log_pipeline_error(f"Summarizer: [Batch] All backends (primary + fallbacks) exhausted.")
    failed_links = [item.get('url', item.get('link', 'unknown')) for item in items]
    return [], failed_links, [], None


def process_file(filename, api_key, prompt_template, history, use_gemini=False, use_geminipro=False, use_perplexity=False, use_gemini_cookie=False, use_gemini_api=False, gemini_api_key=None, use_geminipro_cli=False, use_g4f=False, use_deeperseek=False, use_multi_model=False, key_index=None, use_gemini_selenium=False, use_free_gemini_api=False, use_lmstudio_local=False, use_lmstudio_remote=False):
    """Processes a single category file and returns news items."""
    # Fix: Support absolute paths and fallback
    if os.path.exists(filename):
        input_path = filename
    elif filename == 'links.txt' and os.path.exists('links.txt'):
         input_path = 'links.txt'
    else:
        input_path = os.path.join(OUTPUT_DIR, filename)
    
    print(f"Processing {filename}...")
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        log_pipeline_error(f"Summarizer: Error reading {filename}: {e}")
        return []

    # Extract clean links
    links = []
    for line in lines:
        line = line.strip()
        if not line: 
            continue
            
        if '][' in line:
            try:
                parts = line.split('][')
                if len(parts) >= 2:
                    link = parts[1].replace(']', '')
                    links.append(link)
            except (IndexError, ValueError):
                continue
        elif line.startswith('http'):
            # Support raw links
            if 'eredeti-cikk-url' in line or 'example.com' in line:
                continue
            links.append(line)
    
    if not links:
        print(f"No valid links found in {filename}.")
        log_pipeline_error(f"Summarizer: No valid links found in {filename}.")
        return []

    # Filter out already summarized links
    links_to_summarize = []
    skipped_failures = 0
    for link in links:
        if history.is_summarized(link):
            continue
        
        # Check failure count
        if history.get_failure_count(link) >= 2:
            skipped_failures += 1
            continue

        links_to_summarize.append(link)
        
    if skipped_failures > 0:
        print(f"  - Skipped {skipped_failures} links due to repeated failures (>=2 attempts).")
        
    if not links_to_summarize:
        print(f"  - All {len(links)} links in {filename} are either summarized or failed too many times.")
        return []

    # Batching
    # Batching setting
    settings = pipeline_menu.get_settings()
    batch_size = settings.get('global', {}).get('summarizer_batch_size', BATCH_SIZE)

    # NEW: Smart Shuffle to avoid rate limits
    links_to_summarize = smart_shuffle_links(links_to_summarize)

    # NEW: Download articles
    print(f"  Downloading content for {len(links_to_summarize)} links...")
    downloaded_items = []
    failed_downloads = []

    # Helper function for threading
    def _dl_wrapper(url):
        return article_downloader.download_article(url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Map futures to URLs
        future_to_url = {executor.submit(_dl_wrapper, url): url for url in links_to_summarize}
        
        for future in tqdm(concurrent.futures.as_completed(future_to_url), total=len(links_to_summarize), desc="  Downloading"):
            url = future_to_url[future]
            try:
                data = future.result()
                if data and data.get('text') and len(data['text'].strip()) > 50:
                    downloaded_items.append(data)
                else:
                    # Too short or failed
                    failed_downloads.append(url)
            except Exception as e:
                # print(f"Download error {url}: {e}")
                failed_downloads.append(url)
    
    if failed_downloads:
        print(f"  Warning: {len(failed_downloads)} links failed to download or had no content.")
        log_pipeline_error(f"Summarizer: Warning: {len(failed_downloads)} links failed to download or had no content.")
        # Mark them as failed in history? Or just skip? 
        # For now, we just skip them in this run, they remain unsummarized.

    if not downloaded_items:
        print("  No valid articles to summarize.")
        return []

    # Create batches of article objects
    batches = [downloaded_items[i:i + batch_size] for i in range(0, len(downloaded_items), batch_size)]
    
    print(f"  Prepared {len(downloaded_items)} articles in {len(batches)} batches (Size: {batch_size}).")

    all_news = []
    all_failed = []

    # Check if we should use parallel processing
    if use_multi_model:
        # PARALLEL MODE: All backends process batches simultaneously
        print(f"  🚀 PARALLEL MODE: Using all available backends simultaneously")
        
        orchestrator = backend_orchestrator.get_orchestrator()
        backend_orchestrator.setup_default_backends()
        
        # Define the batch processor function for the orchestrator
        def parallel_batch_processor(batch, backend_name, client):
            """Process a single batch using the given backend."""
            # Format articles for prompt
            articles_text = ""
            for i, item in enumerate(batch):
                articles_text += f"--- CIKK {i+1} ---\n"
                articles_text += f"LINK: {item.get('url', item.get('link', 'N/A'))}\n"
                articles_text += f"CÍM: {item['title']}\n"
                articles_text += f"TARTALOM:\n{item['text']}\n"
                articles_text += "-------------------\n\n"
            
            # Build prompt
            if "{links}" in prompt_template:
                final_prompt = prompt_template.replace("{links}", articles_text)
            else:
                final_prompt = prompt_template + "\n\nFeldolgozandó Cikkek:\n" + articles_text
            
            try:
                print(f"      [{backend_name}] Processing batch...")
                content, model = client.call_with_fallback(final_prompt, use_pro=True)
                
                if not content:
                    msg = f"API returned empty content for batch"
                    print(f"      {msg}")
                    log_pipeline_error(f"Summarizer: {msg}")
                    return None, None

                # Validate JSON
                news_list, is_valid, errors, failed, filtered = validate_json_response(content)
                
                if is_valid and news_list:
                    # ----------------------------------------------------
                    # FIX: Robust Pairing (Refactored Helper)
                    # ----------------------------------------------------
                    news_list = pair_news_to_batch(news_list, batch, backend_name)
                    
                    news_list = add_model_info(news_list, model or backend_name)
                    print(f"      [{backend_name}] ✓ Got {len(news_list)} items")
                    
                    # Log importance
                    for item in news_list:
                        imp = item.get('importance', 3)
                        title = item.get('title', 'No Title')
                        print(f"      [{backend_name}] [IMPORTANCE: {imp}] {title[:60]}...")
                        
                    return news_list, model
                else:
                    print(f"      [{backend_name}] ✗ Invalid JSON")
                    log_pipeline_error(f"Summarizer: [{backend_name}] Invalid JSON. Errors: {errors}")
                    
                    # Check for "No JSON payload" error and skip repair if found
                    # But if response is long (>=500 chars), still attempt repair
                    skip_repair = False
                    for err in errors:
                        if "No JSON payload found" in str(err):
                            if len(content) < 500:
                                print(f"      [{backend_name}] ⏭️ Skipping repair: No JSON payload (response too short).")
                                log_pipeline_error(f"Summarizer: [{backend_name}] Skipping repair: No JSON payload (response too short).")
                                skip_repair = True
                            else:
                                print(f"      [{backend_name}] 🔧 No JSON payload, but response is {len(content)} chars - attempting repair...")
                                log_pipeline_error(f"Summarizer: [{backend_name}] No JSON payload, but response is {len(content)} chars - attempting repair...")
                            break
                    
                    if skip_repair:
                        return None, None

                    # Attempt repair
                    print(f"      [{backend_name}] 🔧 Attempting repair...")
                    log_pipeline_error(f"Summarizer: [{backend_name}] Attempting repair...")
                    repaired, repair_model, repair_success = gemini_client.repair_json_response(content)
                    
                    if repair_success and repaired:
                        # Validate the repaired JSON
                        news_list, is_valid, errors, failed, filtered = validate_json_response(repaired)
                        if is_valid and news_list:
                            # ----------------------------------------------------
                            # FIX: Robust Pairing (Refactored Helper) - Repair Path
                            # ----------------------------------------------------
                            news_list = pair_news_to_batch(news_list, batch, backend_name)

                            news_list = add_model_info(news_list, f"{model or backend_name}+repair:{repair_model}")
                            print(f"      [{backend_name}] ✅ JSON repaired! {len(news_list)} items")
                            log_pipeline_error(f"Summarizer: [{backend_name}] JSON repaired! {len(news_list)} items")
                            
                            # Log importance
                            for item in news_list:
                                imp = item.get('importance', 3)
                                title = item.get('title', 'No Title')
                                print(f"      [{backend_name}] [IMPORTANCE: {imp}] {title[:60]}...")
                                
                            return news_list, model
                    
                    return None, None
            except Exception as e:
                msg = f"Unexpected backend error: {e}"
                print(f"      {msg}")
                log_pipeline_error(f"Summarizer: {msg}")
                return None, None
        
        # Process all batches in parallel using race mode
        results = orchestrator.process_batches_parallel(
            batches,
            parallel_batch_processor,
            use_pro=True,
            mode="race"
        )
        
        # Collect results
        for batch_idx, news_list, model in results:
            if news_list:
                all_news.extend(news_list)
                # Mark successful links as summarized
                batch_urls = [item['url'] for item in batches[batch_idx]]
                for link in batch_urls:
                    history.update(link, summarized=True)
        
        # Mark failed batches
        processed_indices = {r[0] for r in results}
        for i, batch in enumerate(batches):
            if i not in processed_indices:
                for item in batch:
                    all_failed.append(item['url'])
                    history.mark_processing_error(item['url'], "Failed in parallel processing")
    else:
        # SEQUENTIAL MODE: Original behavior for single-backend mode
        # Build URL → downloaded_item map for single-item retry
        url_to_item = {item.get('url', ''): item for item in downloaded_items}

        for i, batch in enumerate(tqdm(batches, desc=f"  Summarizing {filename}", unit="batch")):
            news_list, failed_links_batch, filtered_links_batch, model_used = process_batch(
                api_key, prompt_template, batch, filename,
                use_gemini=use_gemini, use_geminipro=use_geminipro, use_perplexity=use_perplexity,
                use_gemini_cookie=use_gemini_cookie, use_gemini_api=use_gemini_api, gemini_api_key=gemini_api_key,
                use_geminipro_cli=use_geminipro_cli,
                use_g4f=use_g4f, use_deeperseek=use_deeperseek, use_multi_model=False,
                key_index=key_index
            )

            # Mark filtered links as FILTERED (don't retry)
            for link in filtered_links_batch:
                history.update(link, status='FILTERED')

            if news_list:
                # ----------------------------------------------------
                # FIX: Robust Pairing (Refactored Helper) - Sequential Mode
                # ----------------------------------------------------
                news_list = pair_news_to_batch(news_list, batch, "Sequential")
                # ----------------------------------------------------

                all_news.extend(news_list)
                batch_urls = [item['url'] for item in batch]
                successful_links = set(batch_urls) - set(failed_links_batch) - set(filtered_links_batch)
                for link in successful_links:
                    history.update(link, summarized=True)

            # Immediate single-item retry for missing items (Batch Missing fix)
            # Failed links that came from _detect_missing_batch_items were dropped by LLM —
            # retry them one-by-one so context pressure can't skip them.
            retry_candidates = [
                url for url in failed_links_batch
                if url in url_to_item and url not in filtered_links_batch
            ]
            if retry_candidates:
                print(f"      [Sequential] 🔁 Retrying {len(retry_candidates)} missing item(s) individually (Batch Missing fix)...")
                log_pipeline_error(f"Summarizer: Retrying {len(retry_candidates)} missing items individually after batch {i+1}")
                still_failed = []
                for retry_url in retry_candidates:
                    single_item = url_to_item[retry_url]
                    retry_news, retry_failed, retry_filtered, _ = process_batch(
                        api_key, prompt_template, [single_item], filename,
                        use_gemini=use_gemini, use_geminipro=use_geminipro, use_perplexity=use_perplexity,
                        use_gemini_cookie=use_gemini_cookie, use_gemini_api=use_gemini_api, gemini_api_key=gemini_api_key,
                        use_geminipro_cli=use_geminipro_cli,
                        use_g4f=use_g4f, use_deeperseek=use_deeperseek, use_multi_model=False,
                        key_index=key_index
                    )
                    if retry_news:
                        retry_news = pair_news_to_batch(retry_news, [single_item], "SingleRetry")
                        all_news.extend(retry_news)
                        history.update(retry_url, summarized=True)
                        print(f"      [SingleRetry] ✅ Recovered: {retry_url[:80]}")
                    else:
                        still_failed.append(retry_url)
                failed_links_batch = still_failed + [
                    url for url in failed_links_batch if url not in retry_candidates
                ]

            if failed_links_batch:
                all_failed.extend(failed_links_batch)
                for link in failed_links_batch:
                    history.mark_processing_error(link, "Failed after max retries")
    
    if all_failed:
        print(f"  - Warning: {len(all_failed)} links failed processing")
    
    return all_news


def reprocess_pairing_failures(output_dir, api_key, prompt_template):
    """
    Post-processing step: reprocess articles that had pairing failures.
    These are articles where the AI returned the content but lost the sourceLink.
    We reprocess them individually (batch size 1) with a fallback backend for better accuracy.
    """
    # Check all data files for pairing failures
    data_files = ['data.json', 'data_i4.json', 'data_i5.json']
    total_fixed = 0
    
    for data_filename in data_files:
        data_path = os.path.join(output_dir, data_filename)
        if not os.path.exists(data_path):
            continue
        
        try:
            with open(data_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
        except Exception:
            continue
        
        # Find items with pairing failures (no sourceLink)
        failed_items = []
        failed_indices = []
        for idx, item in enumerate(items):
            if item.get('pairingFailed') or (not item.get('sourceLink') and item.get('title')):
                failed_items.append((idx, item))
                failed_indices.append(idx)
        
        if not failed_items:
            continue
        
        print(f"\n  🔄 Found {len(failed_items)} pairing failures in {data_filename} — reprocessing individually...")
        log_pipeline_error(f"Summarizer: Reprocessing {len(failed_items)} pairing failures from {data_filename}")
        
        # Try to use free-gemini-api first, then gemini-selenium, then g4f
        fallback_backends = []
        try:
            import gemini_api_pool
            if gemini_api_pool.check_available():
                fallback_backends.append('free_gemini_api')
        except Exception:
            pass
        fallback_backends.append('gemini_selenium')
        fallback_backends.append('g4f')
        
        fixed_count = 0
        max_to_fix = min(len(failed_items), 5)   # Batch-level single-item retry already covers most; cap tightly
        reprocess_start = time.time()
        MAX_REPROCESS_SECONDS = 60  # Hard cap: 1 min max — don't hold up the pipeline
        
        for item_idx, (original_idx, item) in enumerate(failed_items[:max_to_fix]):
            # Global timeout guard: stop reprocessing if we've been at it too long
            if time.time() - reprocess_start > MAX_REPROCESS_SECONDS:
                print(f"  ⏱️ Reprocessing time limit ({MAX_REPROCESS_SECONDS}s) reached — stopping early, items will retry next run.")
                log_pipeline_error(f"Summarizer: Reprocessing stopped early (time limit), {max_to_fix - item_idx} items remaining.")
                break
            
            title = item.get('title', 'Unknown')[:50]
            author = item.get('author', 'Unknown')
            content_preview = item.get('content', '')[:200]
            
            # Build a focused prompt for a single article re-identification
            single_prompt = f"""Az alábbi összefoglalt hír forráslink-je elveszett. Kérlek add vissza a következő JSON formátumban, 
a sourceLink mezővel kiegészítve. Csak a JSON-t add vissza, semmi mást.

Cím: {item.get('title', '')}
Szerző/Forrás: {author}
Tartalom: {content_preview}

Válaszolj PONTOSAN ebben a formátumban:
{{"sourceLink": "https://...", "image": "https://..."}}

Ha nem tudod meghatározni a pontos linket, írd be amit a cím és a szerző alapján a legvalószínűbbnek tartasz.
"""
            
            for backend in fallback_backends:
                try:
                    fb_content = None
                    if backend == 'free_gemini_api':
                        import gemini_api_pool
                        fb_content, fb_success = gemini_api_pool.generate_with_free_api(single_prompt)
                        if not fb_success:
                            fb_content = None
                    elif backend == 'gemini_selenium':
                        import gemini_selenium_client
                        (fb_content, _), fb_success = call_with_timeout(
                            lambda: gemini_selenium_client.call_with_fallback(single_prompt, use_pro=False),
                            timeout=SELENIUM_TIMEOUT  # Selenium needs full timeout even for single items (browser startup + response wait)
                        )
                        if not fb_success:
                            fb_content = None
                    elif backend == 'g4f':
                        try:
                            import g4f_client
                            (fb_content, _), fb_success = call_with_timeout(
                                lambda: g4f_client.call_with_fallback(single_prompt),
                                timeout=60
                            )
                            if not fb_success:
                                fb_content = None
                        except (SyntaxError, UnicodeDecodeError, ImportError) as g4f_err:
                            print(f"    ⚠️ [{item_idx+1}/{max_to_fix}] g4f import/encoding error (skipping): {g4f_err}")
                            fb_content = None
                    
                    if fb_content:
                        # Try to parse the response
                        try:
                            # Strip code fences
                            cleaned = fb_content.strip()
                            if cleaned.startswith('```'):
                                cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
                            if cleaned.endswith('```'):
                                cleaned = cleaned[:cleaned.rfind('```')]
                            cleaned = cleaned.strip()
                            
                            parsed = json.loads(cleaned)
                            new_link = parsed.get('sourceLink', '').strip()
                            new_image = parsed.get('image', '').strip()
                            
                            if new_link and new_link.startswith('http'):
                                items[original_idx]['sourceLink'] = new_link
                                if 'pairingFailed' in items[original_idx]:
                                    del items[original_idx]['pairingFailed']
                                if 'originalTitle' in items[original_idx] and items[original_idx]['originalTitle'] == 'Unknown - Pairing Failed':
                                    del items[original_idx]['originalTitle']
                                # Also update image if we got one and current is empty
                                if new_image and new_image.startswith('http') and not items[original_idx].get('image'):
                                    items[original_idx]['image'] = new_image
                                fixed_count += 1
                                print(f"    ✅ [{item_idx+1}/{max_to_fix}] Fixed: {title}...")
                                break  # Success, move to next item
                        except json.JSONDecodeError:
                            pass
                except Exception as e:
                    print(f"    ⚠️ [{item_idx+1}/{max_to_fix}] Backend {backend} error: {e}")
            
            # Small delay between items
            time.sleep(1)
        
        if fixed_count > 0:
            # Save updated data
            try:
                with open(data_path, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                print(f"  ✅ Fixed {fixed_count}/{len(failed_items)} pairing failures in {data_filename}")
                total_fixed += fixed_count
            except Exception as e:
                print(f"  ❌ Error saving {data_filename}: {e}")
        else:
            print(f"  ⚠️ Could not fix any pairing failures in {data_filename}")
    
    return total_fixed



def smart_shuffle_links(links):
    """
    Shuffles links to maximize distance between same-domain URLs.
    Grouping by domain -> Round Robin interleaving -> Result.
    This helps the parallel downloader avoid hitting the per-domain rate limit (60s).
    """
    import random
    from urllib.parse import urlparse
    
    if not links: return []
    
    # helper
    def get_domain(url):
        try:
            return urlparse(url).netloc
        except:
            return "unknown"

    # Group by domain
    domain_groups = {}
    for link in links:
        domain = get_domain(link)
        if domain not in domain_groups:
            domain_groups[domain] = []
        domain_groups[domain].append(link)
    
    # Shuffle within groups
    for domain in domain_groups:
        random.shuffle(domain_groups[domain])
        
    # Interleave (Round Robin)
    result = []
    keys = list(domain_groups.keys())
    random.shuffle(keys) # Randomize domain order
    
    while keys:
        for domain in list(keys): # Iterate over a copy to allow modification
            if domain_groups[domain]:
                result.append(domain_groups[domain].pop(0))
            else:
                keys.remove(domain)
                
    return result

def add_fooldal_section(all_news):
    """
    Removes 'fooldal' from all items, then randomly selects up to 30 news items
    and adds 'fooldal' to their section. This ensures fresh homepage content each run.
    """
    import random
    
    # First, remove 'fooldal' from all items
    for item in all_news:
        current_section = item.get('section', '')
        if isinstance(current_section, list):
            # Remove fooldal from list if present
            item['section'] = [s for s in current_section if s != 'fooldal']
            # If only one item left, convert back to string
            if len(item['section']) == 1:
                item['section'] = item['section'][0]
            elif len(item['section']) == 0:
                item['section'] = ''
        elif current_section == 'fooldal':
            # If the only section was fooldal, set to empty
            item['section'] = ''
    
    # Now randomly select up to 30 items for fooldal
    valid_items = [item for item in all_news if item.get('section')]  # Items with valid sections
    
    if len(valid_items) <= 30:
        selected = valid_items
    else:
        selected = random.sample(valid_items, 30)
    
    # Add 'fooldal' to selected items
    for item in selected:
        current_section = item.get('section', '')
        if isinstance(current_section, str) and current_section:
            item['section'] = [current_section, 'fooldal']
        elif isinstance(current_section, list) and 'fooldal' not in current_section:
            item['section'].append('fooldal')
    
    print(f"  📰 Assigned {len(selected)} items to fooldal (refreshed)")
    
    return all_news


def get_primary_section(item):
    """Gets the primary (first) section from a news item."""
    section = item.get('section', '')
    if isinstance(section, list) and len(section) > 0:
        return section[0]
    return section if isinstance(section, str) else ''


def randomize_within_sections(all_news):
    """
    Groups news by their primary section and randomizes within each group.
    Returns a list where sections are grouped together, but items within each section are randomized.
    Each news item keeps all its original fields intact.
    """
    import random
    
    # Define section order for consistent output
    section_order = ['fooldal', 'belfold_kulfold', 'tech', 'tudomany', 'uzlet', 'szorakozas', 'gamer', 'kripto', 'eletmod', 'bulvar', 'sport']
    
    # Group news by primary section
    section_groups = {}
    for item in all_news:
        primary_section = get_primary_section(item)
        if primary_section not in section_groups:
            section_groups[primary_section] = []
        section_groups[primary_section].append(item)
    
    # Randomize within each section group
    for section in section_groups:
        random.shuffle(section_groups[section])
    
    # Build result in section order
    result = []
    
    # First add sections in defined order
    for section in section_order:
        if section in section_groups:
            result.extend(section_groups[section])
            del section_groups[section]
    
    # Add any remaining sections not in the order list
    for section in section_groups:
        result.extend(section_groups[section])
    
    print(f"  Randomized news within {len(section_groups) + len([s for s in section_order if s in section_groups or s not in section_groups])} sections")
    
    return result


class ImageUploadManager:
    """Manages image upload state, suspension, and retries."""
    def __init__(self):
        self.lock = threading.Lock()
        self.suspended_until = 0
        self.retry_queue = [] # List of (item, category_file)
        self.permanent_failure = False
        
    def is_suspended(self):
        with self.lock:
            if self.permanent_failure: return True
            return time.time() < self.suspended_until
            
    def suspend(self, duration=3600):
        with self.lock:
            # Only extend if not already suspended longer
            new_end = time.time() + duration
            if new_end > self.suspended_until:
                self.suspended_until = new_end
                print(f"\n🛑 Upload suspended for {duration/60:.0f} minutes (Rate Limit Exhausted).")
                print(f"   Downloading images locally, will retry upload at {datetime.datetime.fromtimestamp(new_end).strftime('%H:%M:%S')}")
                
    def add_retry(self, item, category_file):
        with self.lock:
            # Avoid duplicates? Item is dict, heavy to check.
            self.retry_queue.append((item, category_file))
            
    def should_retry(self):
        with self.lock:
            if not self.retry_queue: return False
            if self.permanent_failure: return False
            return time.time() >= self.suspended_until
            
    def get_retry_item(self):
        with self.lock:
            if self.retry_queue:
                return self.retry_queue.pop(0)
            return None
            
    def set_permanent_failure(self):
        with self.lock:
            self.permanent_failure = True
            print(f"\n❌ Permanent Upload Failure. Stopping all upload attempts.")

    def final_retry(self, output_dir, api_key, result_manager):
        """Attempts to process the entire retry queue at the end, or leaves a flag for the background daemon."""
        with self.lock:
            count = len(self.retry_queue)
        
        if count == 0: return
        
        print(f"\n🔄 initiating FINAL RETRY for {count} pending images...")
        
        wait_time = self.suspended_until - time.time()
        if wait_time > 0:
            print(f"   Uploads are suspended due to rate limits.")
            print(f"   Writing .uploads_suspended flag for background daemon to retry later.")
            try:
                # date folder is basename of output_dir
                date_folder = os.path.basename(os.path.normpath(output_dir))
                with open(".uploads_suspended", "w") as f:
                    f.write(date_folder)
            except Exception as e:
                print(f"   Failed to write suspension flag: {e}")
            return

        # Retry Loop if NOT suspended
        success_count = 0
        still_failed = []
        
        # We process in main thread now
        while True:
            item_tuple = self.get_retry_item()
            if not item_tuple: break
            
            item, category_file = item_tuple
            
            # We already downloaded them locally, so process_single_item 
            # will see the local file and try upload.
            
            # Reconstruct args (Replace api_key with date_folder for Git Repo sync)
            date_folder = os.path.basename(os.path.normpath(output_dir))
            dummy_args = (0, item, count, os.path.join(output_dir, 'Images'), output_dir, date_folder)
            
            # IMPORTANT: Using the imported module function
            import image_downloader
            res = image_downloader.process_single_item(dummy_args)
            
            if res['uploaded']:
                # Update item in ResultManager
                item['image'] = res['new_image_url']
                # Local path cleanup?
                if 'local_image_path' in item: del item['local_image_path']
                result_manager.save_item(item)
                success_count += 1
            elif res.get('upload_exhausted'):
                still_failed.append((item, category_file))
                print("   ❌ Final retry hit rate limit again. Aborting and passing to background daemon.")
                # Write flag for background daemon
                try:
                    date_folder = os.path.basename(os.path.normpath(output_dir))
                    with open(".uploads_suspended", "w") as f:
                        f.write(date_folder)
                except: pass
                break
            else:
                 # Other error
                 still_failed.append((item, category_file))
        
        # Rest of queue
        with self.lock:
             self.retry_queue.extend(still_failed)
        
        if self.retry_queue:
            self.print_manual_hint(api_key)
        else:
            print(f"   ✅ Final retry successful! Uploaded {success_count} images.")

    def print_manual_hint(self, api_key):
        print("\n" + "="*50)
        print(f"⚠️  {len(self.retry_queue)} images could NOT be uploaded.")
        print("💡 The background daemon will automatically retry.")
        print("   If it fails, you can retry uploading them manually later with:")
        print(f"   python3 image_downloader.py --date <YYYY-MM-DD> --key {api_key if api_key else 'YOUR_API_KEY'}")
        print("="*50 + "\n")


def _process_image_item(item, category_file, result_manager, output_dir, imgbb_api_key, upload_manager, worker_id):
    import image_downloader
    
    # 1. Check if suspended
    skip = upload_manager.is_suspended()
    
    # 2. Process
    # Construct args: (i, item, total, images_dir, folder_path, api_key)
    # We use 0 for i/total as it's just logging
    args = (0, item, 0, os.path.join(output_dir, 'Images'), output_dir, imgbb_api_key)
    
    try:
        res = image_downloader.process_single_item(args, skip_upload=skip)
        
        if res['new_image_url']:
            item['image'] = res['new_image_url']
            if 'local_image_path' in item: del item['local_image_path']
            # Save if updated
            result_manager.save_item(item)
            
        elif res.get('upload_exhausted'):
            # Trigger 1h suspension
            upload_manager.suspend(3600)
            # Add to retry queue
            if res.get('local_path'):
                item['local_image_path'] = res['local_path']
            upload_manager.add_retry(item, category_file)
            # Save potential local path update
            result_manager.save_item(item)
            
        elif skip:
            # We skipped upload intentionally
            if res.get('local_path'):
                item['local_image_path'] = res['local_path']
            upload_manager.add_retry(item, category_file)
            # Save local path
            result_manager.save_item(item)
            
        elif res['clear_image']:
             item['image'] = ""
             result_manager.save_item(item)
        else:
             # No image to process (common case) - still save the item!
             result_manager.save_item(item)
             
    except Exception as e:
        print(f"    [ImageWorker-{worker_id}] Error: {e}")
        # If error, maybe retry?
        upload_manager.add_retry(item, category_file)


class LinkManager:
    """
    Manages a priority queue of links to process.
    - Priority 1: Stock/Business ('uzlet') news
    - Priority 2: Processing errors / retries
    - Priority 3: Other categories
    """
    def __init__(self, history, existing_links):
        self.lock = threading.Lock()
        self.queue = []  # List of tuples: (priority, timestamp, category_file, link)
        self.history = history
        self.existing_links = existing_links
        
        # Tracking
        self.total_links = 0
        self.processed_links = 0
        self.failed_links = 0
        
        # Category tracking for "push on completion"
        self.category_counts = {}  # {category: total_count}
        self.category_done = {}    # {category: processed_count}
        self.category_pushed = {}  # {category: bool}
        
        # Track retries to prevent infinite loops
        self.retry_counts = {}
        self.failed_links_by_backend = {}  # {backend_name: [links]}
        
    def add_links(self, links, category_file):
        with self.lock:
            # Determine priority
            priority = 3
            if 'uzlet' in category_file.lower():
                priority = 1
            elif 'pending' in category_file.lower():
                priority = 2
            
            # --- SMART SHUFFLE: Spread domains apart ---
            # Group links by domain, then interleave (Round Robin)
            from urllib.parse import urlparse
            
            def get_domain(url):
                try:
                    return urlparse(url).netloc
                except:
                    return "unknown"
            
            # Group by domain
            domain_groups = {}
            for link in links:
                if link in self.existing_links:
                    continue
                if self.history.is_filtered(link):
                    self.existing_links.add(link)
                    continue
                domain = get_domain(link)
                if domain not in domain_groups:
                    domain_groups[domain] = []
                domain_groups[domain].append(link)
            
            # Shuffle within each domain group
            import random
            for domain in domain_groups:
                random.shuffle(domain_groups[domain])
            
            # Interleave domains (Round Robin)
            shuffled_links = []
            keys = list(domain_groups.keys())
            random.shuffle(keys)  # Randomize domain order
            
            while keys:
                for domain in list(keys):
                    if domain_groups[domain]:
                        shuffled_links.append(domain_groups[domain].pop(0))
                    else:
                        keys.remove(domain)
            
            # Add shuffled links to queue
            count = 0
            for link in shuffled_links:
                self.queue.append((priority, time.time() + count * 0.001, category_file, link))
                self.existing_links.add(link)
                count += 1
            
            # Update stats
            self.total_links += count
            if category_file not in self.category_counts:
                self.category_counts[category_file] = 0
                self.category_done[category_file] = 0
                self.category_pushed[category_file] = False
            self.category_counts[category_file] += count
            
            # Sort queue by priority (asc), then time (asc)
            self.queue.sort(key=lambda x: (x[0], x[1]))
            
            if count > 0:
                print(f"    [LinkManager] Added {count} links ({len(domain_groups)} domains) with smart shuffle")
            
            return count

    def get_batch(self, batch_size=10):
        with self.lock:
            if not self.queue:
                return None, None
            
            batch = []
            category_counts = {}
            
            # Take up to batch_size items
            to_process = self.queue[:batch_size]
            self.queue = self.queue[batch_size:]
            
            links = []
            # We assume a batch should ideally be uniform in category for labeling,
            # but for efficiency we mix them if needed. However, the worker expects 
            # 'category_file' for logging. let's just use "Mixed" or dominant one.
            # Actually, `process_batch` takes `category_file` mostly for logging.
            
            current_category = to_process[0][2]
            
            # Optimization: Try to get items of same category if possible?
            # For strict priority, we just take top N.
            
            for _, _, cat, link in to_process:
                links.append(link)
                # Keep track of category for logging/stats (approximate)
                current_category = cat 
            
            return links, current_category

    def report_success(self, links, category_file):
        with self.lock:
            self.processed_links += len(links)
            for link in links:
                self.existing_links.add(link)
                self.history.update(link, summarized=True)
            
            # Stats update (heuristic if mixed batch, but usually grouped)
            # Since we prioritized, batches are likely homogenous.
            # If mixed, this simple count might be slightly off per category,
            # but 'uzlet' has priority 1 so it fronts the queue and stays together.
            if category_file in self.category_done:
                self.category_done[category_file] += len(links)

    def report_failure(self, links, category_file, retry=True, backend_name="unknown"):
        with self.lock:
            if retry:
                # Filter out links that have been retried too many times
                links_to_retry = []
                links_to_fail = []
                for link in links:
                    count = self.retry_counts.get(link, 0)
                    if count >= 1: # Only allow 1 global requeue, as internal retries handle temporary errors
                        links_to_fail.append(link)
                    else:
                        self.retry_counts[link] = count + 1
                        links_to_retry.append(link)
                
                if links_to_retry:
                    print(f"      [LinkManager] Recycling {len(links_to_retry)} failed links to queue...")
                    for link in links_to_retry:
                        self.queue.insert(0, (2, time.time(), category_file, link))
                    self.queue.sort(key=lambda x: (x[0], x[1]))
                
                if links_to_fail:
                    self._fail_links(links_to_fail, backend_name)
            else:
                self._fail_links(links, backend_name)

    def _fail_links(self, links, backend_name):
        self.failed_links += len(links)
        if backend_name not in self.failed_links_by_backend:
            self.failed_links_by_backend[backend_name] = []
        self.failed_links_by_backend[backend_name].extend(links)
        for link in links:
            self.history.mark_processing_error(link, f"Failed on {backend_name}")

    def is_category_complete(self, category_keyword="uzlet"):
        """Checks if a category is complete and not yet pushed."""
        with self.lock:
            for cat, total in self.category_counts.items():
                if category_keyword in cat.lower():
                    done = self.category_done.get(cat, 0)
                    pushed = self.category_pushed.get(cat, False)
                    # If we have processed all valid links for this category
                    # Note: total might include duplicates we skipped? 
                    # queue logic: we only add unique to queue.
                    if total > 0 and done >= total and not pushed:
                        return True, cat
            return False, None

    def get_completed_unpushed(self):
        """Returns a list of completed categories that haven't been pushed yet."""
        completed = []
        with self.lock:
            for cat, total in self.category_counts.items():
                done = self.category_done.get(cat, 0)
                pushed = self.category_pushed.get(cat, False)
                # Check for completion (allow some margin for skipped/errors?)
                # Actually, done tracks successfully processed.
                # Total tracks queued.
                # If failed links are recycled, they stay in queue.
                # If failed links explicitly failed max retries, they are not 'done' successfully?
                # report_failure increments failed_links.
                # So total should == done + failed.
                failed = 0
                # We don't track failed per category explicitly in a dict (only global self.failed_links).
                # But we can assume if queue is empty of this category, it's done?
                # Hard to track exact queue content by category efficiently without iterating.
                # Simpler metric: if done >= total, it's definitely done.
                if total > 0 and done >= total and not pushed:
                    completed.append(cat)
        return completed

    def mark_category_pushed(self, category):
        with self.lock:
            self.category_pushed[category] = True
    
    def has_work(self):
        with self.lock:
            # Check queue or if there are active retries?
            # actually has_work means queue is not empty.
            return len(self.queue) > 0

    # New stats tracking
    def report_pairing_success(self):
        with self.lock:
            if not hasattr(self, 'stats_pairing_success'): self.stats_pairing_success = 0
            self.stats_pairing_success += 1

    def report_pairing_failure(self, link):
        with self.lock:
            if not hasattr(self, 'stats_pairing_failed'): self.stats_pairing_failed = 0
            if not hasattr(self, 'failed_pairing_links'): self.failed_pairing_links = []
            self.stats_pairing_failed += 1
            self.failed_pairing_links.append(link)


class ResultManager:
    """
    Manages thread-safe saving of data.json and triggers incremental git pushes.
    """
    def __init__(self, output_dir):
        self.lock = threading.Lock()
        self.output_dir = output_dir
        self.data_json_path = os.path.join(output_dir, 'data.json')
        self.items_since_push = 0
        self.push_threshold = 50
        self.total_saved = 0
        self._dirty_count = 0

        # Keep data in memory to avoid read races
        self._data = []
        self._source_links_index = {}  # Fast lookup by sourceLink
        self._titles_index = set()  # For title-based dedup (global across all days)
        
        # Load GLOBAL title index AND source links from historical data files
        # Limit to last 7 days: older articles are very unlikely to reappear in RSS,
        # and scanning 100+ files on every startup causes significant I/O delay.
        # The full-history dedup is handled by history_manager for summarized URLs.
        self._global_source_links = set()
        import glob
        import datetime as _dt
        cutoff = (_dt.date.today() - _dt.timedelta(days=7)).strftime('%Y-%m-%d')
        def _valid_date_folder(path):
            folder = os.path.basename(os.path.dirname(path))
            try:
                _dt.datetime.strptime(folder, '%Y-%m-%d')
                return folder >= cutoff
            except ValueError:
                return False
        all_data_files = sorted(
            f for f in glob.glob('Output/*/data.json')
            if _valid_date_folder(f)
        )
        all_data_files += sorted(
            f for f in glob.glob('Output/*/data_i4.json')
            if _valid_date_folder(f)
        )
        all_data_files += sorted(
            f for f in glob.glob('Output/*/data_i5.json')
            if _valid_date_folder(f)
        )
        for data_file in all_data_files:
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    historical_data = json.load(f)
                    for item in historical_data:
                        # Add processed title
                        title = item.get('title', '')
                        if title:
                            self._titles_index.add(self._normalize_title(title))
                        # Add original title if exists
                        original_title = item.get('originalTitle', '')
                        if original_title:
                            self._titles_index.add(self._normalize_title(original_title))
                        # Add source link (both raw and normalized) for URL-based dedup
                        src_link = item.get('sourceLink', '')
                        if src_link:
                            self._global_source_links.add(src_link.strip())
                            self._global_source_links.add(normalize_url(src_link))
            except Exception:
                pass  # Skip corrupted files
        
        print(f"    [ResultManager] Loaded {len(self._titles_index)} unique titles, {len(self._global_source_links)} source links from {len(all_data_files)} historical files (incl. i4/i5)")
        
        # Load today's data for sourceLink index
        try:
            if os.path.exists(self.data_json_path):
                with open(self.data_json_path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                    # Build sourceLink index (today only)
                    for idx, item in enumerate(self._data):
                        link = item.get('sourceLink', '')
                        if link:
                            self._source_links_index[link] = idx
        except Exception as e:
            print(f"    [ResultManager] Warning: Could not load existing data.json: {e}")
            self._data = []
    
    def is_url_already_processed(self, url):
        """Check if URL already exists in any historical data.json (cross-day dedup)."""
        if not url:
            return False
        url_stripped = url.strip()
        if url_stripped in self._global_source_links:
            return True
        if normalize_url(url_stripped) in self._global_source_links:
            return True
        return False

    def _normalize_title(self, title):
        """Normalize title for comparison (lowercase, strip punctuation)."""
        import re
        if not title:
            return ""
        # Lowercase, remove punctuation, collapse whitespace
        title = title.lower()
        title = re.sub(r'[^\w\s]', '', title)
        title = re.sub(r'\s+', ' ', title).strip()
        return title
    
    def _is_similar_title(self, new_title):
        """Check if a similar title already exists (70% word overlap).
        
        Uses an inverted word index for O(1) candidate lookup instead of O(n) scan.
        """
        if not new_title:
            return False
        normalized = self._normalize_title(new_title)
        
        # 1. Exact match after normalization
        if normalized in self._titles_index:
            return True
        
        # 2. Word overlap check via inverted index
        new_words = set(normalized.split())
        if len(new_words) < 3:
            return False  # Too short for reliable matching
        
        # Build inverted index lazily on first fuzzy call
        if not hasattr(self, '_word_to_titles'):
            self._word_to_titles = {}
            for title in self._titles_index:
                for word in title.split():
                    if word not in self._word_to_titles:
                        self._word_to_titles[word] = set()
                    self._word_to_titles[word].add(title)
        
        # Find candidate titles that share at least one word
        candidates = set()
        for word in new_words:
            if word in self._word_to_titles:
                candidates.update(self._word_to_titles[word])
        
        # Only check overlap against candidates (much smaller than full index)
        for existing in candidates:
            existing_words = set(existing.split())
            if len(existing_words) < 3:
                continue
            overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
            if overlap >= 0.7:
                return True
        return False
    
    def save_item(self, item):
        """Saves a single complete item to data.json and checks push threshold.

        Batches disk writes: flushes every 5 items instead of every single one.
        """
        with self.lock:
            title = item.get('title', '')
            source_link = item.get('sourceLink', '')

            if source_link and source_link in self._source_links_index:
                # Update existing entry (e.g. image added later)
                idx = self._source_links_index[source_link]
                self._data[idx] = item
            else:
                # New item: title-dedup check before appending
                if self._is_similar_title(title):
                    return False

                self._data.append(item)
                if source_link:
                    self._source_links_index[source_link] = len(self._data) - 1

                normalized_title = self._normalize_title(title)
                if normalized_title:
                    self._titles_index.add(normalized_title)
                    if hasattr(self, '_word_to_titles'):
                        for word in normalized_title.split():
                            self._word_to_titles.setdefault(word, set()).add(normalized_title)

            self.total_saved = len(self._data)
            self.items_since_push += 1

            self._dirty_count += 1

            if self._dirty_count >= 5:
                self._flush_to_disk()

            if self.items_since_push >= self.push_threshold:
                self._flush_to_disk()
                self.trigger_push()

            return True
    
    def _flush_to_disk(self):
        """Write in-memory data to disk. Must be called with self.lock held."""
        try:
            with open(self.data_json_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            self._dirty_count = 0
        except Exception as e:
            print(f"⚠️ [ResultManager] Disk flush failed: {e}")
                
    def trigger_push(self):
        """Triggers a git push for data.json."""
        print(f"\n🚀 [Incremental Push] Threshold reached ({self.items_since_push} new items). Pushing data.json...")
        try:
            import subprocess
            subprocess.run(["git", "add", self.data_json_path], check=True)
            subprocess.run(["git", "commit", "-m", f"Auto-update: data.json ({self.total_saved} total)"], check=False)
            subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)

            subprocess.run(["git", "push"], check=True)
            print("✅ [Incremental Push] Success!")
            self.items_since_push = 0 # Reset counter
        except Exception as e:
            print(f"⚠️ [Incremental Push] Failed: {e}")


def batch_worker(worker_id, backend_name, link_manager, result_manager, image_queue, api_key, prompt_template, 
                 use_gemini=False, use_geminipro=False, use_perplexity=False,
                 use_gemini_cookie=False, use_gemini_api=False, gemini_api_key=None, use_geminipro_cli=False,
                 use_g4f=False, use_deeperseek=False, use_multi_model=False, use_gemini_selenium=False, use_free_gemini_api=False, key_index=None, use_lmstudio_local=False, use_lmstudio_remote=False):
    """
    Worker thread that requests matches from LinkManager, summarizes them, 
    and sends them to the image_queue for parallel processing.
    """
    import time
    
    while link_manager.has_work():
        # Request work - Maximize Gemini Pro to 7 links per batch
        current_batch_size = 7 if (use_geminipro or "Gemini" in backend_name) else BATCH_SIZE
        batch_links, category_file = link_manager.get_batch(batch_size=current_batch_size)
        
        if not batch_links:
            time.sleep(1)
            continue
            
        print(f"    [{backend_name}] Processing batch of {len(batch_links)} links from {category_file}...")
        
        # Download content first
        downloaded_items = []
        failed_download_links = []
        skipped_duplicate_titles = 0
        
        for url in batch_links:
            try:
                data = article_downloader.download_article(url)
                if data and data.get('text') and len(data['text'].strip()) > 50:
                    # PRE-SUMMARIZATION TITLE CHECK
                    article_title = data.get('title', '')
                    if article_title and result_manager._is_similar_title(article_title):
                        print(f"      [{backend_name}] SKIPPED (similar title exists): {article_title[:40]}...")
                        link_manager.history.update(url, status='FILTERED')
                        skipped_duplicate_titles += 1
                        continue
                    
                    # Store original title for later injection into result
                    data['originalTitle'] = article_title
                    
                    # Add to title index to prevent intra-batch duplicates
                    if article_title:
                        result_manager._titles_index.add(result_manager._normalize_title(article_title))
                    
                    downloaded_items.append(data)
                else:
                    failed_download_links.append(url)
            except Exception:
                failed_download_links.append(url)
        
        if skipped_duplicate_titles > 0:
            print(f"    [{backend_name}] Filtered {skipped_duplicate_titles} articles with duplicate titles before summarization")
        
        # Determine actual batch to process
        if not downloaded_items:
             # All failed or empty
             if failed_download_links:
                 print(f"    [{backend_name}] All {len(failed_download_links)} links failed download.")
                 link_manager.report_failure(failed_download_links, category_file, retry=False, backend_name=backend_name)
             continue

        # Process downloaded items
        try:
            news_list, failed_processing_links, filtered_links, model_used = process_batch(
                api_key, prompt_template, downloaded_items, category_file,
                use_gemini=use_gemini, use_geminipro=use_geminipro, use_perplexity=use_perplexity,
                use_gemini_cookie=use_gemini_cookie, use_gemini_api=use_gemini_api, gemini_api_key=gemini_api_key, use_gemini_selenium=use_gemini_selenium,
                use_geminipro_cli=use_geminipro_cli, use_g4f=use_g4f, use_deeperseek=use_deeperseek,
                use_multi_model=use_multi_model, use_free_gemini_api=use_free_gemini_api, key_index=key_index, use_lmstudio_local=use_lmstudio_local, use_lmstudio_remote=use_lmstudio_remote
            )
            
            # Mark filtered links as FILTERED (don't retry)
            for link in filtered_links:
                link_manager.history.update(link, status='FILTERED')
            
            # Combine failures (exclude filtered from retry)
            failed_links = failed_download_links + failed_processing_links
            
            # Handle Success
            if news_list:
                # ----------------------------------------------------
                # CRITICAL FIX: Robust Pairing (Refactored Helper)
                # ----------------------------------------------------
                news_list = pair_news_to_batch(news_list, downloaded_items, backend_name, link_manager)
                
                for item in news_list:
                    
                    # Pass to Image Worker Queue
                    image_queue.put((item, category_file, model_used))
                
                success_links = [item['sourceLink'] for item in news_list]
                link_manager.report_success(success_links, category_file)
                
                # Immediate Push for Stock still applies? 
                # User said: "If at least 50 news... push". 
                # But also "For the news... immediately start... image download".
                # The "Stock Push" requirement from before might be superseded or complementary.
                # Let's keep the Stock Push logic in LinkManager but maybe it should just call result_manager.trigger_push()?
                # Actually, better to let ResultManager handle all pushes based on threshold.
                # However, user explicitly asked for "Generate weather and market first... push to git". That's Phase 1.
                # The "Stock" logic inside summarizer might be redundant for Phase 1 if market_gen handles it.
                # But 'uzlet' category in summarizer is different from 'market_generator'.
                # Let's keep 'uzlet' priority but rely on ResultManager's threshold for pushing 'data.json'.
            
            if failed_links:
                 print(f"    [{backend_name}] {len(failed_links)} links failed -> Recycling")
                 link_manager.report_failure(failed_links, category_file, retry=True, backend_name=backend_name)
                 
                 if len(failed_links) == len(batch_links):
                     print(f"    ⚠️ [{backend_name}] FULL BATCH FAILURE. Taking a short nap (30s)...")
                     time.sleep(30)
            
        except Exception as e:
            print(f"    [{backend_name}] CRITICAL WORKER ERROR: {e}")
            link_manager.report_failure(batch_links, category_file, retry=True, backend_name=backend_name)
            time.sleep(5)

    print(f"    [{backend_name}] Worker finished.")


def summarizer_worker(worker_id, backend_name, link_manager, article_buffer, result_manager, image_queue, 
                      history, api_key, prompt_template,
                      use_gemini=False, use_geminipro=False, use_perplexity=False,
                      use_gemini_cookie=False, use_gemini_api=False, gemini_api_key=None, 
                      use_geminipro_cli=False, use_g4f=False, use_deeperseek=False, 
                      use_multi_model=False, use_gemini_selenium=False, use_free_gemini_api=False, key_index=None, use_lmstudio_local=False, use_lmstudio_remote=False):
    """
    Worker thread that consumes articles from ArticleBuffer and summarizes them.
    Part of the producer-consumer architecture:
    - Producer: DownloadWorkers
    - Consumer: This function (SummarizerWorker)
    """
    import time
    
    consecutive_empty = 0
    max_empty_before_exit = 10  # Exit after 10 consecutive empty batches (50s total)
    
    while True:
        # Check for Free Gemini Pool exhaustion
        if use_free_gemini_api:
            # Import locally to avoid circular dependencies if used elsewhere
            import gemini_api_pool
            if not gemini_api_pool.check_available():
                print(f"    [{backend_name}] ⚠️ Free Gemini Pool exhausted! Saving state and exiting worker.")
                break

        # Get batch from buffer
        current_batch_size = 7 if (use_geminipro or "Gemini" in backend_name) else BATCH_SIZE
        batch = article_buffer.get_batch(size=current_batch_size, timeout=5)
        
        if not batch:
            consecutive_empty += 1
            
            # Check if downloads are complete and buffer is empty
            if article_buffer.is_complete():
                print(f"    [{backend_name}] Downloads complete, buffer empty. Exiting.")
                break
            
            # If we've waited too long, exit
            if consecutive_empty >= max_empty_before_exit:
                print(f"    [{backend_name}] No articles for {consecutive_empty * 5}s. Exiting.")
                break
            
            # Wait a bit more
            time.sleep(2)
            continue
        
        # Reset empty counter when we get work
        consecutive_empty = 0
        
        print(f"    [{backend_name}] Processing batch of {len(batch)} articles from buffer...")
        
        # Process the batch
        try:
            news_list, failed_processing_links, filtered_links, model_used = process_batch(
                api_key, prompt_template, batch, "buffer",
                use_gemini=use_gemini, use_geminipro=use_geminipro, use_perplexity=use_perplexity,
                use_gemini_cookie=use_gemini_cookie, use_gemini_api=use_gemini_api, 
                gemini_api_key=gemini_api_key, use_gemini_selenium=use_gemini_selenium,
                use_geminipro_cli=use_geminipro_cli, use_g4f=use_g4f, use_deeperseek=use_deeperseek,
                use_multi_model=use_multi_model, use_free_gemini_api=use_free_gemini_api, key_index=key_index, use_lmstudio_local=use_lmstudio_local, use_lmstudio_remote=use_lmstudio_remote
            )
            
            # Mark filtered links as FILTERED
            for link in filtered_links:
                history.update(link, status='FILTERED')
            
            # Handle success
            if news_list:
                # ----------------------------------------------------
                # CRITICAL FIX: Map results back to original items by URL
                # Do NOT assume index order matches (LLM might filter/reorder)
                # ----------------------------------------------------
                
                # ----------------------------------------------------
                # CRITICAL FIX: Robust Pairing (Refactored Helper)
                # ----------------------------------------------------
                news_list = pair_news_to_batch(news_list, batch, backend_name, link_manager)
                
                
                for item in news_list:
                    # Pass to Image Worker Queue (only if enabled)
                    if image_queue:
                        image_queue.put((item, "buffer", model_used))
                
                # Mark as summarized
                for item in news_list:
                    history.update(item.get('sourceLink', ''), summarized=True)
                
                print(f"    [{backend_name}] ✅ Summarized {len(news_list)} articles")
            
            # Handle failures - mark in history
            for link in failed_processing_links:
                history.mark_processing_error(link, "Summarization failed")
            
        except Exception as e:
            print(f"    [{backend_name}] CRITICAL ERROR: {e}")
            # Mark all batch items as failed
            for item in batch:
                history.mark_processing_error(item.get('url', ''), str(e)[:100])
            time.sleep(5)
        
        # Ensure task_done is called for all items
        article_buffer.task_done(len(batch))
    
    print(f"    [{backend_name}] Summarizer worker finished.")


def image_worker(worker_id, image_queue, result_manager, output_dir, imgbb_api_key, upload_manager):
    """
    Worker thread that consumes summarized items, downloads/uploads images,
    and saves to ResultManager.
    """
    import time
    import queue
    
    images_dir = os.path.join(output_dir, 'Images')
    os.makedirs(images_dir, exist_ok=True)
    
    while True:
        try:
            # Try to get new work
            try:
                # Short timeout to allow checking retry queue
                work = image_queue.get(timeout=2)
                item, category_file, model_used = work
                _process_image_item(item, category_file, result_manager, output_dir, imgbb_api_key, upload_manager, worker_id)
                image_queue.task_done()
            except queue.Empty:
                pass
                
            # Check retry queue if idle or periodically?
            if upload_manager.should_retry():
                 retry = upload_manager.get_retry_item()
                 if retry:
                     item, category_file = retry
                     print(f"    [ImageWorker-{worker_id}] 🔄 Retrying upload for: {item.get('title', 'Unknown')[:20]}")
                     _process_image_item(item, category_file, result_manager, output_dir, imgbb_api_key, upload_manager, worker_id)
        
        except Exception as e:
            # print(f"    [ImageWorker-{worker_id}] Idle/Error: {e}")
            pass



def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Summarize news articles using AI")
    parser.add_argument('--use-gemini', action='store_true', help="Use Gemini (Legacy Cookie Fallback)")
    parser.add_argument('--use-geminipro', action='store_true', help="Use Gemini 3 Pro (Cookie)")
    parser.add_argument('--use-gemini-cookie', action='store_true', help="Use Gemini (Cookie Fallback)")
    parser.add_argument('--use-gemini-api', action='store_true', help="Use Gemini API Key")
    parser.add_argument('--use-geminipro-cli', action='store_true', help="Use Gemini Pro CLI")
    parser.add_argument('--use-perplexity', action='store_true', help="Use Perplexity Pro API")
    parser.add_argument('--use-g4f', action='store_true', help="Use GPT4Free (free providers)")
    parser.add_argument('--use-deeperseek', action='store_true', help="Use DeeperSeek (DeepSeek free API)")
    parser.add_argument('--gemini-selenium', action='store_true', help="Use Gemini Selenium Automation")
    parser.add_argument('--multi-model', action='store_true', help="Use all backends with auto-failover")
    parser.add_argument('--gemini-api-key', type=str, help="Gemini API Key")
    parser.add_argument('--push', action='store_true', help="Git push after each file")
    parser.add_argument('--workers', type=int, default=1, help="Number of parallel workers (default: 1)")
    parser.add_argument('--gemini-api-key-index', type=int, default=None, help="Index of Gemini API key to use (0-indexed)")
    parser.add_argument('--use-free-gemini-api', action='store_true', help="Use Free Gemini API Pool (with rotation)")
    parser.add_argument('--use-lmstudio-local', action='store_true', help="Use LM Studio Local endpoint")
    parser.add_argument('--use-lmstudio-remote', action='store_true', help="Use LM Studio Remote endpoint")
    parser.add_argument('--debug-pairing', action='store_true', help="Enable debug pairing mode (stop on failure)")
    parser.add_argument('--skip-images', action='store_true', help="Skip image downloading and uploading entirely")
    
    args = parser.parse_args()
    
    global DEBUG_PAIRING_MODE
    DEBUG_PAIRING_MODE = args.debug_pairing

    use_gemini = args.use_gemini
    use_geminipro = args.use_geminipro
    use_geminipro_cli = args.use_geminipro_cli
    use_gemini_cookie = args.use_gemini_cookie or use_gemini
    use_gemini_api = args.use_gemini_api
    use_perplexity = args.use_perplexity
    use_g4f = args.use_g4f
    use_deeperseek = args.use_deeperseek
    use_gemini_selenium = args.gemini_selenium
    use_multi_model = args.multi_model
    gemini_api_key = args.gemini_api_key
    use_free_gemini_api = args.use_free_gemini_api
    
    # Load config to get API key if not passed
    api_key_conf, prompt_template, gemini_key_conf = load_config()
    if not gemini_api_key and gemini_key_conf:
        gemini_api_key = gemini_key_conf

    # Build list of active backends for parallel processing
    # Format: (backend_name, kwargs_dict)
    active_backends = []
    
    if use_perplexity:
        if not perplexity_client.check_cookies():
            print("⚠️ Perplexity cookies invalid, skipping this backend")
        else:
            perplexity_client.reset_stats()
            active_backends.append(("Perplexity", {'use_perplexity': True}))
    
    if use_geminipro:
        if not geminipro_client.check_cookies():
            print("⚠️ Gemini Pro cookies invalid, skipping this backend")
        else:
            geminipro_client.reset_stats()
            active_backends.append(("GeminiPro", {'use_geminipro': True}))
    
    if use_gemini_cookie:
        # Note: GeminiPro client handles both Pro and Flash/Cookie fallback
        active_backends.append(("GeminiCookie", {'use_gemini_cookie': True}))

    if use_gemini_api:
        active_backends.append(("GeminiAPI", {'use_gemini_api': True, 'gemini_api_key': gemini_api_key}))
    
    if use_geminipro_cli:
        if not geminipro_cli_client.check_gemini_installed():
             print("⚠️ Gemini CLI not found, skipping this backend")
        else:
             active_backends.append(("GeminiCLI", {'use_geminipro_cli': True}))
    
    if use_g4f:
        if not g4f_client.check_cookies():
             print("⚠️ GPT4Free not available, skipping this backend")
        else:
             g4f_client.reset_stats()
             active_backends.append(("G4F", {'use_g4f': True}))
    
    if use_deeperseek:
        if not deeperseek_client.check_cookies():
             print("⚠️ DeeperSeek not available, skipping this backend")
        else:
             deeperseek_client.reset_stats()
             active_backends.append(("DeeperSeek", {'use_deeperseek': True}))

    if use_gemini_selenium:
        import gemini_selenium_client
        if not gemini_selenium_client.check_cookies():
             print("⚠️ Gemini Selenium not available (check failed), skipping")
        else:
             active_backends.append(("GeminiSelenium", {'gemini_selenium': True}))
    
    if use_free_gemini_api:
        # Check if keys are available
        import gemini_api_pool
        if gemini_api_pool.check_available():
            active_backends.append(("FreeGeminiAPI", {'use_free_gemini_api': True}))
        else:
            print("⚠️ Free Gemini API Pool exhausted or empty, skipping this backend")
    
    # Load settings
    settings_path = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')
    enabled_backends = []
    fallback_backend_name = "none"
    try:
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                settings = json.load(f)
                enabled_backends = settings.get('multi_model', {}).get('enabled_backends', [])
                fallback_backend_name = settings.get('multi_model', {}).get('fallback_backend', 'none')
    except Exception as e:
        print(f"  ⚠️ Failed to load settings: {e}")
        enabled_backends = ["perplexity", "gemini-selenium"]

    # Map backend names to active_backends format
    # NOTE: Both underscore and hyphen variants exist due to menu inconsistencies
    backend_map = {
        "perplexity": ("Perplexity", {'use_perplexity': True}),
        "gemini-selenium": ("GeminiSelenium", {'gemini_selenium': True}),
        "gemini_selenium": ("GeminiSelenium", {'gemini_selenium': True}),
        "deeperseek": ("DeeperSeek", {'use_deeperseek': True}),
        "g4f": ("G4F", {'use_g4f': True}),
        "free_gemini_api": ("FreeGeminiAPI", {'use_free_gemini_api': True}),
        "free-gemini-api": ("FreeGeminiAPI", {'use_free_gemini_api': True}),
        "gemini_api": ("GeminiAPI", {'use_gemini_api': True}),
        "gemini-api": ("GeminiAPI", {'use_gemini_api': True}),
        "geminipro": ("GeminiPro", {'use_geminipro': True}),
        "geminipro-cli": ("GeminiCLI", {'use_geminipro_cli': True}),
        "geminipro_cli": ("GeminiCLI", {'use_geminipro_cli': True}),
        "lmstudio-local": ("LMStudioLocal", {'use_lmstudio_local': True}),
        "lmstudio-remote": ("LMStudioRemote", {'use_lmstudio_remote': True}),
    }

    if use_multi_model:
        # Multi-model mode: spawn separate parallel workers for each enabled backend
        print("🤖 Using Multi-Model Mode (PARALLEL workers per backend)")
        print(f"  📋 Enabled backends: {', '.join(enabled_backends)}")
        
        for backend_key in enabled_backends:
            if backend_key in backend_map:
                active_backends.append(backend_map[backend_key])
                print(f"    ✅ Added worker: {backend_map[backend_key][0]}")
            else:
                print(f"    ⚠️ Unknown backend: {backend_key}")
    
    # Default to Gemini API if no backends selected and no flags provided
    if not active_backends and not (use_gemini or use_geminipro or use_perplexity or use_gemini_cookie or use_gemini_api or use_geminipro_cli or use_g4f or use_deeperseek or use_multi_model):
        active_backends.append(("GeminiAPI", {'use_gemini_api': True, 'gemini_api_key': gemini_api_key}))
    
    # Report mode
    if len(active_backends) >= 2:
        backend_names = [b[0] for b in active_backends]
        print(f"🚀 MULTI-BACKEND MODE: {' + '.join(backend_names)}")
    elif active_backends:
        print(f"🤖 Using {active_backends[0][0]} API")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not api_key_conf or not prompt_template:
        return

    history = HistoryManager()
    history.cleanup_old_entries(days=60)  # prune stale non-summarized entries on every run
    links_file_root = 'links.txt'
    links_file_out = os.path.join(OUTPUT_DIR, 'links.txt')
    
    files = []
    if os.path.exists(links_file_root):
        print(f"Found links.txt in root directory.")
        files.append(os.path.abspath(links_file_root))
    
    if os.path.exists(links_file_out):
        # Avoid duplicate if root and out are same (unlikely but safe)
        abs_out = os.path.abspath(links_file_out)
        if abs_out not in files:
            print(f"Found links.txt in {OUTPUT_DIR}...")
            files.append(abs_out)
    
    if not files:
        print(f"No links.txt found (checked root and {OUTPUT_DIR}).")

    # Load existing data.json for resume capability
    output_path = os.path.join(OUTPUT_DIR, 'data.json')
    existing_news = []
    existing_links = set()
    
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                existing_news = json.load(f)
            for item in existing_news:
                link = item.get('sourceLink', '')
                if link:
                    existing_links.add(link)
                    existing_links.add(normalize_url(link))
            print(f"Loaded {len(existing_news)} existing items from data.json for resume")
            
            # Sync: Mark existing links as summarized in history to prevent redundant API calls
            synced_count = 0
            for link in existing_links:
                if link and not history.is_summarized(link):
                    # Batch update: modify in-memory dict without saving each time
                    norm_link = history.normalize_url(link)
                    if norm_link not in history.history:
                        history.history[norm_link] = {
                            'first_seen': __import__('datetime').datetime.now().isoformat(),
                            'status': 'UNKNOWN',
                            'summarized': True
                        }
                    else:
                        history.history[norm_link]['summarized'] = True
                        history.history[norm_link]['last_updated'] = __import__('datetime').datetime.now().isoformat()
                    synced_count += 1
            if synced_count > 0:
                history.save()  # Single write instead of one per link
                print(f"Synced {synced_count} links from data.json to history (marked as summarized)")
                
        except Exception as e:
            print(f"Warning: Could not load existing data.json: {e}")
    
    # Load pending links from history (unsummarized POSITIVE links)
    pending_links = []
    for url, data in history.history.items():
        if not data.get('summarized', False):
            status = data.get('status', '')
            if status in ['POSITIVE', 'PROCESSING_ERROR', 'UNKNOWN', '']:
                if url not in existing_links:
                    pending_links.append(url)
    
    if pending_links:
        print(f"📋 Found {len(pending_links)} pending links from previous runs to retry")
    
    # Collect all news from all files
    all_news = list(existing_news)  # Start with existing
    new_items_count = 0
    
    # ============================================
    # PARALLEL PROCESSING MODE
    # ============================================
    if len(active_backends) >= 2 or args.workers > 1:
        print(f"\n🚀 Starting parallel processing with {len(active_backends)} backends and {args.workers} workers...")
        
        # Initialize Managers
        link_manager = LinkManager(history, existing_links)
        result_manager = ResultManager(OUTPUT_DIR)
        
        # Only initialize upload manager if images are enabled
        upload_manager = None
        if not args.skip_images:
            upload_manager = ImageUploadManager()
        
        # Populate LinkManager with links from all files
        total_queued = 0
        for filename in files:
            if os.path.exists(filename):
                input_path = filename
            elif filename == 'links.txt' and os.path.exists('links.txt'):
                 input_path = 'links.txt'
            else:
                 input_path = os.path.join(OUTPUT_DIR, filename)
            try:
                with open(input_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"Error reading {filename}: {e}")
                continue
            
            # Extract clean links (now just raw links, one per line)
            links = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Handle both old format [Category][link] and new format (raw link)
                if '][' in line:
                    try:
                        parts = line.split('][')
                        if len(parts) >= 2:
                            link = parts[1].replace(']', '')
                            if link not in existing_links and not history.is_summarized(link):
                                links.append(link)
                    except (IndexError, ValueError):
                        continue
                elif line.startswith('[') and '](' in line:
                    # Markdown link: [text](url)
                    try:
                        link = line.split('](')[1].rstrip(')')
                        if link not in existing_links and not history.is_summarized(link):
                             links.append(link)
                    except (IndexError, ValueError):
                         pass
                elif line.startswith('http'):
                    # New format: raw link
                    # Fix: Handle markdown-like artifacts (e.g. trailing parenthesis)
                    if line.endswith(')'):
                        line = line.rstrip(')')
                    
                    if line not in existing_links and not history.is_summarized(line) and not result_manager.is_url_already_processed(line):
                        links.append(line)
            
            if links:
                count = link_manager.add_links(links, filename)
                total_queued += count
        
        # Add pending links
        if pending_links:
            pending_to_process = [url for url in pending_links if url not in existing_links]
            if pending_to_process:
                link_manager.add_links(pending_to_process, "pending_retry")
                total_queued += len(pending_to_process)
        
        print(f"  Total links queued: {total_queued}")
        
        if total_queued > 0:
            # Stats for Image Processing
            imgbb_api_key = os.environ.get('IMGBB_API_KEY')
            if not imgbb_api_key:
                # Try to load from input.txt if not env
                try:
                    with open(os.path.join(INPUT_DIR, 'input.txt'), 'r') as f:
                        for line in f:
                            if 'IMGBB_API_KEY' in line:
                                imgbb_api_key = line.split('=')[1].strip()
                except: pass
            
            # Create Queue for Summarizer -> Image Worker
            image_queue = queue.Queue()
            
            # Start Image Workers (e.g. 5 threads)
            image_threads = []
            for i in range(5):
                t = threading.Thread(
                    target=image_worker,
                    args=(i, image_queue, result_manager, OUTPUT_DIR, imgbb_api_key, upload_manager)
                )
                t.daemon = True
                t.start()
                image_threads.append(t)
            print(f"  Started 5 Image Processing threads")
            
            # ============================================
            # PRODUCER-CONSUMER ARCHITECTURE
            # ============================================
            
            # Create shared queues and buffers
            download_queue = queue.Queue()
            article_buffer = ArticleBuffer()
            
            # Collect all URLs to download (from LinkManager queue)
            all_urls_to_download = []
            skipped_historical = 0
            with link_manager.lock:
                for item in link_manager.queue:
                    # Queue items are tuples: (priority, timestamp, category_file, link)
                    link = item[3]
                    # Skip if already in historical data (cross-day dedup)
                    if result_manager.is_url_already_processed(link):
                        skipped_historical += 1
                        continue
                    # Skip if already summarized or too many failures
                    if not history.is_summarized(link) and history.get_failure_count(link) < 2:
                        all_urls_to_download.append(link)
            
            if skipped_historical > 0:
                print(f"  📋 Skipped {skipped_historical} URLs already in historical data (cross-day dedup)")
            
            print(f"\n📥 PRODUCER-CONSUMER MODE: {len(all_urls_to_download)} URLs to download")
            
            # Add all URLs to download queue
            for url in all_urls_to_download:
                download_queue.put(url)
            
            # Start Download Workers (10 parallel threads)
            download_threads = []
            for i in range(DOWNLOAD_WORKERS):
                worker = DownloadWorker(i, download_queue, article_buffer, history, result_manager)
                worker.start()
                download_threads.append(worker)
            print(f"  Started {DOWNLOAD_WORKERS} Download Worker threads")
            
            # Start Playwright Worker for 403 retries (runs in parallel)
            article_downloader.init_playwright_queue()
            playwright_worker = article_downloader.PlaywrightWorker(article_buffer, history)
            playwright_worker.start()
            print(f"  Started PlaywrightWorker for 403 fallback")
            
            # Start Summarizer Worker threads
            summarizer_threads = []
            
            if len(active_backends) == 1 and args.workers > 1:
                # Spawn multiple workers for the single backend
                backend_name, kwargs = active_backends[0]
                print(f"  Spawning {args.workers} Summarizer workers for {backend_name}...")
                for i in range(args.workers):
                    t = threading.Thread(
                        target=summarizer_worker,
                        args=(i, f"{backend_name}_{i+1}", link_manager, article_buffer, result_manager, 
                              image_queue, history, api_key_conf, prompt_template),
                        kwargs={
                            'use_gemini': kwargs.get('use_gemini', False),
                            'use_geminipro': kwargs.get('use_geminipro', False),
                            'use_perplexity': kwargs.get('use_perplexity', False),
                            'use_gemini_cookie': kwargs.get('use_gemini_cookie', False),
                            'use_gemini_api': kwargs.get('use_gemini_api', False),
                            'gemini_api_key': kwargs.get('gemini_api_key', None),
                            'use_geminipro_cli': kwargs.get('use_geminipro_cli', False),
                            'use_g4f': kwargs.get('use_g4f', False),
                            'use_deeperseek': kwargs.get('use_deeperseek', False),
                            'use_multi_model': kwargs.get('use_multi_model', False),
                            'use_gemini_selenium': kwargs.get('gemini_selenium', False),
                            'use_free_gemini_api': kwargs.get('use_free_gemini_api', False),
                            'key_index': args.gemini_api_key_index,
                            'use_lmstudio_local': kwargs.get('use_lmstudio_local', False),
                            'use_lmstudio_remote': kwargs.get('use_lmstudio_remote', False)
                        }
                    )
                    t.daemon = True
                    t.start()
                    summarizer_threads.append(t)
                    if i < args.workers - 1:
                        print(f"      Waiting 30s before starting next summarizer...")
                        time.sleep(30)
            else:
                # Multiple backends: 1 worker per backend
                for idx, (backend_name, kwargs) in enumerate(active_backends):
                    t = threading.Thread(
                        target=summarizer_worker,
                        args=(idx, backend_name, link_manager, article_buffer, result_manager, 
                              image_queue, history, api_key_conf, prompt_template),
                        kwargs={
                            'use_gemini': kwargs.get('use_gemini', False),
                            'use_geminipro': kwargs.get('use_geminipro', False),
                            'use_perplexity': kwargs.get('use_perplexity', False),
                            'use_gemini_cookie': kwargs.get('use_gemini_cookie', False),
                            'use_gemini_api': kwargs.get('use_gemini_api', False),
                            'gemini_api_key': kwargs.get('gemini_api_key', None),
                            'use_geminipro_cli': kwargs.get('use_geminipro_cli', False),
                            'use_g4f': kwargs.get('use_g4f', False),
                            'use_deeperseek': kwargs.get('use_deeperseek', False),
                            'use_multi_model': kwargs.get('use_multi_model', False),
                            'use_gemini_selenium': kwargs.get('gemini_selenium', False),
                            'use_free_gemini_api': kwargs.get('use_free_gemini_api', False),
                            'key_index': args.gemini_api_key_index,
                            'use_lmstudio_local': kwargs.get('use_lmstudio_local', False),
                            'use_lmstudio_remote': kwargs.get('use_lmstudio_remote', False)
                        }
                    )
                    t.daemon = True
                    t.start()
                    summarizer_threads.append(t)
                    print(f"  Started {backend_name} Summarizer Worker thread")
            
            # Wait for downloads to complete
            download_queue.join()
            print(f"\n📥 All downloads queued for processing")
            
            # Send poison pills to download workers
            for _ in range(DOWNLOAD_WORKERS):
                download_queue.put(None)
            
            # Wait for download workers to finish
            for worker in download_threads:
                worker.join(timeout=10)
            
            # Stop Playwright worker gracefully
            playwright_worker.stop()
            playwright_worker.join(timeout=30)
            if playwright_worker.is_alive():
                print(f"  ⚠️ PlaywrightWorker did not stop within 30s, continuing anyway")
            
            # Signal article buffer that downloads are complete
            article_buffer.mark_download_complete()
            stats = article_buffer.get_stats()
            print(f"📊 Download stats: {stats['downloaded']} downloaded, {stats['failed']} failed")
            
            # Use summarizer_threads instead of threads for the rest of the logic
            threads = summarizer_threads
            
            # Monitoring loop
            # time is already imported globally
            while article_buffer.has_pending() or not image_queue.empty() or any(t.is_alive() for t in threads):
                
                # Check for completed categories to push
                if args.push:
                    completed_cats = link_manager.get_completed_unpushed()
                    for cat in completed_cats:
                        print(f"\n🚀 [Parallel Push] Category '{cat}' complete! Triggering push...")
                        
                        # We trigger simple push of data.json. 
                        # Ideally result_manager.trigger_push() handles data.json commits.
                        # But user wants specific file-like completion.
                        # result_manager.trigger_push() commits with "Auto-update: data.json (N total)".
                        # We can manually trigger a push with a custom message here.
                        try:
                            # Flush ResultManager if needed? It writes immediately.
                            path = result_manager.data_json_path
                            subprocess.run(["git", "add", path], check=True)
                            subprocess.run(["git", "commit", "-m", f"Auto-update: {cat} (Complete)"], check=False)
                            subprocess.run(["git", "push"], check=True)
                            print(f"✅ [Parallel Push] Success for {cat}!")
                            link_manager.mark_category_pushed(cat)
                        except Exception as e:
                            print(f"⚠️ [Parallel Push] Failed: {e}")
                
                
                # Check if all summarizer threads have died while work remains
                alive_threads = [t for t in threads if t.is_alive()]
                if not alive_threads:
                    if article_buffer.has_pending():
                        print(f"\n⚠️ All {len(threads)} summarizer workers have stopped unexpectedly (likely due to key exhaustion). Stopping main loop.")
                        break
                
                time.sleep(5)
            
            # Wait for Summary threads
            for t in threads:
                t.join()
            
            # Wait for Image Queue to drain
            image_queue.join()
            
            # Signal Image workers to stop (optional, they are daemon)
            # Since threads are daemon, we can just finish
            
            print("  All processing complete (Round 1)!")
            
            # ============================================
            # RETRY LOGIC (Max 2 Rounds)
            # ============================================
            MAX_RETRIES = 2
            
            for retry_round in range(MAX_RETRIES):
                # 1. Identify missing links
                print(f"\n🔍 Checking for missing summaries (Retry Round {retry_round + 1})...")
                
                # Check data.json content
                current_data = []
                try:
                    if os.path.exists(output_path):
                        with open(output_path, 'r', encoding='utf-8') as f:
                            current_data = json.load(f)
                except: pass
                
                processed_links = set()
                for item in current_data:
                    lnk = item.get('sourceLink', '')
                    # Handle markdown [url](url)
                    if '(' in lnk and ')' in lnk:
                         try:
                             processed_links.add(lnk.split('(')[1].split(')')[0].strip())
                         except:
                             processed_links.add(lnk.strip())
                    else:
                         processed_links.add(lnk.strip())

                # Check all source files
                missing_links = []
                for filename in files:
                    fpath = os.path.join(OUTPUT_DIR, filename)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            for line in f:
                                if '][' in line:
                                    try:
                                        parts = line.split('][')
                                        if len(parts) >= 2:
                                            url = parts[1].replace(']', '').strip()
                                            if url not in processed_links and not history.is_summarized(url):
                                                 missing_links.append(url)
                                    except: pass
                    except: pass
                
                if not missing_links:
                    print("✅ No missing summaries found.")
                    break
                
                print(f"⚠️ Found {len(missing_links)} missing links! Restarting workers for Retry Round {retry_round + 1}...")
                
                # 2. Re-queue missing links
                link_manager.add_links(missing_links, f"retry_round_{retry_round+1}")
                
                # --- PER-LLM FAILOVER INJECTION ---
                failover_settings = {}
                try:
                    with open(settings_path, 'r') as f:
                        s = json.load(f)
                        failover_settings = s.get('multi_model', {}).get('failover_backends', {})
                except: pass
                
                unmapped_links = missing_links.copy()
                backend_retry_queues = {}
                for backend_name, failed_links in link_manager.failed_links_by_backend.items():
                     intersection = [lnk for lnk in unmapped_links if lnk in failed_links]
                     if intersection:
                         backend_retry_queues[backend_name] = intersection
                         for lnk in intersection:
                             unmapped_links.remove(lnk)
                
                # Determine backends to run in this retry round
                retry_active_backends = []
                
                for orig_backend, links in backend_retry_queues.items():
                    # Check if original backend actually exists in map, if not try to map it
                    # (e.g. 'free_gemini_api' might be in backend_map under a slightly different key if using alias)
                    # We map them based on exact names in backend_map
                    failovers = failover_settings.get(orig_backend, [])
                    # Special fix: free_gemini_api -> free-gemini-api
                    if orig_backend == 'free_gemini_api' and 'free-gemini-api' in failover_settings:
                         failovers = failover_settings.get('free-gemini-api', [])
                    elif orig_backend == 'GeminiAPI' and 'gemini-api' in failover_settings:
                         failovers = failover_settings.get('gemini-api', [])
                         
                    # Pick the failover based on the retry_round index!
                    target_backend_key = orig_backend
                    if retry_round < len(failovers):
                        target_backend_key = failovers[retry_round]
                    elif len(failovers) > 0:
                        # If we exhausted the failover chain, keep using the last one
                        target_backend_key = failovers[-1]
                        
                    # Find the backend config in backend_map
                    if target_backend_key in backend_map:
                        print(f"🔄 [Retry Round {retry_round+1}] Routing {len(links)} failures from {orig_backend} to {target_backend_key}")
                        if backend_map[target_backend_key] not in retry_active_backends:
                            retry_active_backends.append(backend_map[target_backend_key])
                    else:
                        print(f"🔄 [Retry Round {retry_round+1}] Failover '{target_backend_key}' not found. Re-using original for {orig_backend}.")
                        if orig_backend in backend_map and backend_map[orig_backend] not in retry_active_backends:
                            retry_active_backends.append(backend_map[orig_backend])
                            
                if unmapped_links:
                    print(f"🔄 Routing {len(unmapped_links)} unmapped lost links to original backends pool")
                    for b in active_backends:
                        if b not in retry_active_backends:
                            retry_active_backends.append(b)
                            
                if not retry_active_backends:
                     print(f"🔄 No specific failovers identified. Re-using all original backends.")
                     retry_active_backends = active_backends # Fallback to whatever was running
                
                active_backends = retry_active_backends
                
                # 3. Restart Workers (previous ones died)
                # Image queue/workers are technically alive if we didn't join?
                # Actually, we joined them above! So we must restart EVERYTHING.
                
                # Re-create vars
                image_queue = queue.Queue()
                
                # Initialize threads list for all workers in this retry round
                threads = []

                # 3. Start Image Worker Threads (Only if images enabled)
                if upload_manager:
                    print(f"    Starting {args.workers} image worker threads...")
                    for i in range(args.workers):
                        t = threading.Thread(
                            target=image_worker,
                            args=(i, image_queue, result_manager, OUTPUT_DIR, imgbb_api_key, upload_manager),
                            daemon=True
                        )
                        t.start()
                        threads.append(t)
                
                # Restart Summary Workers
                if len(active_backends) == 1 and args.workers > 1:
                    backend_name, kwargs = active_backends[0]
                    for i in range(args.workers):
                        t = threading.Thread(
                            target=batch_worker,
                            args=(i, f"{backend_name}_{i+1}", link_manager, result_manager, image_queue, api_key_conf, prompt_template),
                            kwargs={
                                'use_gemini': kwargs.get('use_gemini', False),
                                'use_geminipro': kwargs.get('use_geminipro', False),
                                'use_perplexity': kwargs.get('use_perplexity', False),
                                'use_gemini_cookie': kwargs.get('use_gemini_cookie', False),
                                'use_gemini_api': kwargs.get('use_gemini_api', False),
                                'gemini_api_key': kwargs.get('gemini_api_key', None),
                                'use_geminipro_cli': kwargs.get('use_geminipro_cli', False),
                                'use_g4f': kwargs.get('use_g4f', False),
                                'use_deeperseek': kwargs.get('use_deeperseek', False),
                                'use_multi_model': kwargs.get('use_multi_model', False),
                                'use_gemini_selenium': kwargs.get('gemini_selenium', False),
                                'use_free_gemini_api': kwargs.get('use_free_gemini_api', False),
                                'key_index': args.gemini_api_key_index,
                            'use_lmstudio_local': kwargs.get('use_lmstudio_local', False),
                            'use_lmstudio_remote': kwargs.get('use_lmstudio_remote', False)
                            }
                        )
                        t.daemon = True
                        t.start()
                        threads.append(t)
                        if i < args.workers - 1:  # Don't sleep after the last worker
                            print(f"      Waiting 30s before starting next worker...")
                            time.sleep(30)
                else:
                    for idx, (backend_name, kwargs) in enumerate(active_backends):
                        t = threading.Thread(
                            target=batch_worker,
                            args=(idx, backend_name, link_manager, result_manager, image_queue, api_key_conf, prompt_template),
                            kwargs={
                                'use_gemini': kwargs.get('use_gemini', False),
                                'use_geminipro': kwargs.get('use_geminipro', False),
                                'use_perplexity': kwargs.get('use_perplexity', False),
                                'use_gemini_cookie': kwargs.get('use_gemini_cookie', False),
                                'use_gemini_api': kwargs.get('use_gemini_api', False),
                                'gemini_api_key': kwargs.get('gemini_api_key', None),
                                'use_geminipro_cli': kwargs.get('use_geminipro_cli', False),
                                'use_g4f': kwargs.get('use_g4f', False),
                                'use_deeperseek': kwargs.get('use_deeperseek', False),
                                'use_multi_model': kwargs.get('use_multi_model', False),
                                'use_gemini_selenium': kwargs.get('gemini_selenium', False),
                                'use_free_gemini_api': kwargs.get('use_free_gemini_api', False),
                                'key_index': args.gemini_api_key_index,
                            'use_lmstudio_local': kwargs.get('use_lmstudio_local', False),
                            'use_lmstudio_remote': kwargs.get('use_lmstudio_remote', False)
                            }
                        )
                        t.daemon = True
                        t.start()
                        threads.append(t)

                # 4. Monitor Loop (Retry)
                while link_manager.has_work() or not image_queue.empty() or any(t.is_alive() for t in threads):
                     if args.push:
                        completed_cats = link_manager.get_completed_unpushed()
                        for cat in completed_cats:
                            print(f"\n🚀 [Retry Push] Category '{cat}' complete! Triggering push...")
                            try:
                                path = result_manager.data_json_path
                                subprocess.run(["git", "add", path], check=True)
                                subprocess.run(["git", "commit", "-m", f"Auto-update: {cat} (Retry)"], check=False)
                                subprocess.run(["git", "push"], check=True)
                                link_manager.mark_category_pushed(cat)
                            except: pass
                     time.sleep(5)
                
                # Join
                for t in threads: t.join()
                image_queue.join()
            
            print("\n✅ All Retry Rounds Complete.")

            # Flush any remaining dirty items that didn't hit the threshold
            with result_manager.lock:
                if result_manager._dirty_count > 0:
                    result_manager._flush_to_disk()

            # Final Retry for uploads
            if upload_manager:
                upload_manager.final_retry(OUTPUT_DIR, imgbb_api_key, result_manager)
            
            # Sync back results from ResultManager to the main lists for accurate statistics
            if hasattr(result_manager, '_data'):
                all_news = result_manager._data
                new_items_count = len(all_news) - len(existing_news)
                if new_items_count < 0:
                    new_items_count = 0
    
    # ============================================
    # SINGLE THREAD MODE (original behavior)
    # ============================================
    else:
        # Identify which backend to use
        kwargs = active_backends[0][1] if active_backends else {}
        
        for filename in files:
            news_items = process_file(
                filename, api_key_conf, prompt_template, history, 
                use_gemini=kwargs.get('use_gemini', False),
                use_geminipro=kwargs.get('use_geminipro', False),
                use_perplexity=kwargs.get('use_perplexity', False),
                use_gemini_cookie=kwargs.get('use_gemini_cookie', False),
                use_gemini_api=kwargs.get('use_gemini_api', False),
                gemini_api_key=kwargs.get('gemini_api_key', None),
                use_geminipro_cli=kwargs.get('use_geminipro_cli', False),
                use_g4f=kwargs.get('use_g4f', False),
                use_deeperseek=kwargs.get('use_deeperseek', False),
                use_multi_model=use_multi_model,  # CRITICAL: Pass through multi-model flag
                key_index=args.gemini_api_key_index,
                use_lmstudio_local=args.use_lmstudio_local,
                use_lmstudio_remote=args.use_lmstudio_remote
            )
            
            # Only add new items (not already in existing)
            for item in news_items:
                link = item.get('sourceLink', '')
                if link not in existing_links:
                    all_news.append(item)
                    existing_links.add(link)
                    new_items_count += 1
            
            # Save after each file for crash recovery
            if news_items:
                try:
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(all_news, f, ensure_ascii=False, indent=2)
                    print(f"  [Saved {len(all_news)} items to data.json]")
                    
                    # Auto-push if requested
                    if args.push:
                        print(f"\n🚀 [Auto-Push] Completed {filename}. Pushing data.json...")
                        try:
                            subprocess.run(["git", "add", output_path], check=True)
                            subprocess.run(["git", "commit", "-m", f"Auto-update: {filename} ({len(news_items)} new items)"], check=False)
                            subprocess.run(["git", "push"], check=True)
                            print("✅ [Auto-Push] Success!")
                        except Exception as e:
                            print(f"⚠️ [Auto-Push] Failed: {e}")

                except Exception as e:
                    print(f"  Warning: Could not save intermediate data.json: {e}")

        # Process pending links from previous runs (single thread)
        # BROKEN LEGACY LOOP - Disabling to prevent crash
        # The parallel processing above (link_manager) already handles pending links.
        # This block expects dicts but receives URLs, causing TypeError.
        # if pending_links:
        #     pending_links = [url for url in pending_links if url not in existing_links]
        #     
        #     if pending_links:
        #         print(f"\n📋 Processing {len(pending_links)} pending links from previous runs...")
        #         
        #         batches = [pending_links[i:i + BATCH_SIZE] for i in range(0, len(pending_links), BATCH_SIZE)]
        #         
        #         for i, batch in enumerate(tqdm(batches, desc="  Retrying pending", unit="batch")):
        #             # process_batch call removed/commented
        #             pass
        #             
        #             # Mark filtered links
        #             for link in filtered_links:
        #                 history.update(link, status='FILTERED')
        #             
        #             if news_list:
        #                 for item in news_list:
        #                     link = item.get('sourceLink', '')
        #                     if link not in existing_links:
        #                         all_news.append(item)
        #                         existing_links.add(link)
        #                         new_items_count += 1
        #                 
        #                 successful_links = set(batch) - set(failed_links) - set(filtered_links)
        #                 for link in successful_links:
        #                     history.update(link, summarized=True)
        #                 
        #                 try:
        #                     with open(output_path, 'w', encoding='utf-8') as f:
        #                         json.dump(all_news, f, ensure_ascii=False, indent=2)
        #                 except Exception as e:
        #                     print(f"  Warning: Could not save: {e}")

    # Final logic: add fooldal section and randomize
    if new_items_count == 0 and not all_news:
        print("\nNo items processed.")
        
        # If we have existing news but no new ones, we can still reshuffle if requested,
        # but normally we assume job is done.
        # But if we have valid items, we should ensure they are randomized/refreshed
        if existing_news:
             print("Reshuffling existing items...")
             all_news = existing_news
        else:
             return
    
    # ============================================
    # FINAL STATISTICS & ERROR REPORTING
    # ============================================
    if len(active_backends) >= 2 or args.workers > 1:
        # Get stats from LinkManager
        success_count = getattr(link_manager, 'stats_pairing_success', 0)
        failure_count = getattr(link_manager, 'stats_pairing_failed', 0)
        failed_links = getattr(link_manager, 'failed_pairing_links', [])
        
        print("\n" + "="*50)
        print("          PIPELINE EXECUTION SUMMARY")
        print("="*50)
        print(f"✅ Successful Pairings: {success_count}")
        print(f"❌ Failed Pairings:     {failure_count}")
        print("-" * 50)
        
        if failure_count > 0:
            print("\n📋 FAILED PAIRINGS (Links returned by LLM but not found in batch):")
            for i, link in enumerate(failed_links):
                print(f"  {i+1}. {link}")
            print("\n⚠️ NOTE: These items were KEPT as fallback (without original metadata). Images might be missing.")
        else:
            print("🎉 No pairing errors! All processed items were correctly matched.")
        print("="*50 + "\n")

    print(f"\nNew items added: {new_items_count}")
    print(f"Total news items: {len(all_news)}")
    
    # Add fooldal section to random 30 items
    all_news = add_fooldal_section(all_news)
    
    # Randomize news within sections (group by section, randomize within groups)
    print("Randomizing news within sections...")
    all_news = randomize_within_sections(all_news)
    
    # Write unified data.json
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        print(f"\nSaved {len(all_news)} news items to {output_path}")
    except Exception as e:
        print(f"Error writing data.json: {e}")

    # Print processing statistics
    if use_perplexity:
        perplexity_client.print_stats()
    if use_geminipro or use_gemini_cookie:
        geminipro_client.print_stats()
    elif use_gemini_api:
        gemini_client.print_stats()

    # Post-processing: Fix pairing failures — run in background thread so pipeline isn't blocked
    print("\n🔄 Post-processing: Starting pairing failure reprocessing in background...")
    _repair_result = [0]  # mutable container for thread result

    def _repair_worker():
        try:
            _repair_result[0] = reprocess_pairing_failures(OUTPUT_DIR, api_key_conf, prompt_template)
        except Exception as e:
            print(f"  ❌ Reprocessing thread error: {e}")

    repair_thread = threading.Thread(target=_repair_worker, name="PairingRepair", daemon=True)
    repair_thread.start()

    # --- Pipeline can do other things here (stats are already printed above) ---
    # Wait for the repair thread — but max 3 minutes after EVERYTHING else is done
    REPAIR_TIMEOUT = 180
    print(f"  ⏳ Waiting up to {REPAIR_TIMEOUT}s for background repair thread...")
    repair_thread.join(timeout=REPAIR_TIMEOUT)

    if repair_thread.is_alive():
        print("  ⏭️ Repair thread still running after timeout — pipeline will not wait further. Thread will finish or die on its own.")
    elif _repair_result[0] > 0:
        print(f"✅ Fixed {_repair_result[0]} pairing failure(s) across all data files")
    else:
        print("ℹ️ No fixable pairing failures found")

if __name__ == "__main__":
    main()
