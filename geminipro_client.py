"""
Gemini Pro Client using gemini_chat_api

This module provides Gemini 3 Pro access with fallback to Gemini 3 Flash.
Uses the OEvortex/Gemini-Chat-API library for authentication via cookies.

Models:
- Primary: GEMINI_3_0_PRO
- Fallback: GEMINI_3_0_FLASH
"""

import sys
import os
import threading

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Add gemini_chat_api to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'gemini_chat_api'))

from gemini_chat_api.core import Chatbot, Model

# --- CONFIGURATION ---
COOKIE_PATH = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_cookies.json')

# Thread-safe counters for statistics
_stats_lock = threading.Lock()
_pro_count = 0
_flash_count = 0
_pro_failures = 0

# Rate limit wait mode (set via set_wait_for_rate_limit)
_wait_for_rate_limit = False

# Thread-local storage for chatbot instances
import threading
_thread_local = threading.local()

def _get_thread_local_chatbot():
    if not hasattr(_thread_local, 'chatbot_instance'):
        _thread_local.chatbot_instance = None
        _thread_local.chatbot_model_type = None
    return _thread_local


def set_wait_for_rate_limit(enabled):
    """Enable/disable extended rate limit waiting mode.
    
    When enabled:
    - First rate limit: wait 2 hours
    - Subsequent rate limits: wait 1 hour each
    - Keeps retrying until success
    """
    global _wait_for_rate_limit
    _wait_for_rate_limit = enabled
    if enabled:
        print("    ⏰ Wait-refresh mode enabled: will wait for rate limits")


def reset_stats():
    """Reset the processing statistics."""
    global _pro_count, _flash_count, _pro_failures
    with _stats_lock:
        _pro_count = 0
        _flash_count = 0
        _pro_failures = 0


def get_stats():
    """Get current processing statistics."""
    with _stats_lock:
        return {
            'pro_count': _pro_count,
            'flash_count': _flash_count,
            'pro_failures': _pro_failures
        }


def print_stats():
    """Print processing statistics summary."""
    stats = get_stats()
    total = stats['pro_count'] + stats['flash_count']
    if total > 0:
        print(f"\n📊 Gemini Pro Processing Summary:")
        print(f"   ✅ Gemini 3 Pro: {stats['pro_count']} batches")
        print(f"   🔄 Gemini 3 Flash (fallback): {stats['flash_count']} batches")
        if stats['pro_failures'] > 0:
            print(f"   ⚠️ Pro failures: {stats['pro_failures']}")


def _get_or_create_chatbot(model):
    """Get existing thread-local chatbot or create a new one."""
    tl = _get_thread_local_chatbot()
    
    # Return existing if valid and correct model
    if tl.chatbot_instance and tl.chatbot_model_type == model:
        return tl.chatbot_instance
        
    # Close existing if model mismatch
    if tl.chatbot_instance:
        try:
            tl.chatbot_instance.close()
        except:
            pass
        tl.chatbot_instance = None
    
    try:
        # Use 600 second timeout (10 minutes) for Pro model responses as requested by user
        tl.chatbot_instance = Chatbot(cookie_path=COOKIE_PATH, model=model, timeout=600)
        tl.chatbot_model_type = model
        return tl.chatbot_instance
    except Exception as e:
        print(f"    ⚠️ Failed to create chatbot with {model.model_name}: {e}")
        return None


# Error types for intelligent handling
ERROR_AUTH = "auth"       # Cookie/authentication issues
ERROR_RATE = "rate"       # Rate limiting
ERROR_NETWORK = "network" # Network/timeout issues
ERROR_PARSE = "parse"     # Response parsing issues
ERROR_UNKNOWN = "unknown" # Unknown errors

# Track last error type for smart retry decisions
_last_error_type = None


def _detect_error_type(error_msg):
    """Detect the type of error from error message."""
    error_lower = str(error_msg).lower()
    
    # Authentication/cookie issues
    if any(x in error_lower for x in [
        "snlm0e", "authentication", "cookie", "permission", 
        "sign in", "login", "401", "403", "expired"
    ]):
        return ERROR_AUTH
    
    # Rate limiting
    if any(x in error_lower for x in [
        "rate limit", "429", "too many", "quota", "exceeded"
    ]):
        return ERROR_RATE
    
    # Network issues
    if any(x in error_lower for x in [
        "timeout", "timed out", "network", "connection", 
        "curl", "socket", "dns", "resolve"
    ]):
        return ERROR_NETWORK
    
    # Parse issues
    if any(x in error_lower for x in [
        "json", "parse", "decode", "extract", "format"
    ]):
        return ERROR_PARSE
    
    return ERROR_UNKNOWN

    return ERROR_UNKNOWN


def call_geminipro(prompt, model=Model.GEMINI_3_0_PRO):
    """
    Call Gemini Pro API with intelligent error detection.
    
    Args:
        prompt: User prompt/content
        model: Model to use (default: GEMINI_3_0_PRO)
        
    Returns:
        (content, success) tuple
    """
    global _last_error_type
    
    chatbot = None
    try:
        chatbot = _get_or_create_chatbot(model)
        if not chatbot:
            _last_error_type = ERROR_AUTH
            return None, False
            
        # Ensure fresh context for new request
        chatbot.reset()
        
        print(f"    🤖 Calling {model.model_name}...")
        response = chatbot.ask(prompt)
        
        if response and not response.get("error"):
            content = response.get("content", "")
            # Handle case where content is a list instead of string
            if isinstance(content, list):
                content = ' '.join(str(item) for item in content if item)
            elif not isinstance(content, str):
                content = str(content)
            
            _last_error_type = None
            return content, True
        else:
            error_msg = response.get("error") if response else "No response"
            content_preview = str(response.get("content", ""))[:100] if response else ""
            
            # Detect error type
            _last_error_type = _detect_error_type(f"{error_msg} {content_preview}")
            
            # Format user-friendly error message
            error_icons = {
                ERROR_AUTH: "🔐",
                ERROR_RATE: "⏱️",
                ERROR_NETWORK: "🌐",
                ERROR_PARSE: "📄",
                ERROR_UNKNOWN: "⚠️"
            }
            error_labels = {
                ERROR_AUTH: "Auth error (cookie may be expired)",
                ERROR_RATE: "Rate limit exceeded",
                ERROR_NETWORK: "Network error",
                ERROR_PARSE: "Response parse error",
                ERROR_UNKNOWN: "Unknown error"
            }
            
            icon = error_icons.get(_last_error_type, "⚠️")
            label = error_labels.get(_last_error_type, "Error")
            
            print(f"    {icon} {model.model_name}: {label}")
            if error_msg and error_msg != "True" and len(str(error_msg)) < 100:
                print(f"       Details: {error_msg}")
            
            return None, False
            
    except Exception as e:
        error_str = str(e)
        _last_error_type = _detect_error_type(error_str)
        
        # Format error
        if len(error_str) > 80:
            error_str = error_str[:80] + "..."
            
        print(f"    ⚠️ {model.model_name} exception: {error_str}")
        return None, False



def get_last_error_type():
    """Get the type of the last error for retry decisions."""
    return _last_error_type


# Track if we've already tried refreshing cookies this session
_cookie_refresh_attempted = False


def try_refresh_cookies():
    """Try to refresh Chrome cookies automatically."""
    global _cookie_refresh_attempted
    
    if _cookie_refresh_attempted:
        return False
    
    _cookie_refresh_attempted = True
    print("    🔄 Attempting to refresh cookies automatically via Selenium...")
    
    try:
        # Import here to avoid early dependency issues
        import selenium_cookie_extract
        
        # This will open Chrome, ask for login if needed, and save cookies
        success = selenium_cookie_extract.extract_cookies()
        
        if success:
            print("    ✅ Cookies refreshed successfully using Selenium!")
            return True
        else:
            print("    ⚠️ Selenium cookie refresh failed")
            return False
            
    except Exception as e:
        print(f"    ⚠️ Cookie refresh failed: {e}")
        return False


def call_with_fallback(prompt, use_pro=True, gemini_api_key=None):
    """
    Call Gemini Pro API with fallback to Flash (Cookie) and then Flash (API Key).
    
    Args:
        prompt: User prompt/content
        use_pro: Whether to try Pro first
        gemini_api_key: Optional API key for ultimate fallback
        
    Returns:
        (content, model_used) tuple
    """
    import time
    global _pro_count, _flash_count, _pro_failures, _last_error_type
    
    # 1. Try Gemini Pro (Cookie)
    if use_pro:
        content, success = call_geminipro(prompt, Model.GEMINI_3_0_PRO)
        if success and content:
            with _stats_lock:
                _pro_count += 1
            return content, "gemini-3.1-pro-preview"
        
        # Handle specific error cases for Pro failure (e.g. rate limit wait)
        with _stats_lock:
            _pro_failures += 1
            
        print("    🔄 Falling back from Pro...")

    # 2. Try Gemini Flash (Cookie)
    print("    🔄 Trying Gemini 3 Flash (via Cookies)...")
    content, success = call_geminipro(prompt, Model.GEMINI_3_0_FLASH)
    if success and content:
        with _stats_lock:
            _flash_count += 1
        return content, "gemini-flash-latest-cookie"

    # 3. Ultimate Fallback: Gemini Flash (API Key) if enabled/available
    # Check enviroment or argument
    api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if api_key:
        print("    🔄 Falling back to Gemini Flash (Official API)...")
        try:
            # Lazy import to avoid circular dependency issues at top level if untangled
            import gemini_api_client
            content, success = gemini_api_client.call_gemini(prompt, api_key=api_key, timeout=60)
            if success and content:
                 return content, "gemini-flash-latest-api"
            else:
                 print("    ❌ Gemini API Fallback failed too")
        except Exception as e:
            print(f"    ❌ Gemini API Fallback error: {e}")

    return None, None


def clean_json_response(content):
    """Clean up potential markdown code blocks from JSON response."""
    if not content:
        return content
    
    # Handle list content
    if isinstance(content, list):
        content = ' '.join(str(item) for item in content if item)
    elif not isinstance(content, str):
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
        print(f"❌ Cookie file not found: {COOKIE_PATH}")
        print("   Please create the file with your Gemini cookies.")
        print("   Instructions:")
        print("   1. Go to https://gemini.google.com/app")
        print("   2. Open Developer Tools (F12) → Application → Cookies")
        print("   3. Copy __Secure-1PSID and __Secure-1PSIDTS values")
        print("   4. Update Input/gemini_cookies.json")
        return False
    
    try:
        import json
        with open(COOKIE_PATH, 'r') as f:
            cookies = json.load(f)
        
        for cookie in cookies:
            if "YOUR___" in cookie.get("value", ""):
                print(f"❌ Cookie file contains placeholder values")
                print("   Please update Input/gemini_cookies.json with real values")
                return False
        
        return True
    except Exception as e:
        print(f"❌ Error reading cookie file: {e}")
        return False


# JSON repair prompt template
JSON_REPAIR_PROMPT = """FELADAT: Javítsd a hibás JSON kimenetet!

A következő JSON válasz hibás formátumú. Javítsd ki, hogy érvényes JSON array legyen.

KÖTELEZŐ SZABÁLYOK:
1. A kimenet CSAK és KIZÁRÓLAG egy valid JSON array legyen!
2. Ne adj hozzá semmilyen szöveget a JSON előtt vagy után!
3. Ne használj markdown kód blokkot!
4. Az ékezetes karakterek UTF-8 kódolásban legyenek!
5. Az idézőjeleket escape-eld: \\\"

KÖTELEZŐ MEZŐK minden objektumban:
- "section": tech/tudomany/belfold_kulfold/sport/uzlet/szorakozas/eletmod/bulvar
- "title": címke
- "content": tartalom
- "tags": ["tag1", "tag2", "tag3"]
- "image": "" (lehet üres)
- "sourceLink": eredeti URL
- "author": szerző

HIBÁS JSON:
{broken_json}

JAVÍTOTT JSON (csak a valid JSON array, semmi más):"""


def repair_json_response(broken_json, max_attempts=4):
    """
    Attempt to repair malformed JSON by alternating between Flash and Pro.
    
    Args:
        broken_json: The malformed JSON string
        max_attempts: Maximum repair attempts (default: 4)
        
    Returns:
        (repaired_content, model_used, success) tuple
    """
    import json
    
    if not broken_json:
        return None, None, False
    
    print("    🔧 Attempting to repair malformed JSON...")
    
    # Prepare the repair prompt
    repair_prompt = JSON_REPAIR_PROMPT.format(broken_json=broken_json[:8000])  # Limit size
    
    # Alternate between Flash and Pro
    models = [Model.GEMINI_3_0_FLASH, Model.GEMINI_3_0_PRO]
    
    for attempt in range(max_attempts):
        model = models[attempt % 2]  # Alternate: Flash, Pro, Flash, Pro
        model_name = "Flash" if model == Model.GEMINI_3_0_FLASH else "Pro"
        
        print(f"    🔧 Repair attempt {attempt + 1}/{max_attempts} using Gemini 3 {model_name}...")
        
        content, success = call_geminipro(repair_prompt, model)
        
        if success and content:
            # Clean and validate
            cleaned = clean_json_response(content)
            
            try:
                # Try to parse as JSON
                parsed = json.loads(cleaned)
                
                # Check if it's a list
                if isinstance(parsed, list):
                    print(f"    ✅ JSON repaired successfully by Gemini 3 {model_name}!")
                    return cleaned, f"gemini-3.0-{model_name.lower()}", True
                else:
                    print(f"    ⚠️ Repair produced non-array JSON, retrying...")
                    
            except json.JSONDecodeError as e:
                print(f"    ⚠️ Repair attempt {attempt + 1} still invalid: {str(e)[:50]}...")
        else:
            print(f"    ⚠️ Repair attempt {attempt + 1} failed (no response)")
    
    print("    ❌ JSON repair failed after all attempts")
    return None, None, False
