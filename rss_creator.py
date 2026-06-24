import argparse
import logging
import signal
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from curl_cffi import requests
from bs4 import BeautifulSoup
import newspaper
from feedgen.feed import FeedGenerator
import datetime as _dt

from datetime import timezone
import json
import time
import os
import subprocess

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
try:
    import trafilatura
    from trafilatura.feeds import find_feed_urls as _traf_find_feeds
    from trafilatura.sitemaps import sitemap_search as _traf_sitemap_search
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False
    logging.warning("trafilatura not installed — fallback extraction and sitemap/feed discovery disabled")

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = _dt.datetime.now(timezone.utc).strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join(os.getcwd(), 'Output', today)
    
RSS_FEEDS_DIR = os.path.join(DAILY_OUTPUT_DIR, 'rss_feeds')
if not os.path.exists(RSS_FEEDS_DIR):
    os.makedirs(RSS_FEEDS_DIR, exist_ok=True)
    
GENERATED_FEEDS_FILE = os.path.join(DAILY_OUTPUT_DIR, 'generated_feeds.txt')

# In-memory set to avoid duplicate entries in generated_feeds.txt
_registered_feeds = set()
_registered_feeds_lock = threading.Lock()

# Persistent failed-URL blacklist — URLs that have failed twice are skipped forever
FAILED_URLS_FILE = os.path.join(os.getcwd(), 'Input', 'rss_failed_urls.txt')

# Minimum number of items in a cached feed to consider it valid for skipping
MIN_ITEMS_TO_SKIP = 3

# Maximum age of a cached feed before it is considered stale (hours)
FEED_MAX_AGE_HOURS = 8

# Per-article extraction timeout (seconds)
ARTICLE_TIMEOUT = 20

# HTTP request timeout (seconds)
HTTP_TIMEOUT = 15

# Trafilatura sitemap discovery timeout (seconds)
SITEMAP_TIMEOUT = 30

# Trafilatura feed discovery timeout (seconds)
FEED_DISCOVERY_TIMEOUT = 15

# Per-URL processing timeout (seconds) — hard cap for parallel workers
PER_URL_TIMEOUT = 120

# Default number of parallel workers
DEFAULT_WORKERS = 10

# Sites known to require JS rendering
JS_RENDER_SITES = {'playboy.com', 'fhm.com', 'maxim.com', 'thestreet.com', 'bayareatimes.com', 'faroutmagazine.co.uk'}

# Default impersonation profile and fallback list for anti-bot bypass
DEFAULT_IMPERSONATE = "chrome131"
IMPERSONATE_FALLBACKS = ["chrome131", "safari180", "firefox133", "chrome124", "chrome136"]

# Path to LightPanda binary (lightweight headless browser for JS rendering)
LIGHTPANDA_BIN = os.path.expanduser('~/bin/lightpanda')

# Hardcoded RSS feed URLs for sites where auto-discovery often fails
# These are verified-working RSS endpoints as of 2026-04-28
KNOWN_RSS_FEEDS = {
    'hu.ign.com': ['https://hu.ign.com/feed.xml'],
    'www.economx.hu': ['https://www.economx.hu/feed'],
    'economx.hu': ['https://www.economx.hu/feed'],
    'sg.hu': ['https://sg.hu/rss'],
    'totalcar.hu': ['https://totalcar.hu/feed'],
    'nlc.hu': ['https://nlc.hu/feed'],
    'sorozatjunkie.hu': ['https://sorozatjunkie.hu/feed'],
    'marieclaire.hu': ['https://marieclaire.hu/feed'],
    'ng.24.hu': ['https://ng.24.hu/feed'],
    'player.hu': ['https://player.hu/feed'],
    'firstclass.hu': ['https://firstclass.hu/feed'],
    'offmedia.hu': ['https://offmedia.hu/feed'],
    'funzine.hu': ['https://funzine.hu/feed'],
    'cliffhanger.hu': ['https://cliffhanger.hu/feed'],
    'raketa.hu': ['https://raketa.hu/feed'],
    'roadster.hu': ['https://roadster.hu/feed'],
    'azutazo.hu': ['https://azutazo.hu/feed'],
    'aihirfolyam.hu': ['https://aihirfolyam.hu/feed'],
    'the-zone.hu': ['https://the-zone.hu/feed'],
    'travelmagazin.hu': ['https://travelmagazin.hu/feed'],
    'telex.hu': ['https://telex.hu/rss'],
    'fhm.com': ['https://fhm.com/feed'],
    'www.maxim.com': ['https://www.maxim.com/feed'],
    'maxim.com': ['https://www.maxim.com/feed'],
    'caravantimes.co.uk': ['https://caravantimes.co.uk/feed'],
    'instylemen.hu': ['https://instylemen.hu/feed'],
    'velvet.hu': ['https://velvet.hu/feed'],
    'hwsw.hu': ['https://hwsw.hu/hirek/rss'],
    'prog.hu': ['https://prog.hu/hirek/rss'],
    'femina.hu': ['https://femina.hu/feed'],
    'www.penzcentrum.hu': ['https://www.penzcentrum.hu/rss/all.xml'],
    'penzcentrum.hu': ['https://www.penzcentrum.hu/rss/all.xml'],
}


def _fetch_with_retries(url, timeout=None, max_retries=None):
    """Fetch a URL using curl_cffi with impersonation rotation on failure.
    
    Tries multiple browser impersonation profiles to bypass anti-bot measures.
    Returns (response, None) on success or (None, last_exception) on failure.
    """
    if timeout is None:
        timeout = HTTP_TIMEOUT
    if max_retries is None:
        max_retries = len(IMPERSONATE_FALLBACKS)
    
    profiles = IMPERSONATE_FALLBACKS[:max_retries]
    last_error = None
    
    for profile in profiles:
        try:
            response = requests.get(url, impersonate=profile, timeout=(10, timeout))
            # Treat 403/429 as "blocked" — try next profile
            if response.status_code in (403, 429):
                last_error = Exception(f"HTTP {response.status_code} with {profile}")
                logging.debug(f"  🔄 {profile} got HTTP {response.status_code} for {url}, trying next profile...")
                continue
            response.raise_for_status()
            return response, None
        except Exception as e:
            last_error = e
            logging.debug(f"  🔄 {profile} failed for {url}: {e}")
            continue
    
    return None, last_error

# ---------------------------------------------------------------------------
# Playwright — dedicated thread approach
# ---------------------------------------------------------------------------
# Playwright's sync API uses greenlets that are bound to the thread that called
# sync_playwright().start(). Sharing the browser/page objects across threads
# causes the "Cannot switch to a different thread" greenlet crash even when
# protected by a Lock.  The fix: one permanent daemon thread owns the entire
# Playwright context; every other thread sends a (url, result_queue) pair and
# blocks until the daemon replies.
# ---------------------------------------------------------------------------
import queue as _queue

_PW_REQUEST_SENTINEL = None  # sent to shut down the daemon
_pw_request_queue: _queue.Queue = _queue.Queue()
_pw_thread_started = False
_pw_thread_lock = threading.Lock()


def _playwright_daemon():
    """Daemon thread: owns the Playwright browser for its entire lifetime."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logging.warning("playwright not installed — JS rendering unavailable")
        # Drain any queued requests with None so callers don't block forever
        while True:
            try:
                item = _pw_request_queue.get(timeout=1)
                if item is _PW_REQUEST_SENTINEL:
                    break
                _url, _timeout, reply_q = item
                reply_q.put(None)
            except _queue.Empty:
                pass
        return

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        logging.info("  🎭 Playwright browser launched (dedicated thread)")

        while True:
            try:
                item = _pw_request_queue.get(timeout=5)
            except _queue.Empty:
                continue

            if item is _PW_REQUEST_SENTINEL:
                break

            url, timeout, reply_q = item
            result = None
            try:
                page = browser.new_page(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) '
                               'Chrome/131.0.0.0 Safari/537.36'
                )
                try:
                    # Use domcontentloaded instead of networkidle to avoid hangs
                    # on sites with long-polling/WebSocket connections
                    page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                    # Brief extra wait for JS rendering (2s max)
                    try:
                        page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        pass  # Acceptable — DOM is already loaded
                    result = page.content()
                finally:
                    page.close()
            except Exception as e:
                logging.warning(f"Playwright fetch failed for {url}: {e}")
                result = None
            finally:
                reply_q.put(result)

    except Exception as e:
        logging.error(f"Playwright daemon crashed: {e}")
    finally:
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass


def _ensure_playwright_daemon():
    """Start the Playwright daemon thread on first use (idempotent)."""
    global _pw_thread_started
    if _pw_thread_started:
        return
    with _pw_thread_lock:
        if _pw_thread_started:
            return
        t = threading.Thread(target=_playwright_daemon, name="playwright-daemon", daemon=True)
        t.start()
        _pw_thread_started = True


def fetch_with_lightpanda(url, timeout=15):
    """Fetch a page using LightPanda headless browser. Returns HTML string or None.

    LightPanda is a lightweight headless browser written in Zig that supports
    JavaScript execution but is 11x faster and 16x more memory-efficient
    than headless Chrome. Falls back to Playwright if LightPanda is not installed.
    """
    if not os.path.exists(LIGHTPANDA_BIN):
        logging.debug("LightPanda not found, falling back to Playwright")
        return fetch_with_playwright(url, timeout=timeout)

    try:
        result = subprocess.run(
            [LIGHTPANDA_BIN, 'fetch', '--dump', 'html',
             '--wait-until', 'domcontentloaded',
             '--wait-ms', str(min(timeout * 1000, 10000)),
             url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if result.returncode == 0 and len(result.stdout) > 200:
            logging.info(f"  🐼 LightPanda fetched {url} ({len(result.stdout)} bytes)")
            return result.stdout
        else:
            logging.debug(f"LightPanda returned {result.returncode} for {url}, stderr: {result.stderr[:200] if result.stderr else ''}")
            return None
    except subprocess.TimeoutExpired:
        logging.warning(f"LightPanda timeout ({timeout}s) for {url}")
        return None
    except Exception as e:
        logging.warning(f"LightPanda error for {url}: {e}")
        return None


def fetch_with_playwright(url, timeout=30):
    """Fetch a page using headless Chromium. Returns HTML string or None.

    Thread-safe: all calls are serialised through the Playwright daemon thread
    so there is never a greenlet-context mismatch.
    """
    _ensure_playwright_daemon()
    reply_q: _queue.Queue = _queue.Queue(maxsize=1)
    _pw_request_queue.put((url, timeout, reply_q))
    try:
        return reply_q.get(timeout=timeout + 15)  # extra slack for queue wait
    except _queue.Empty:
        logging.warning(f"Playwright timeout (queue) for {url}")
        return None


def fetch_js_rendered(url, timeout=20):
    """Fetch a JS-rendered page. Tries LightPanda first, then Playwright."""
    html = fetch_with_lightpanda(url, timeout=timeout)
    if html and len(html) > 500:
        return html
    # Fallback to Playwright
    return fetch_with_playwright(url, timeout=timeout)


def cleanup_playwright():
    """Signal the Playwright daemon to shut down gracefully."""
    global _pw_thread_started
    if _pw_thread_started:
        _pw_request_queue.put(_PW_REQUEST_SENTINEL)
        _pw_thread_started = False


# Pre-compiled regex for DOM context scoring
_ARTICLE_CLASS_RE = re.compile(r'(?:^|[-_ ])(?:post|article|story|news|card|item|entry|hir|cikk|feed|headline)(?:[-_ ]|$)')


class ArticleTimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise ArticleTimeoutError("Article extraction timed out")


def _is_js_site(url):
    """Check if a URL belongs to a site that needs JS rendering."""
    domain = get_base_domain(url).lower().removeprefix('www.')
    return domain in JS_RENDER_SITES


def _register_feed(filepath):
    """Append feed path to generated_feeds.txt, avoiding duplicates. Thread-safe."""
    if filepath.startswith('http://') or filepath.startswith('https://'):
        target_path = filepath
    else:
        target_path = os.path.abspath(filepath)
        
    with _registered_feeds_lock:
        if target_path in _registered_feeds:
            return
        _registered_feeds.add(target_path)
        try:
            with open(GENERATED_FEEDS_FILE, 'a', encoding='utf-8') as f:
                f.write(target_path + '\n')
        except Exception as e:
            logging.error(f"Failed to append to {GENERATED_FEEDS_FILE}: {e}")


def load_failed_urls():
    """Load the persistent list of URLs that have failed too many times."""
    if not os.path.exists(FAILED_URLS_FILE):
        return {}
    result = {}
    try:
        with open(FAILED_URLS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.rsplit('\t', 1)
                    url = parts[0].strip()
                    count = int(parts[1]) if len(parts) == 2 else 1
                    result[url] = count
    except Exception as e:
        logging.warning(f"Could not load failed URLs file: {e}")
    return result


# Lock for thread-safe writes to failed_urls dict
_failed_urls_lock = threading.Lock()


def record_failed_url(url, failed_urls):
    """Record a failed URL in memory. Returns True if the URL is now blacklisted (>=2 failures). Thread-safe."""
    with _failed_urls_lock:
        failed_urls[url] = failed_urls.get(url, 0) + 1
        return failed_urls[url] >= 2


def save_failed_urls(failed_urls):
    """Persist failed URLs dict to disk. Call once at session end."""
    try:
        with open(FAILED_URLS_FILE, 'w', encoding='utf-8') as f:
            f.write('# RSS Creator - permanently failed article URLs\n')
            f.write('# Format: url\tfailure_count\n')
            for u, count in sorted(failed_urls.items()):
                f.write(f"{u}\t{count}\n")
    except Exception as e:
        logging.warning(f"Could not save failed URLs file: {e}")

def load_known_urls(days=3):
    """Load already-processed article URLs from history.json and recent data.json files."""
    known = set()

    # --- From history.json (only load URLs, not full data) ---
    try:
        history_path = os.path.join(os.getcwd(), 'history.json')
        if os.path.exists(history_path):
            with open(history_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
            for url in history:
                known.add(url.rstrip('/'))
    except Exception as e:
        logging.warning(f"Could not load history.json for dedup: {e}")

    # --- From recent data files ---
    base_out = os.path.join(os.getcwd(), 'Output')
    today_dt = _dt.date.today()
    for i in range(days):
        date_str = (today_dt - _dt.timedelta(days=i)).strftime('%Y-%m-%d')
        for fname in ('data.json', 'data_i4.json', 'data_i5.json'):
            p = os.path.join(base_out, date_str, fname)
            if os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    for item in items:
                        link = item.get('sourceLink', '')
                        if link:
                            known.add(link.rstrip('/'))
                except Exception:
                    pass

    logging.info(f"Dedup: loaded {len(known)} known article URLs (history + recent data).")
    return known


def get_feed_info(filepath):
    """Return item count and last build date from an RSS XML file."""
    result = {'item_count': 0, 'last_build_date': None}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        result['item_count'] = content.count('<item>')
        # Extract lastBuildDate
        match = re.search(r'<lastBuildDate>([^<]+)</lastBuildDate>', content)
        if match:
            result['last_build_date'] = _parse_date_string(match.group(1).strip())
    except Exception:
        pass
    return result


def get_base_domain(url):
    parsed = urlparse(url)
    return parsed.netloc


def _domains_match(netloc, base_domain):
    """Check if two domain strings refer to the same site (with/without www)."""
    a = netloc.lower().removeprefix('www.')
    b = base_domain.lower().removeprefix('www.')
    return a == b


def _parse_date_string(date_str):
    """Parse common date formats (ISO 8601, RSS, human-readable). Returns datetime or None."""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S %Z',
        '%d %b %Y %H:%M:%S %z',
        '%d %b %Y',
        '%B %d, %Y',
        '%b %d, %Y',
    ]
    for fmt in formats:
        try:
            return _dt.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def extract_meta_tags(html_content, url=''):
    """Extract metadata from JSON-LD, Open Graph, and Twitter Card tags.

    Returns dict with keys: title, description, image, published_date, authors, sitename, categories.
    """
    result = {
        'title': '', 'description': '', 'image': '',
        'published_date': None, 'authors': [], 'sitename': '', 'categories': []
    }
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception:
        return result

    # --- 1. JSON-LD ---
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            # Handle @graph wrapper
            if isinstance(data, dict) and '@graph' in data:
                data = data['@graph']
            if isinstance(data, list):
                # Find the first Article/NewsArticle/BlogPosting
                for item in data:
                    if isinstance(item, dict) and item.get('@type', '') in (
                        'Article', 'NewsArticle', 'BlogPosting', 'WebPage', 'ReportageNewsArticle'
                    ):
                        data = item
                        break
                else:
                    data = data[0] if data else {}
            if isinstance(data, dict):
                if not result['title']:
                    result['title'] = data.get('headline', '') or data.get('name', '')
                if not result['description']:
                    result['description'] = data.get('description', '')
                if not result['image']:
                    img = data.get('image', '')
                    if isinstance(img, dict):
                        img = img.get('url', '')
                    elif isinstance(img, list):
                        img = img[0] if img else ''
                        if isinstance(img, dict):
                            img = img.get('url', '')
                    result['image'] = img
                if not result['published_date']:
                    result['published_date'] = _parse_date_string(
                        data.get('datePublished', '') or data.get('dateCreated', '')
                    )
                if not result['authors']:
                    author = data.get('author', '')
                    if isinstance(author, dict):
                        name = author.get('name', '')
                        if name:
                            result['authors'] = [name]
                    elif isinstance(author, list):
                        result['authors'] = [
                            a.get('name', '') if isinstance(a, dict) else str(a)
                            for a in author if a
                        ]
                    elif isinstance(author, str) and author:
                        result['authors'] = [author]
                if not result['categories']:
                    keywords = data.get('keywords', [])
                    if isinstance(keywords, str):
                        keywords = [k.strip() for k in keywords.split(',') if k.strip()]
                    if isinstance(keywords, list):
                        result['categories'] = keywords[:10]
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    # --- 2. Open Graph ---
    og_map = {
        'og:title': 'title', 'og:description': 'description',
        'og:image': 'image', 'og:site_name': 'sitename'
    }
    for prop, key in og_map.items():
        if not result[key]:
            tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                result[key] = tag['content']
    # OG article:published_time
    if not result['published_date']:
        pub_tag = soup.find('meta', property='article:published_time')
        if pub_tag and pub_tag.get('content'):
            result['published_date'] = _parse_date_string(pub_tag['content'])
    # OG article:tag for categories
    if not result['categories']:
        for tag in soup.find_all('meta', property='article:tag'):
            if tag.get('content'):
                result['categories'].append(tag['content'])

    # --- 3. Twitter Card ---
    tw_map = {'twitter:title': 'title', 'twitter:description': 'description', 'twitter:image': 'image'}
    for name, key in tw_map.items():
        if not result[key]:
            tag = soup.find('meta', attrs={'name': name}) or soup.find('meta', property=name)
            if tag and tag.get('content'):
                result[key] = tag['content']

    return result


def _score_link_dom_context(a_tag):
    """Score an <a> tag based on its DOM context (0-10 bonus points)."""
    bonus = 0
    # Check if inside <article>, <main>, or <section>
    for parent in a_tag.parents:
        if parent.name in ('article', 'main', 'section'):
            bonus += 3
            break
    # CSS class signals on parent elements (up to 4 levels)
    for ancestor in list(a_tag.parents)[:4]:
        cls = ' '.join(ancestor.get('class', [])).lower()
        if _ARTICLE_CLASS_RE.search(cls):
            bonus += 2
            break
    # Link is inside a heading
    for parent in a_tag.parents:
        if parent.name in ('h1', 'h2', 'h3', 'h4'):
            bonus += 4
            break
    # Link text length
    link_text = a_tag.get_text(strip=True)
    if len(link_text) > 20:
        bonus += 2
    elif len(link_text) > 10:
        bonus += 1
    # Contains an image
    if a_tag.find('img'):
        bonus += 1
    return min(bonus, 10)


def _is_likely_article_url(parsed, base_domain, dom_bonus=0):
    """Smart heuristic to determine if a URL is likely an article page."""
    path = parsed.path.rstrip('/')
    lower_path = path.lower()
    
    # Must be same domain (or www. variant)
    if not _domains_match(parsed.netloc, base_domain):
        return False
    
    # Skip root, hash-only, and very short paths
    if not path or path == '/' or len(path) < 5:
        return False
    
    # Exclude known non-article patterns
    excluded_patterns = [
        '/tag/', '/tags/', '/category/', '/categories/', '/author/', '/authors/',
        '/account/', '/api/', '/cdn-cgi/', '/prices/', '/sign-in', '/sign-up',
        '/login', '/register', '/search', '/feed', '/rss', '/sitemap',
        '/privacy', '/terms', '/contact', '/about/', '/jobs', '/careers',
        '/dashboard', '/settings', '/admin', '/wp-admin', '/wp-login',
        '/static/', '/assets/', '/images/', '/img/', '/css/', '/js/',
        '/favicon', '.xml', '.json', '.css', '.js', '.png', '.jpg', '.gif', '.pdf',
        '/page/', '/oldal/', '#', '/cookie', '/adatkezeles', '/impresszum',
        '/felhasznalasi', '/subscribe', '/join', '/create', '/reset',
        '/discord', '/convert', '/claimables', '/explore', '/mindshare',
        '/guests', '/ico-watch',
        '/sponsor/', '/documents/', '/forum/', '/adatlap/', '/szemely/', '/helyszin/',
        '/jegy/', '/csatorna/', '/esemeny/', '/filmek/', '/people/', '/user/',
        '/event_type/', '/osszes-cikk', '/hirlevel', '/archiv', '/rolunk',
        '/szerzoi-jogok', '/podcast/',
        '?oldal=', '?p=', '&utm_',
    ]
    
    # Site-specific exclusion overrides
    base_lower = base_domain.lower()
    site_overrides = set()
    if 'starity.hu' in base_lower:
        site_overrides.add('/magazin/')
    
    for excl in excluded_patterns:
        if excl in site_overrides:
            continue
        if excl in lower_path:
            return False
    
    # Hard reject: single-segment short paths without dashes/numbers
    # These are almost always navigation pages (e.g. /utazas/, /eletmod/, /tech/)
    segments_check = [s for s in path.split('/') if s]
    if (len(segments_check) == 1
            and '-' not in segments_check[0]
            and len(segments_check[0]) < 20
            and not re.search(r'\d', segments_check[0])):
        return False
    
    # Positive signals: Score-based approach
    score = 0
    segments = [s for s in path.split('/') if s]
    
    # Path has 2+ segments (e.g. /category/article-slug)
    if len(segments) >= 2:
        score += 2
    
    # Path contains a dash (classic slug pattern)
    if '-' in path:
        score += 2
    
    # Path contains a date-like pattern (2024, 2025, 2026, etc.)
    if any(f'/{y}/' in path or f'/{y}' == path[-5:] for y in range(2020, 2030)):
        score += 3
    
    # Path has a long final segment (article slugs tend to be 15+ chars)
    if segments and len(segments[-1]) > 15:
        score += 1
    
    # Path length > 15 chars total
    if len(path) > 15:
        score += 1
    
    # Numeric ID in the path (common for Hungarian news sites)
    if re.search(r'/\d{4,}', path):
        score += 2
    
    # Has underscore in slug (some sites use underscores instead of dashes)
    if segments and '_' in segments[-1] and len(segments[-1]) > 10:
        score += 1

    # Minimum score to qualify (dom_bonus from parent context)
    return (score + dom_bonus) >= 2


def discover_rss_feeds(homepage_url, html_content=None):
    """Try to discover native RSS/Atom feeds from a page's HTML.
    
    Checks KNOWN_RSS_FEEDS first, then HTML <link> tags, then common paths,
    then trafilatura as a last resort.
    
    Returns a list of (feed_url, feed_title) tuples.
    """
    feeds = []
    
    # --- Priority 0: Check hardcoded KNOWN_RSS_FEEDS map ---
    domain = get_base_domain(homepage_url).lower().removeprefix('www.')
    domain_with_www = 'www.' + domain
    for lookup in [domain, domain_with_www, get_base_domain(homepage_url)]:
        if lookup in KNOWN_RSS_FEEDS:
            for feed_url in KNOWN_RSS_FEEDS[lookup]:
                feeds.append((feed_url, 'Known RSS'))
            logging.info(f"  📡 Using known RSS feed(s) for {domain}: {[f[0] for f in feeds]}")
            return feeds
    
    try:
        if html_content is None:
            resp, err = _fetch_with_retries(homepage_url, timeout=HTTP_TIMEOUT, max_retries=2)
            if resp:
                html_content = resp.text
            else:
                logging.debug(f"RSS discovery: could not fetch {homepage_url}: {err}")
                return feeds
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for <link> tags with RSS/Atom feed types
        for link_tag in soup.find_all('link', type=True):
            feed_type = link_tag.get('type', '').lower()
            if 'rss' in feed_type or 'atom' in feed_type or 'xml' in feed_type:
                href = link_tag.get('href', '')
                if href:
                    full_url = urljoin(homepage_url, href)
                    # Skip non-RSS endpoints (xmlrpc, opensearch, wlwmanifest, etc.)
                    lower_url = full_url.lower()
                    if any(skip in lower_url for skip in ('xmlrpc', 'opensearch', 'wlwmanifest', 'osd.xml')):
                        continue
                    title = link_tag.get('title', 'RSS Feed')
                    feeds.append((full_url, title))
        
        # Also check common RSS URL patterns if none found
        if not feeds:
            parsed_hp = urlparse(homepage_url)
            base_origin = f"{parsed_hp.scheme}://{parsed_hp.netloc}"
            # Try both the full URL path and the base domain
            bases_to_try = [homepage_url.rstrip('/')]
            if parsed_hp.path.strip('/'):
                bases_to_try.append(base_origin)
            common_paths = [
                '/feed', '/rss', '/rss.xml', '/feed.xml', '/atom.xml', '/index.xml',
                '/rss/', '/blog/feed/', '/?feed=rss2', '/feed/rss2/',
                '/blog/rss.xml', '/news/feed/', '/articles/feed/',
                '/hirek/rss',  # Hungarian sites
            ]
            found_rss = False
            for base in bases_to_try:
                if found_rss:
                    break
                for rss_path in common_paths:
                    try:
                        test_url = base + rss_path
                        resp, _ = _fetch_with_retries(test_url, timeout=8, max_retries=2)
                        if resp and resp.status_code == 200:
                            ct = resp.headers.get('content-type', '').lower()
                            text_start = resp.text[:500].lower()
                            if 'xml' in ct or '<rss' in text_start or '<feed' in text_start or '<channel' in text_start:
                                feeds.append((test_url, 'Discovered RSS'))
                                found_rss = True
                                break
                    except Exception:
                        continue
                    
    except Exception as e:
        logging.debug(f"RSS discovery failed for {homepage_url}: {e}")

    # --- Trafilatura feed discovery fallback (with timeout) ---
    if not feeds and HAS_TRAFILATURA:
        try:
            traf_feeds = _run_with_timeout(_traf_find_feeds, (homepage_url,), FEED_DISCOVERY_TIMEOUT,
                                           label=f"trafilatura feed discovery for {homepage_url}")
            if traf_feeds:
                for tf_url in list(traf_feeds)[:3]:
                    feeds.append((tf_url, 'Trafilatura Discovered'))
                logging.info(f"  📡 trafilatura found {len(traf_feeds)} feed(s) for {homepage_url}")
        except Exception as e:
            logging.debug(f"Trafilatura feed discovery failed for {homepage_url}: {e}")

    return feeds


def _run_with_timeout(func, args, timeout, label="function"):
    """Run a function in a daemon thread with a hard timeout.
    
    On timeout, the daemon thread is abandoned (it will be killed when
    the process exits). Returns None on timeout.
    """
    result_holder = [None]
    error_holder = [None]
    done_event = threading.Event()

    def _worker():
        try:
            result_holder[0] = func(*args)
        except Exception as e:
            error_holder[0] = e
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    finished = done_event.wait(timeout=timeout)
    if not finished:
        logging.warning(f"  ⏰ Timeout ({timeout}s) exceeded for {label} — abandoning")
        return None
    if error_holder[0]:
        raise error_holder[0]
    return result_holder[0]


def discover_sitemap_urls(homepage_url, max_urls=20):
    """Discover recent article URLs from sitemap.xml using trafilatura (with timeout)."""
    if not HAS_TRAFILATURA:
        return []
    try:
        urls = _run_with_timeout(_traf_sitemap_search, (homepage_url,), SITEMAP_TIMEOUT,
                                label=f"sitemap discovery for {homepage_url}")
        if not urls:
            return []
        # Take the last N URLs (typically most recent)
        base_domain = get_base_domain(homepage_url)
        filtered = []
        for u in list(urls)[-max_urls * 2:]:
            parsed = urlparse(u)
            if _domains_match(parsed.netloc, base_domain):
                filtered.append(u)
        return filtered[-max_urls:]
    except Exception as e:
        logging.debug(f"Sitemap discovery failed for {homepage_url}: {e}")
        return []


def parse_rss_feed(feed_url, max_items=20):
    """Parse a native RSS/Atom feed and return article data dicts."""
    articles = []
    try:
        response, err = _fetch_with_retries(feed_url, timeout=HTTP_TIMEOUT, max_retries=3)
        if not response:
            logging.warning(f"All impersonation profiles failed for feed {feed_url}: {err}")
            return articles
        
        # Try XML parser first (requires lxml), fall back to html.parser
        try:
            soup = BeautifulSoup(response.content, 'xml')
        except Exception:
            logging.debug(f"XML parser unavailable/failed for {feed_url}, falling back to html.parser")
            soup = BeautifulSoup(response.content, 'html.parser')
        
        items = soup.find_all('item') or soup.find_all('entry')
        
        for item in items[:max_items]:
            title = item.find('title')
            link = item.find('link')
            desc = item.find('description') or item.find('summary') or item.find('content')
            pub_date = item.find('pubDate') or item.find('published') or item.find('updated')
            
            # Extract link - for Atom feeds, link is in href attribute
            article_url = ''
            if link:
                article_url = link.get('href', '') or link.get_text(strip=True)
            
            # Strip URL fragments (e.g. #comment-209) to avoid phantom duplicates
            if '#' in article_url:
                article_url = article_url.split('#')[0]
            
            if not title or not article_url:
                continue
            
            # Try to extract image from description/content
            image = ''
            if desc:
                desc_soup = BeautifulSoup(desc.get_text(), 'html.parser')
                img_tag = desc_soup.find('img')
                if img_tag:
                    image = img_tag.get('src', '')
            
            # Also check for media:content or enclosure
            if not image:
                media = item.find('media:content') or item.find('media:thumbnail') or item.find('enclosure')
                if media:
                    image = media.get('url', '')
            
            # Parse date
            parsed_date = None
            if pub_date:
                date_text = pub_date.get_text(strip=True)
                parsed_date = _parse_date_string(date_text)
            
            if not parsed_date:
                parsed_date = _dt.datetime.now(timezone.utc)
            
            desc_text = ''
            if desc:
                # Strip HTML tags for a clean description
                desc_text = BeautifulSoup(desc.get_text(), 'html.parser').get_text(strip=True)[:500]

            # Extract categories from <category> tags
            categories = []
            for cat_tag in item.find_all('category'):
                cat_text = cat_tag.get_text(strip=True)
                if cat_text:
                    categories.append(cat_text)

            # Extract author
            authors = []
            author_tag = item.find('author') or item.find('dc:creator')
            if author_tag:
                author_text = author_tag.get_text(strip=True)
                if author_text:
                    authors = [author_text]

            articles.append({
                'title': title.get_text(strip=True),
                'url': article_url,
                'description': desc_text or title.get_text(strip=True),
                'image': image,
                'published_date': parsed_date,
                'authors': authors,
                'categories': categories,
                'source': 'native_rss'
            })
    
    except Exception as e:
        logging.warning(f"Failed to parse RSS feed {feed_url}: {e}")
    
    return articles


def find_article_links(homepage_url, max_links=20, known_urls=None):
    logging.info(f"Fetching homepage: {homepage_url}")
    if known_urls is None:
        known_urls = set()
    response, err = _fetch_with_retries(homepage_url, timeout=HTTP_TIMEOUT)
    if not response:
        logging.error(f"Failed to fetch {homepage_url}: {err}")
        return [], None

    html_content = response.text
    soup = BeautifulSoup(response.content, 'html.parser')
    base_domain = get_base_domain(homepage_url)
    links = set()
    
    for a in soup.find_all('a', href=True):
        href = a['href']
        full_url = urljoin(homepage_url, href)
        parsed = urlparse(full_url)

        # Remove fragment and query for cleaner URLs
        clean_url = parsed._replace(fragment='').geturl()

        dom_bonus = _score_link_dom_context(a)
        if _is_likely_article_url(parsed, base_domain, dom_bonus=dom_bonus):
            normalized = clean_url.rstrip('/')
            if normalized not in known_urls:
                links.add(clean_url)
                
    logging.info(f"Found {len(links)} new potential article links (already-known URLs skipped).")
    
    # Return a subset to avoid taking too long
    return list(links)[:max_links], html_content

def extract_with_trafilatura(html_content, url):
    """Fallback extraction using trafilatura. Returns dict or None."""
    if not HAS_TRAFILATURA:
        return None
    try:
        result = trafilatura.bare_extraction(
            html_content, url=url, as_dict=True,
            include_images=True, favor_recall=True,
            max_tree_size=500000
        )
        if not result:
            return None
        pub_date = _parse_date_string(result.get('date', ''))
        authors = []
        author = result.get('author', '')
        if author:
            authors = [a.strip() for a in author.split(';') if a.strip()]
        categories = []
        cats = result.get('categories', '') or result.get('tags', '')
        if isinstance(cats, str) and cats:
            categories = [c.strip() for c in cats.split(';') if c.strip()]
        elif isinstance(cats, list):
            categories = cats
        return {
            'title': result.get('title', ''),
            'url': url,
            'description': (result.get('description', '') or (result.get('text', '') or '')[:300]),
            'image': result.get('image', ''),
            'published_date': pub_date,
            'authors': authors,
            'categories': categories
        }
    except Exception as e:
        logging.debug(f"Trafilatura extraction failed for {url}: {e}")
        return None


def extract_article_data(url, html_override=None):
    """Multi-extractor pipeline: meta tags > newspaper3k > trafilatura.
    
    Thread-safe: uses HTTP_TIMEOUT on requests instead of SIGALRM.
    """
    logging.info(f"Extracting data from: {url}")

    _extract_start = time.monotonic()

    try:
        # --- Fetch HTML ---
        if html_override:
            html = html_override
        else:
            resp, err = _fetch_with_retries(url, timeout=HTTP_TIMEOUT, max_retries=3)
            if resp:
                html = resp.text
            else:
                # All curl_cffi profiles failed — try LightPanda/Playwright as last resort
                logging.debug(f"All impersonation profiles failed for {url}: {err}, trying JS renderer...")
                html = fetch_js_rendered(url) or ''
            # JS renderer fallback if response is too short on JS-heavy sites
            if len(html) < 1000 and _is_js_site(url):
                js_html = fetch_js_rendered(url)
                if js_html and len(js_html) > len(html):
                    html = js_html
            if not html or len(html) < 100:
                logging.warning(f"Could not fetch article HTML for {url}")
                return None

        # Check time budget
        if time.monotonic() - _extract_start > ARTICLE_TIMEOUT:
            logging.error(f"Timeout extracting article {url} (>{ARTICLE_TIMEOUT}s)")
            return None

        # --- 1. Meta tags extraction ---
        meta = extract_meta_tags(html, url)

        # --- 2. newspaper3k extraction ---
        np_title = ''
        np_desc = ''
        np_image = ''
        np_date = None
        np_authors = []
        try:
            article = newspaper.Article(url)
            article.set_html(html)
            article.parse()
            np_title = article.title or ''
            np_desc = article.meta_description or ''
            if not np_desc and article.text:
                np_desc = article.text[:300].strip()
                if len(article.text) > 300:
                    np_desc += '...'
            np_image = article.top_image or ''
            np_date = article.publish_date
            np_authors = article.authors or []
        except Exception as e:
            logging.debug(f"newspaper3k failed for {url}: {e}")

        # --- 3. Trafilatura fallback (if newspaper result is weak) ---
        traf = None
        if len(np_title.strip()) < 3 or not np_desc:
            traf = extract_with_trafilatura(html, url)

        # --- 4. HTML <title> fallback ---
        html_title = ''
        soup = BeautifulSoup(html, 'html.parser')
        title_tag = soup.find('title')
        if title_tag:
            html_title = title_tag.get_text(strip=True)

        # --- 5. Merge: meta_tags > newspaper > trafilatura > html ---
        title = (
            meta.get('title') or np_title or
            (traf.get('title', '') if traf else '') or html_title
        )
        description = (
            meta.get('description') or np_desc or
            (traf.get('description', '') if traf else '')
        )
        image = (
            meta.get('image') or np_image or
            (traf.get('image', '') if traf else '')
        )
        published_date = (
            np_date or meta.get('published_date') or
            (traf.get('published_date') if traf else None)
        )
        if not published_date:
            published_date = _dt.datetime.now(timezone.utc)
        authors = (
            np_authors or meta.get('authors', []) or
            (traf.get('authors', []) if traf else [])
        )
        categories = (
            meta.get('categories', []) or
            (traf.get('categories', []) if traf else [])
        )

        # Quality gate: reject junk results
        if not title or len(title.strip()) < 3:
            logging.debug(f"Rejecting article {url}: title too short ({title!r})")
            return None

        return {
            'title': title.strip(),
            'url': url,
            'description': description,
            'image': image,
            'published_date': published_date,
            'authors': authors,
            'categories': categories
        }
    except Exception as e:
        logging.error(f"Failed parsing article {url}: {e}")
        return None

def generate_rss(site_url, articles, output_file=None):
    if not articles:
        articles = []

    if not output_file:
        parsed_site = urlparse(site_url)
        site_domain = get_base_domain(site_url)
        slug = site_domain.replace('.', '_')
        path_part = parsed_site.path.strip('/').replace('/', '_')
        if path_part:
            slug = f"{slug}_{path_part}"
        output_file = os.path.join(RSS_FEEDS_DIR, f"{slug}_feed.xml")

    # Guard: never overwrite a good feed with a worse result
    if os.path.exists(output_file):
        existing = get_feed_info(output_file)
        new_count = sum(1 for a in articles if a and a.get('title'))
        if existing['item_count'] > 0 and new_count < existing['item_count'] // 2:
            logging.warning(
                f"New feed for {site_url} has only {new_count} items vs existing {existing['item_count']} — keeping existing feed."
            )
            _register_feed(output_file)
            return output_file

    site_domain = get_base_domain(site_url)
    
    fg = FeedGenerator()
    fg.id(site_url)
    fg.title(f"{site_domain} - Generated RSS")
    fg.author({'name': 'RSS Creator App'})
    fg.link(href=site_url, rel='alternate')
    fg.description(f"Auto-generated RSS feed for {site_domain}")
    fg.language('hu')
    
    count = 0
    for art in articles:
        if not art or not art.get('title'):
            continue
            
        fe = fg.add_entry()
        fe.id(art['url'])
        fe.title(art['title'])
        fe.link(href=art['url'])
        
        # Description formatting
        desc = art.get('description', '')
        fe.description(desc)

        # Image as enclosure (accessible to feedparser as entry.enclosures)
        if art.get('image'):
            fe.enclosure(art['image'], 0, 'image/jpeg')
        
        if art.get('authors'):
            fe.author([{'name': a} for a in art['authors']])
            
        pub_date = art.get('published_date')
        if pub_date:
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            fe.published(pub_date)

        if art.get('categories'):
            for cat in art['categories']:
                fe.category({'term': cat})

        count += 1

    fg.rss_file(output_file)
    logging.info(f"Successfully generated RSS with {count} entries: {output_file}")

    _register_feed(output_file)

    return output_file

def process_url(url, max_links, output_file, known_urls=None, failed_urls=None, force=False):
    start_time = time.time()
    log_entry = {
        "url": url,
        "start_time": _dt.datetime.now(timezone.utc).isoformat(),
        "success": False,
        "articles_found": 0,
        "articles_processed": 0,
        "rss_source": "scrape",
        "error": None
    }
    
    if failed_urls is None:
        failed_urls = {}
    
    if not url.startswith('http'):
        url = 'https://' + url
        log_entry["url"] = url
        
    try:
        logging.info(f"--- Processing URL: {url} ---")

        parsed_site = urlparse(url)
        site_domain = get_base_domain(url)
        domain_clean = site_domain.lower().removeprefix('www.')
        domain_with_www = 'www.' + domain_clean

        # Check if this site has a known native RSS feed first
        known_feeds = []
        for lookup in [domain_clean, domain_with_www, site_domain]:
            if lookup in KNOWN_RSS_FEEDS:
                known_feeds = KNOWN_RSS_FEEDS[lookup]
                break

        if known_feeds:
            logging.info(f"  📡 Using known native RSS feed(s) directly for {site_domain}: {known_feeds}")
            for feed_url in known_feeds:
                _register_feed(feed_url)
            log_entry["success"] = True
            log_entry["rss_source"] = "native_rss_known"
            log_entry["duration_seconds"] = round(time.time() - start_time, 2)
            log_entry["end_time"] = _dt.datetime.now(timezone.utc).isoformat()
            return log_entry
        
        # --- Skip if RSS feed already generated for this URL today (with sufficient items) ---
        site_domain = get_base_domain(url)
        slug = site_domain.replace('.', '_')
        path_part = parsed_site.path.strip('/').replace('/', '_')
        if path_part:
            slug = f"{slug}_{path_part}"
        expected_file = os.path.join(RSS_FEEDS_DIR, f"{slug}_feed.xml")
        
        if not force and os.path.exists(expected_file):
            feed_info = get_feed_info(expected_file)
            existing_items = feed_info['item_count']
            # Check staleness: skip only if feed is fresh (< FEED_MAX_AGE_HOURS)
            is_fresh = False
            if feed_info['last_build_date']:
                build_dt = feed_info['last_build_date']
                if build_dt.tzinfo is None:
                    build_dt = build_dt.replace(tzinfo=timezone.utc)
                age_hours = (_dt.datetime.now(timezone.utc) - build_dt).total_seconds() / 3600
                is_fresh = age_hours < FEED_MAX_AGE_HOURS
            else:
                # Fallback: check file modification time
                file_age_hours = (time.time() - os.path.getmtime(expected_file)) / 3600
                is_fresh = file_age_hours < FEED_MAX_AGE_HOURS

            if existing_items >= MIN_ITEMS_TO_SKIP and is_fresh:
                logging.info(f"  ⏩ Skipping (RSS already exists with {existing_items} items): {expected_file}")
                _register_feed(expected_file)
                log_entry["success"] = True
                log_entry["skipped"] = True
                log_entry["existing_items"] = existing_items
                log_entry["duration_seconds"] = round(time.time() - start_time, 2)
                log_entry["end_time"] = _dt.datetime.now(timezone.utc).isoformat()
                return log_entry
            else:
                if existing_items < MIN_ITEMS_TO_SKIP:
                    logging.info(f"  🔄 Regenerating (existing feed only has {existing_items} items): {expected_file}")
                else:
                    logging.info(f"  🔄 Regenerating (feed is stale, {existing_items} items): {expected_file}")
        
        # --- STEP 1: Try to find and use a native RSS feed ---
        html_content = None
        native_articles = []
        
        resp, fetch_err = _fetch_with_retries(url, timeout=HTTP_TIMEOUT)
        if resp:
            html_content = resp.text
        else:
            logging.warning(f"All curl_cffi profiles failed for {url}: {fetch_err}")
            # LightPanda/Playwright fallback for homepage fetch
            html_content = fetch_js_rendered(url)
            if not html_content:
                logging.error(f"All fetch methods (curl_cffi + LightPanda + Playwright) failed for {url}")
                log_entry["error"] = f"Homepage fetch failed: {fetch_err}"
                log_entry["duration_seconds"] = round(time.time() - start_time, 2)
                log_entry["end_time"] = _dt.datetime.now(timezone.utc).isoformat()
                return log_entry
        
        discovered_feeds = discover_rss_feeds(url, html_content)
        # Filter out irrelevant general feeds when processing a specific subpage
        if discovered_feeds:
            discovered_feeds = [(fu, ft) for fu, ft in discovered_feeds if _is_feed_relevant(fu, url)]
        
        # If <link> feeds were found but all filtered as irrelevant for this subpage,
        # try constructing subpage-specific feed URLs (e.g. /category/tech/ → /category/tech/feed)
        if not discovered_feeds:
            target_path = urlparse(url).path.strip('/')
            if target_path:  # only for subpages, not root
                subpage_feed_paths = ['/feed', '/rss', '/feed/', '/rss/']
                base_url = url.rstrip('/')
                for sfp in subpage_feed_paths:
                    try:
                        test_url = base_url + sfp
                        resp_sub, _ = _fetch_with_retries(test_url, timeout=8, max_retries=2)
                        if resp_sub and resp_sub.status_code == 200:
                            ct = resp_sub.headers.get('content-type', '').lower()
                            text_start = resp_sub.text[:500].lower()
                            if 'xml' in ct or '<rss' in text_start or '<feed' in text_start or '<channel' in text_start:
                                discovered_feeds.append((test_url, 'Subpage-specific RSS'))
                                logging.info(f"  📡 Found subpage-specific feed: {test_url}")
                                break
                    except Exception:
                        continue
        
        if discovered_feeds:
            logging.info(f"  📡 Discovered native RSS feed(s) dynamically. Registering directly:")
            for feed_url, feed_title in discovered_feeds:
                logging.info(f"    Feed: {feed_title} -> {feed_url}")
                _register_feed(feed_url)
            log_entry["success"] = True
            log_entry["rss_source"] = "native_rss_discovered"
            log_entry["duration_seconds"] = round(time.time() - start_time, 2)
            log_entry["end_time"] = _dt.datetime.now(timezone.utc).isoformat()
            return log_entry
        
        # --- STEP 2: Also scrape the homepage for links (supplement native RSS) ---
        soup = BeautifulSoup(html_content, 'html.parser')
        base_domain = get_base_domain(url)
        scraped_links = set()
        
        # Collect native article URLs for dedup
        native_urls = {a.get('url', '').rstrip('/') for a in native_articles}
        combined_known = (known_urls or set()) | native_urls
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(url, href)
            parsed = urlparse(full_url)
            clean_url = parsed._replace(fragment='').geturl()

            dom_bonus = _score_link_dom_context(a_tag)
            if _is_likely_article_url(parsed, base_domain, dom_bonus=dom_bonus):
                normalized = clean_url.rstrip('/')
                if normalized not in combined_known:
                    scraped_links.add(clean_url)
        
        logging.info(f"  Found {len(native_articles)} from native RSS + {len(scraped_links)} scraped links")

        # --- STEP 2b: Sitemap fallback if very few results ---
        if len(native_articles) + len(scraped_links) < 5:
            sitemap_urls = discover_sitemap_urls(url, max_urls=20)
            if sitemap_urls:
                new_from_sitemap = 0
                for sm_url in sitemap_urls:
                    normalized = sm_url.rstrip('/')
                    if normalized in combined_known or sm_url in scraped_links:
                        continue
                    # Filter through article heuristic
                    sm_parsed = urlparse(sm_url)
                    if not _is_likely_article_url(sm_parsed, base_domain):
                        continue
                    scraped_links.add(sm_url)
                    new_from_sitemap += 1
                if new_from_sitemap:
                    logging.info(f"  🗺️ Added {new_from_sitemap} URLs from sitemap.xml")

        # Determine how many more articles we need from scraping
        remaining_slots = max_links - len(native_articles)
        scraped_links_list = list(scraped_links)[:max(remaining_slots, 0)]
        
        log_entry["articles_found"] = len(native_articles) + len(scraped_links_list)
        log_entry["native_rss_count"] = len(native_articles)
        log_entry["scraped_links_count"] = len(scraped_links_list)
        
        # --- STEP 3: Extract article data from scraped links ---
        scraped_articles = []
        for a_url in scraped_links_list:
            # Skip URLs that have already failed 2+ times
            if failed_urls.get(a_url, 0) >= 2:
                logging.debug(f"Skipping blacklisted URL: {a_url}")
                continue
            data = extract_article_data(a_url)
            if data:
                scraped_articles.append(data)
            else:
                blacklisted = record_failed_url(a_url, failed_urls)
                if blacklisted:
                    logging.info(f"  🚫 Blacklisted (2+ failures): {a_url}")
        
        # --- STEP 4: JS rendering fallback (LightPanda → Playwright) ---
        all_articles = native_articles + scraped_articles
        if len(all_articles) < 3 and (_is_js_site(url) or len(all_articles) == 0):
            js_html = fetch_js_rendered(url)
            if js_html and len(js_html) > 500:
                logging.info(f"  🐼 Re-processing {url} with JS-rendered HTML")
                js_soup = BeautifulSoup(js_html, 'html.parser')
                js_links = set()
                js_existing = combined_known | {a.get('url', '').rstrip('/') for a in all_articles}
                for a_tag in js_soup.find_all('a', href=True):
                    href = a_tag['href']
                    full_url = urljoin(url, href)
                    parsed = urlparse(full_url)
                    clean_url = parsed._replace(fragment='').geturl()
                    dom_bonus = _score_link_dom_context(a_tag)
                    if _is_likely_article_url(parsed, base_domain, dom_bonus=dom_bonus):
                        normalized = clean_url.rstrip('/')
                        if normalized not in js_existing:
                            js_links.add(clean_url)
                if js_links:
                    logging.info(f"  🐼 Found {len(js_links)} new links from JS render")
                    js_remaining = max_links - len(all_articles)
                    for js_url in list(js_links)[:max(js_remaining, 0)]:
                        if failed_urls.get(js_url, 0) >= 2:
                            continue
                        data = extract_article_data(js_url)
                        if data:
                            all_articles.append(data)
                        else:
                            record_failed_url(js_url, failed_urls)

        # --- STEP 5: Combine and generate RSS ---
        log_entry["articles_processed"] = len(all_articles)
        
        if native_articles:
            log_entry["rss_source"] = "native+scrape" if scraped_articles else "native_rss"
        else:
            log_entry["rss_source"] = "scrape"
        
        generated_file = generate_rss(url, all_articles, output_file)
        
        log_entry["success"] = True
    except Exception as e:
        logging.error(f"Error processing {url}: {e}")
        log_entry["error"] = str(e)
        
    log_entry["duration_seconds"] = round(time.time() - start_time, 2)
    log_entry["end_time"] = _dt.datetime.now(timezone.utc).isoformat()
    return log_entry

def _is_feed_relevant(feed_url, target_url):
    """Check if a discovered RSS feed is relevant to the target URL.
    
    Rejects general site-wide feeds (e.g. /rss, /feed) when the target
    is a specific subpage (e.g. /tudomany/til).
    """
    target_parsed = urlparse(target_url)
    feed_parsed = urlparse(feed_url)
    target_path = target_parsed.path.strip('/')
    feed_path = feed_parsed.path.strip('/')
    
    # If target is the site root, any feed is relevant
    if not target_path or target_path in ('', 'index.html', 'index.php'):
        return True
    
    # Reject opensearch.xml — not an RSS feed
    if 'opensearch' in feed_path.lower():
        logging.info(f"    ❌ Skipping non-RSS feed: {feed_url}")
        return False
    
    # Get significant path segments of the target (e.g. ['tudomany', 'til'])
    target_segments = [s for s in target_path.split('/') if s and len(s) > 1]
    
    # If the feed URL contains at least one significant segment from the target path, it's relevant
    feed_path_lower = feed_path.lower()
    for seg in target_segments:
        if seg.lower() in feed_path_lower:
            return True
    
    # Generic feed paths that don't match the target subpage — skip
    generic_feed_patterns = (
        'rss', 'feed', 'atom', 'feeds', 'rss.xml', 'feed.xml',
        'atom.xml', 'index.xml', 'rss2', 'feed/rss', 'feed/atom',
    )
    if feed_path.lower().rstrip('/') in generic_feed_patterns or feed_path.lower().split('/')[-1] in ('rss', 'feed', 'atom.xml', 'rss.xml', 'feed.xml'):
        logging.info(f"    ❌ Skipping general feed '{feed_url}' (not specific to subpage '{target_path}')")
        return False
    
    # Default: accept
    return True


def main():
    parser = argparse.ArgumentParser(description="RSS Creator App - Generate RSS feed from any news site.")
    parser.add_argument("-u", "--url", help="A single URL of the website to scrape.")
    parser.add_argument("-i", "--input", default=os.path.join("Input", "rss_creator.txt"), help="Path to a text file containing a list of URLs.")
    parser.add_argument("-o", "--output", help="Output XML file name (optional, only used for single URL).")
    parser.add_argument("-m", "--max-links", type=int, default=20, help="Maximum number of articles to scrape per site.")
    parser.add_argument("-l", "--log", default=os.path.join(DAILY_OUTPUT_DIR, "rss_scraper_log.json"), help="Path to the JSON log file.")
    parser.add_argument("-f", "--force", action="store_true", help="Force regeneration of all feeds (ignore cache).")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Number of parallel workers (default: {DEFAULT_WORKERS}, max: 20).")
    
    args = parser.parse_args()
    args.workers = max(1, min(args.workers, 20))  # Clamp to 1..20
        
    # Clear the generated_feeds.txt file if it exists so we start fresh for this run
    with _registered_feeds_lock:
        _registered_feeds.clear()
    if os.path.exists(GENERATED_FEEDS_FILE):
        try:
            os.remove(GENERATED_FEEDS_FILE)
        except Exception as e:
            logging.error(f"Failed to clear {GENERATED_FEEDS_FILE}: {e}")
            
    urls_to_process = []
    
    if args.url:
        urls_to_process.append(args.url)
    elif args.input:
        try:
            with open(args.input, 'r', encoding='utf-8') as f:
                urls_to_process = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except Exception as e:
            logging.error(f"Failed to read input file {args.input}: {e}")
            return
        
    logs = []
    known_urls = load_known_urls()
    failed_urls = load_failed_urls()
    logging.info(f"Loaded {len(failed_urls)} failed URLs from blacklist ({sum(1 for c in failed_urls.values() if c >= 2)} blacklisted).")
    
    total_start = time.time()
    success_count = 0
    fail_count = 0
    total_urls = len(urls_to_process)

    def _process_one(idx_url):
        """Worker function for parallel processing."""
        idx, url = idx_url
        logging.info(f"[{idx}/{total_urls}] Processing: {url}")
        out_file = args.output if total_urls == 1 else None
        try:
            log_entry = process_url(url, args.max_links, out_file, known_urls, failed_urls, force=args.force)
            return log_entry
        except Exception as e:
            logging.error(f"Unexpected error processing {url}: {e}")
            return {
                "url": url,
                "success": False,
                "error": f"Unexpected: {e}",
                "duration_seconds": 0
            }

    try:
        if total_urls <= 1 or args.workers <= 1:
            # Sequential processing for single URL or --workers=1
            # Use _run_with_timeout to prevent any single URL from hanging the pipeline
            for i, url in enumerate(urls_to_process, 1):
                try:
                    result = _run_with_timeout(
                        _process_one, ((i, url),), PER_URL_TIMEOUT,
                        label=f"process_url({url})"
                    )
                    if result is None:
                        # Timeout — _run_with_timeout returned None
                        result = {
                            "url": url,
                            "success": False,
                            "error": f"Per-URL timeout ({PER_URL_TIMEOUT}s) exceeded (sequential)",
                            "duration_seconds": PER_URL_TIMEOUT
                        }
                except Exception as e:
                    result = {
                        "url": url,
                        "success": False,
                        "error": f"Unexpected: {e}",
                        "duration_seconds": 0
                    }
                logs.append(result)
                if result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
        else:
            # Parallel processing
            logging.info(f"🚀 Starting parallel processing with {args.workers} workers for {total_urls} URLs")
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_to_url = {}
                for i, url in enumerate(urls_to_process, 1):
                    future = executor.submit(_process_one, (i, url))
                    future_to_url[future] = url
                
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        result = future.result(timeout=PER_URL_TIMEOUT)
                        logs.append(result)
                        if result.get("success"):
                            success_count += 1
                        else:
                            fail_count += 1
                    except TimeoutError:
                        logging.error(f"⏰ Per-URL timeout ({PER_URL_TIMEOUT}s) exceeded for {url}")
                        logs.append({
                            "url": url,
                            "success": False,
                            "error": f"Per-URL timeout ({PER_URL_TIMEOUT}s) exceeded",
                            "duration_seconds": PER_URL_TIMEOUT
                        })
                        fail_count += 1
                    except Exception as e:
                        logging.error(f"Worker error for {url}: {e}")
                        logs.append({
                            "url": url,
                            "success": False,
                            "error": f"Worker error: {e}",
                            "duration_seconds": 0
                        })
                        fail_count += 1

        # --- Process failed RSS feeds passed by news_filter (before playwright cleanup) ---
        fallback_path = os.path.join(DAILY_OUTPUT_DIR, 'failed_feeds_for_rss_creator.txt')
        # news_filter may still be writing the fallback file — wait up to 90s
        if not os.path.exists(fallback_path):
            logging.info("Waiting up to 90s for failed RSS feed fallback file from news_filter...")
            for _ in range(18):
                time.sleep(5)
                if os.path.exists(fallback_path):
                    break
        if os.path.exists(fallback_path):
            try:
                with open(fallback_path, 'r', encoding='utf-8') as f:
                    fallback_urls = [line.strip() for line in f if line.strip()]
                already_done = set(u.rstrip('/') for u in urls_to_process)
                fallback_urls = [u for u in fallback_urls if u.rstrip('/') not in already_done]
                if fallback_urls:
                    logging.info(f"♻️ Processing {len(fallback_urls)} homepage(s) from failed RSS feeds...")
                    for i, url in enumerate(fallback_urls, 1):
                        try:
                            result = _run_with_timeout(
                                _process_one, ((f"F{i}", url),), PER_URL_TIMEOUT,
                                label=f"fallback({url})"
                            )
                            if result is None:
                                result = {"url": url, "success": False,
                                          "error": f"Fallback timeout ({PER_URL_TIMEOUT}s)", "duration_seconds": PER_URL_TIMEOUT}
                            logs.append(result)
                            if result.get("success"):
                                success_count += 1
                                logging.info(f"  ✅ Fallback success: {url}")
                            else:
                                fail_count += 1
                        except Exception as e:
                            logging.error(f"  Fallback error for {url}: {e}")
                            fail_count += 1
                os.remove(fallback_path)
            except Exception as e:
                logging.error(f"Failed to process fallback file: {e}")

    finally:
        cleanup_playwright()
        save_failed_urls(failed_urls)
        
    total_duration = round(time.time() - total_start, 2)
    
    # Write aggregated logs
    try:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=4, ensure_ascii=False)
        logging.info(f"Run complete in {total_duration}s. Success: {success_count}, Failed: {fail_count}. Workers: {args.workers}. Log: {args.log}")
    except Exception as e:
        logging.error(f"Failed to write log file {args.log}: {e}")

if __name__ == "__main__":
    main()