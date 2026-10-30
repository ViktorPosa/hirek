"""
LEÍRÁS:
Perplexity AI kliens (Pro/Reasoning mód támogatással).
A 'perplexity-ai' könyvtárat használja sütikkel a Pro funkciók eléréséhez.
Támogatja a 'reasoning' (Deep Thinking) és 'pro' módokat, valamint automatikus sütifrissítést Seleniummal.

BEMENET:
- Promptok
- Sütik (Input/perplexity_cookies.json)
- Beállítások (mód, modell)

KIMENET:
- (válasz_szöveg, sikeresség, auth_hiba_jelző)
"""


import sys
import os
import json
import threading

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

try:
    import perplexity
except ImportError:
    print("❌ perplexity-ai package not installed!")
    print("   Run: pip install git+https://github.com/helallao/perplexity-ai.git")
    perplexity = None

# --- CONFIGURATION ---
COOKIE_PATH = os.path.join(os.path.dirname(__file__), 'Input', 'perplexity_cookies.json')

# Thread-safe counters for statistics
_stats_lock = threading.Lock()
_success_count = 0
_failure_count = 0

# Client instance (reused for performance)
_client = None
_client_lock = threading.Lock()

# Track if we've already tried refreshing cookies this session
_cookie_refresh_attempted = False


def reset_stats():
    """Reset the processing statistics."""
    global _success_count, _failure_count
    with _stats_lock:
        _success_count = 0
        _failure_count = 0


def get_stats():
    """Get current processing statistics."""
    with _stats_lock:
        return {
            'success_count': _success_count,
            'failure_count': _failure_count
        }


def print_stats():
    """Print processing statistics summary."""
    stats = get_stats()
    total = stats['success_count'] + stats['failure_count']
    if total > 0:
        print(f"\n📊 Perplexity Processing Summary:")
        print(f"   ✅ Successful: {stats['success_count']} batches")
        if stats['failure_count'] > 0:
            print(f"   ❌ Failed: {stats['failure_count']} batches")


def _load_cookies():
    """Load cookies from the JSON file."""
    if not os.path.exists(COOKIE_PATH):
        return None
    
    try:
        with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        # Check for placeholder values
        for key, value in cookies.items():
            if 'YOUR_' in str(value) or 'HERE' in str(value):
                print(f"    ⚠️ Cookie file contains placeholder values")
                return None
        
        return cookies
    except Exception as e:
        print(f"    ⚠️ Failed to load Perplexity cookies: {e}")
        return None


def reset_client():
    """Reset the client instance to force re-initialization with fresh cookies."""
    global _client
    with _client_lock:
        _client = None


def _get_client(force_new=False):
    """Get or create Perplexity client instance."""
    global _client
    
    if perplexity is None:
        print("    ❌ Perplexity module not available")
        return None
    
    with _client_lock:
        if _client is not None and not force_new:
            return _client
        
        try:
            cookies = _load_cookies()
            if cookies:
                _client = perplexity.Client(cookies)
                print("    🔑 Perplexity client initialized with Pro account")
            else:
                _client = perplexity.Client()
                print("    ⚠️ Perplexity client initialized without account (limited features)")
            
            return _client
        except Exception as e:
            print(f"    ❌ Failed to create Perplexity client: {e}")
            return None


def try_refresh_cookies():
    """Try to refresh Perplexity cookies automatically via Selenium."""
    global _cookie_refresh_attempted
    
    if _cookie_refresh_attempted:
        return False
    
    _cookie_refresh_attempted = True
    print("    🔄 Attempting to refresh Perplexity cookies via Selenium...")
    
    try:
        # Import the cookie extractor
        import perplexity_cookie_extract
        
        # This will open Chrome, and extract cookies
        success = perplexity_cookie_extract.extract_cookies()
        
        if success:
            print("    ✅ Perplexity cookies refreshed successfully!")
            # Reset client to use new cookies
            reset_client()
            return True
        else:
            print("    ⚠️ Selenium cookie refresh failed")
            return False
            
    except ImportError:
        print("    ⚠️ perplexity_cookie_extract.py not found")
        return False
    except Exception as e:
        print(f"    ⚠️ Cookie refresh failed: {e}")
        return False


def _is_auth_error(error_str):
    """Check if the error is authentication-related."""
    error_lower = error_str.lower()
    auth_keywords = [
        'auth', 'login', 'session', 'cookie', 'expired', 
        'unauthorized', '401', '403', 'permission', 'denied'
    ]
    return any(keyword in error_lower for keyword in auth_keywords)


def call_perplexity(prompt, mode='reasoning', model='gemini-3.1-pro', stream=False):
    """
    Call Perplexity API.
    
    Args:
        prompt: User prompt/content
        mode: Search mode ('auto', 'pro', 'reasoning', 'deep research')
        model: Model to use ('gemini-3.1-pro' for reasoning, None for default)
        stream: Whether to stream response
        
    Returns:
        (content, success, is_auth_error) tuple
    """
    global _success_count, _failure_count
    
    client = _get_client()
    if not client:
        with _stats_lock:
            _failure_count += 1
        return None, False, True  # Consider no client as auth error
    
    try:
        print(f"    🔍 Calling Perplexity ({mode} mode, model: {model or 'default'})...")
        
        search_kwargs = {
            'mode': mode,
            'sources': ['web'],
            'stream': stream,
            'language': 'hu-HU',
            'incognito': False
        }
        if model:
            search_kwargs['model'] = model
        
        response = client.search(prompt, **search_kwargs)
        
        if response and 'answer' in response:
            content = response['answer']
            print(f"    ✅ Perplexity response received ({len(content)} chars)")
            with _stats_lock:
                _success_count += 1
            return content, True, False
        else:
            print(f"    ⚠️ Perplexity returned no answer")
            with _stats_lock:
                _failure_count += 1
            return None, False, False
            
    except Exception as e:
        error_str = str(e)
        is_auth = _is_auth_error(error_str)
        
        # Detect loky/executor shutdown errors - reset the client so next call gets a fresh one
        if 'cannot schedule new futures' in error_str or 'interpreter shutdown' in error_str:
            print(f"    ⚠️ Perplexity internal executor died. Resetting client...")
            reset_client()
        
        if len(error_str) > 100:
            error_str = error_str[:100] + "..."
        
        error_icon = "🔐" if is_auth else "❌"
        print(f"    {error_icon} Perplexity error: {error_str}")
        
        with _stats_lock:
            _failure_count += 1
        return None, False, is_auth


def _get_perplexity_settings():
    """Load mode and model from pipeline_settings.json."""
    settings_file = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')
    try:
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
            if 'perplexity' in settings:
                p_mode = settings['perplexity'].get('mode', 'pro')
                p_model = settings['perplexity'].get('model', 'claude-4.6-sonnet')
                return p_mode, p_model
    except Exception as e:
        print(f"    ⚠️ Failed to load perplexity settings: {e}")
    return 'reasoning', 'gemini-3.1-pro-preview'  # Fallbacks

def call_with_fallback(prompt, use_pro=True):
    """
    Call Perplexity API with fallback modes and auto cookie refresh.
    
    Args:
        prompt: User prompt/content
        use_pro: Whether to use reasoning mode (if False, uses auto)
        
    Returns:
        (content, model_used) tuple
    """
    print(f"DEBUG: perplexity_client.call_with_fallback received use_pro={use_pro}")
    
    mode, model = _get_perplexity_settings()
    
    if use_pro:
        content, success, is_auth_error = call_perplexity(prompt, mode=mode, model=model)
        if success and content:
            return content, f"perplexity-{model}-{mode}"
        
        # If auth error, try to refresh cookies
        if is_auth_error:
            if try_refresh_cookies():
                # Retry with refreshed cookies
                print("    🔄 Retrying with refreshed cookies...")
                content, success, _ = call_perplexity(prompt, mode=mode, model=model)
                if success and content:
                    return content, f"perplexity-{model}-{mode}"
        
    # Fallback / Last resort: Gemini Selenium
    print("    🔄 Falling back to Gemini Selenium (last resort)...")
    try:
        import gemini_selenium_client
        content, model_used = gemini_selenium_client.call_with_fallback(prompt, use_pro=use_pro, timeout=120)
        if content:
            return content, model_used or "gemini-selenium-fallback"
    except Exception as e:
        print(f"    ❌ Gemini Selenium fallback failed: {e}")
    
    return None, None


def clean_json_response(content):
    """Clean up potential markdown code blocks from JSON response."""
    if not content:
        return content
    
    if not isinstance(content, str):
        content = str(content)
    
    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    return content.strip()


def check_cookies():
    """Check if cookies file exists and is valid."""
    if not os.path.exists(COOKIE_PATH):
        print(f"❌ Perplexity cookie file not found: {COOKIE_PATH}")
        print("   Run: py perplexity_cookie_extract.py")
        print("   Or manually extract cookies from your browser.")
        return False
    
    try:
        with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        if not isinstance(cookies, dict) or len(cookies) == 0:
            print(f"❌ Perplexity cookie file is empty or invalid format")
            return False
        
        # Check for placeholder values
        for key, value in cookies.items():
            if 'YOUR_' in str(value) or 'HERE' in str(value):
                print(f"❌ Perplexity cookie file contains placeholder values")
                print("   Run: py perplexity_cookie_extract.py")
                return False
        
        print(f"✅ Perplexity cookies loaded ({len(cookies)} keys)")
        return True
        
    except Exception as e:
        print(f"❌ Error reading Perplexity cookie file: {e}")
        return False
