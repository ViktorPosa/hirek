import newspaper
from newspaper import Article
import time
import random
import threading
import queue
from urllib.parse import urlparse

"""
LEÍRÁS:
Cikk letöltő és parse-oló modul.
- newspaper3k: gyors letöltés
- Playwright fallback: külön queue-ban fut párhuzamosan a 403-as hibákhoz

BEMENET:
- URL

KIMENET:
- Dictionary: {url, title, text} vagy None hiba esetén
"""

# --- Rate Limiting State ---
_DOMAIN_LOCK = threading.Lock()
_NEXT_DOMAIN_TIME = {}  # domain -> Unix timestamp when next download is allowed

# --- (B) Reliability bounds ---
MAX_WAIT_PER_DOMAIN = 120   # cap any computed backoff wait (seconds)
MAX_RETRIES_PER_URL = 2     # give up on a URL after this many attempts

_URL_RETRIES = {}           # url -> attempt count
_URL_RETRIES_LOCK = threading.Lock()

_RUN_DEADLINE = None        # Unix timestamp after which the run stops doing work


def set_run_deadline(seconds):
    """Set a global per-run deadline `seconds` from now. Downloads stop past it."""
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.time() + seconds


def run_expired():
    """True if the per-run deadline has passed."""
    return _RUN_DEADLINE is not None and time.time() > _RUN_DEADLINE


def reset_run_state():
    """Reset all per-run module-level state. Call at the start of each pipeline run
    so that URL retry counters and deadlines don't bleed from the 06:00 run into 18:00."""
    global _URL_RETRIES, _RUN_DEADLINE, _NEXT_DOMAIN_TIME
    with _URL_RETRIES_LOCK:
        _URL_RETRIES.clear()
    with _DOMAIN_LOCK:
        _NEXT_DOMAIN_TIME.clear()
    _RUN_DEADLINE = None

# Global lock for curl_cffi to prevent macOS SIGSEGV/SIGABRT malloc errors
# curl_cffi uses libcurl-impersonate with BoringSSL, which is notoriously unstable 
# when initialized/used concurrently across threads on macOS.
_CURL_CFFI_LOCK = threading.Lock()
_GLOBAL_SESSION = None

def get_cffi_session():
    global _GLOBAL_SESSION
    with _CURL_CFFI_LOCK:
        if _GLOBAL_SESSION is None:
            from curl_cffi import requests as cffi_requests
            # Create a single persistent session
            _GLOBAL_SESSION = cffi_requests.Session(impersonate="chrome")
    return _GLOBAL_SESSION

# Playwright queue for 403 retries (shared)
_PLAYWRIGHT_QUEUE = None
_PLAYWRIGHT_WORKER_STARTED = False
_PLAYWRIGHT_LOCK = threading.Lock()


def get_domain(url):
    """Extract domain from URL."""
    try:
        return urlparse(url).netloc
    except:
        return "unknown"


def init_playwright_queue():
    """Initialize the shared Playwright queue."""
    global _PLAYWRIGHT_QUEUE
    if _PLAYWRIGHT_QUEUE is None:
        _PLAYWRIGHT_QUEUE = queue.Queue()
    return _PLAYWRIGHT_QUEUE


def get_playwright_queue():
    """Get the Playwright queue (creates if needed)."""
    global _PLAYWRIGHT_QUEUE
    if _PLAYWRIGHT_QUEUE is None:
        _PLAYWRIGHT_QUEUE = queue.Queue()
    return _PLAYWRIGHT_QUEUE


class PlaywrightWorker(threading.Thread):
    """
    Separate worker thread that handles 403 retries with Playwright.
    Runs in parallel with newspaper downloads.
    """
    
    def __init__(self, article_buffer, history):
        super().__init__(daemon=True)
        self.article_buffer = article_buffer
        self.history = history
        self.name = "PlaywrightWorker"
        self.running = True
        self._browser = None
        self._playwright = None
    
    def run(self):
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth
            
            pw_queue = get_playwright_queue()
            
            with sync_playwright() as p:
                # Launch browser once, reuse for all requests
                browser = p.chromium.launch(headless=True)
                print(f"   🌐 [{self.name}] Started with headless Chromium")
                
                empty_count = 0
                while self.running:
                    try:
                        # Get URL from playwright queue
                        try:
                            url = pw_queue.get(timeout=5)
                        except queue.Empty:
                            empty_count += 1
                            if empty_count > 20:  # 100s of no work
                                print(f"   [{self.name}] No more work, exiting")
                                break
                            continue
                        
                        empty_count = 0
                        
                        if url is None:  # Poison pill
                            pw_queue.task_done()
                            break
                        
                        # Download with Playwright
                        result = self._download_with_browser(browser, url)
                        
                        if result and result.get('text') and len(result['text']) > 100:
                            print(f"   ✅ [{self.name}] Success: {url[:50]}...")
                            self.article_buffer.put(result)
                        else:
                            self.history.mark_processing_error(url, "Playwright: empty content")
                            self.article_buffer.report_failure()
                        
                        pw_queue.task_done()
                        
                    except Exception as e:
                        print(f"   ⚠️ [{self.name}] Error: {str(e)[:50]}")
                
                browser.close()
                
        except Exception as e:
            print(f"   ❌ [{self.name}] Failed to start: {e}")
    
    def _download_with_browser(self, browser, url, timeout=30):
        """Download using existing browser instance with stealth mode."""
        try:
            from playwright_stealth import Stealth
            import random
            
            USER_AGENTS = [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
            ]

            context = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                color_scheme='dark'
            )
            
            page = context.new_page()
            
            # Apply stealth mode immediately to hide webdriver flags and emulate navigator properties
            Stealth().apply_stealth_sync(page)
            
            page.goto(url, timeout=timeout * 1000, wait_until='domcontentloaded')
            time.sleep(1.5)
            
            title = page.title()
            
            # Extract content
            content = ""
            selectors = ['article', '[role="article"]', '.article-content', 
                        '.article-body', '.post-content', '.entry-content', 
                        '.story-body', 'main', '.content']
            
            for selector in selectors:
                try:
                    element = page.query_selector(selector)
                    if element:
                        content = element.inner_text()
                        if len(content) > 200:
                            break
                except:
                    continue
            
            if not content or len(content) < 200:
                content = page.inner_text('body')
            
            context.close()
            
            return {'url': url, 'title': title, 'text': content}
            
        except Exception as e:
            print(f"   ❌ [{self.name}] Download failed {url[:40]}...: {str(e)[:30]}")
            return None
    
    def stop(self):
        self.running = False
        get_playwright_queue().put(None)


def download_article(url, timeout=30, rss_fallback=""):
    """
    Downloads and parses a single article using newspaper3k.
    Takes full HTML content directly via curl_cffi.
    If rss_fallback is provided, uses it if network request is blocked (403/503).
    On 403/503 error without fallback, queues URL for Playwright.
    Returns dictionary with article data, None on failure, or 'QUEUED_FOR_PLAYWRIGHT'.
    """
    domain = get_domain(url)

    # --- (B) Per-run deadline: stop doing more work once expired ---
    if run_expired():
        print(f"   ⏱️ Run deadline reached, skipping: {url[:50]}...")
        return None

    # --- (B) Per-URL retry cap: give up after MAX_RETRIES_PER_URL attempts ---
    with _URL_RETRIES_LOCK:
        attempts = _URL_RETRIES.get(url, 0)
        if attempts >= MAX_RETRIES_PER_URL:
            print(f"   ⏭️ Max retries ({MAX_RETRIES_PER_URL}) reached, skipping: {url[:50]}...")
            return None
        _URL_RETRIES[url] = attempts + 1

    # --- Rate Limiting ---
    delay_needed = 0
    with _DOMAIN_LOCK:
        next_allowed = _NEXT_DOMAIN_TIME.get(domain, 0)
        now = time.time()

        if now < next_allowed:
            delay_needed = next_allowed - now

        actual_start = max(now, next_allowed)

        # Determine delay based on domain
        if domain.endswith('.hu'):
             # Faster for Hungarian sites
             wait_time = random.uniform(2, 5)
        else:
             # International sites — human-like but fast
             wait_time = random.uniform(5, 10)

        _NEXT_DOMAIN_TIME[domain] = actual_start + wait_time

    # --- (B) Cap any computed backoff wait ---
    if delay_needed > MAX_WAIT_PER_DOMAIN:
        delay_needed = MAX_WAIT_PER_DOMAIN

    if delay_needed > 0:
        if delay_needed > 60:
            print(f"   ⚠️ {domain} rate-limited ({delay_needed:.0f}s). Waiting then retrying once...")
            time.sleep(delay_needed + random.uniform(1, 3))
            # Re-check after waiting
            with _DOMAIN_LOCK:
                next_allowed2 = _NEXT_DOMAIN_TIME.get(domain, 0)
                still_needed = max(0, next_allowed2 - time.time())
            if still_needed > 5:
                print(f"   ⏭️ Skipping {domain} after retry wait ({still_needed:.0f}s still needed). Will retry next run.")
                return None
        else:
            time.sleep(delay_needed + random.uniform(0.5, 1.5))
    
    # --- Download Logic with curl_cffi bypass ---
    try:
        session = get_cffi_session()
        time.sleep(random.uniform(0.3, 1.5))
        
        # 1. Manually fetch the HTML with TLS impersonation (bypassing Newspaper3k's internal blocked requests)
        # CRITICAL: wrap in a global lock to strictly serialize libcurl C-extension calls and avoid SIGSEGV
        with _CURL_CFFI_LOCK:
            response = session.get(
                url, 
                timeout=timeout,
                headers={
                    'Referer': 'https://www.google.com/search?q=' + domain,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9,hu;q=0.8',
                }
            )
        response.raise_for_status()
        html_content = response.text
        
        # 2. Feed the raw HTML to Newspaper3k for offline parsing
        config = newspaper.Config()
        article = Article(url, config=config)
        article.set_html(html_content)
        article.parse()
        
        print(f"   ✅ curl_cffi + newspaper OK: {url[:50]}...")
        return {
            'url': url,
            'title': article.title,
            'text': article.text
        }
        
    except Exception as e:
        error_msg = str(e).lower()

        # --- (B) 404 / not found: skip immediately, no retry, no fallback ---
        if "404" in error_msg or "not found" in error_msg:
            print(f"   ⏭️ 404/Not Found, skipping (no retry): {url[:50]}...")
            # Remove from retry counter so we don't waste future slots on it,
            # and signal the caller to mark this URL as NEGATIVE in history.
            with _URL_RETRIES_LOCK:
                _URL_RETRIES.pop(url, None)
            return "404_PERMANENT"

        # --- RSS Fallback check on blocked requests ---
        if rss_fallback and len(rss_fallback.strip()) > 300:
            try:
                print(f"   ⚠️ Blocked! Using RSS Content Fallback for {url[:40]}...")
                config = newspaper.Config()
                article = Article(url, config=config)
                article.set_html(rss_fallback)
                article.parse()
                
                if len(article.text) > 50:
                    return {
                        'url': url,
                        'title': article.title,
                        'text': article.text
                    }
            except Exception as rss_e:
                print(f"   ❌ RSS fallback parsing failed: {rss_e}")
                
        # Queue 403 / 503 errors for Playwright
        if "403" in error_msg or "503" in error_msg:
            pw_queue = get_playwright_queue()
            pw_queue.put(url)
            print(f"   🔄 Anti-Bot Block → Playwright queue: {url[:50]}...")
            return 'QUEUED_FOR_PLAYWRIGHT'
        
        # Other errors
        print(f"   ❌ Download failed {url[:50]}...: {str(e)[:30]}")
        return None
