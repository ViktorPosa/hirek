"""
Free Gemini API Key Pool Manager

Manages a pool of free Gemini API keys with:
- 20 uses/day limit per key
- Automatic rotation when limit reached
- Daily reset of counters
- Key 1 is RESERVED for JSON correction, not available for summarization
"""

import json
import os
import threading
from datetime import datetime, date
from google import genai

# Configuration
KEYS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_api_keys.txt')
USAGE_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_key_usage.json')
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')
# Free tier model
MODEL_NAME = "gemini-3.1-flash-lite"
MODEL_FALLBACKS = ["gemini-flash-latest", "gemini-2.5-pro"]


class FreeGeminiKeyPool:
    """Thread-safe manager for free Gemini API keys with usage limits."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern for shared key pool."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._keys = []
        self._usage = {}
        self._usage_lock = threading.Lock()
        self._current_date = None
        self._initialized = True
        
        self._load_keys()
        self._load_usage()
    
    def _load_keys(self):
        """Load API keys from file, skip key 1 (reserved for JSON correction)."""
        try:
            with open(KEYS_FILE, 'r') as f:
                all_keys = [line.strip() for line in f if line.strip()]
            
            # Skip first key (reserved for JSON correction)
            self._keys = all_keys[1:] if len(all_keys) > 1 else []
            
            print(f"   [FreeGeminiPool] Loaded {len(self._keys)} keys (key 1 reserved)")
            
        except FileNotFoundError:
            print(f"   ⚠️ [FreeGeminiPool] Keys file not found: {KEYS_FILE}")
            self._keys = []

    def get_daily_limit(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                    return settings.get('multi_model', {}).get('free_gemini_daily_limit', 20)
        except Exception:
            pass
        return 20
    
    def _load_usage(self):
        """Load usage counters from file."""
        try:
            with open(USAGE_FILE, 'r') as f:
                data = json.load(f)
                self._current_date = data.get('date')
                self._usage = data.get('usage', {})
                
                # Reset if it's a new day
                today = date.today().isoformat()
                if self._current_date != today:
                    print(f"   [FreeGeminiPool] New day, resetting counters")
                    self._usage = {}
                    self._current_date = today
                    self._save_usage()
                    
        except (FileNotFoundError, json.JSONDecodeError):
            self._usage = {}
            self._current_date = date.today().isoformat()
    
    def _save_usage(self):
        """Save usage counters to file."""
        try:
            with open(USAGE_FILE, 'w') as f:
                json.dump({
                    'date': self._current_date,
                    'usage': self._usage
                }, f, indent=2)
        except Exception as e:
            print(f"   ⚠️ [FreeGeminiPool] Failed to save usage: {e}")
    
    def get_available_key(self):
        """
        Get an available API key with remaining quota.
        Returns (key, remaining_uses) or (None, 0) if all exhausted.
        """
        with self._usage_lock:
            # Check if it's a new day
            today = date.today().isoformat()
            if self._current_date != today:
                print(f"   [FreeGeminiPool] Daily reset")
                self._usage = {}
                self._current_date = today
                self._save_usage()
            
            # Find a key with remaining quota
            import random
            available = []
            limit = self.get_daily_limit()
            for key in self._keys:
                used = self._usage.get(key, 0)
                if used < limit:
                    remaining = limit - used
                    available.append((key, remaining))
            
            if available:
                return random.choice(available)
            
            # All keys exhausted
            return None, 0
    
    def record_usage(self, key):
        """Record one usage of a key."""
        with self._usage_lock:
            self._usage[key] = self._usage.get(key, 0) + 1
            self._save_usage()
            
            used = self._usage[key]
            limit = self.get_daily_limit()
            remaining = max(0, limit - used)
            print(f"   [FreeGeminiPool] Key ...{key[-6:]}: {used}/{limit} used, {remaining} remaining")
    
    def get_total_remaining(self):
        """Get total remaining uses across all keys."""
        with self._usage_lock:
            total = 0
            limit = self.get_daily_limit()
            for key in self._keys:
                used = self._usage.get(key, 0)
                total += max(0, limit - used)
            return total
    
    def get_status(self):
        """Get human-readable status of all keys."""
        with self._usage_lock:
            status = []
            limit = self.get_daily_limit()
            for i, key in enumerate(self._keys):
                used = self._usage.get(key, 0)
                remaining = max(0, limit - used)
                status.append(f"Key {i+2}: {used}/{limit} used ({remaining} left)")
            return status


def generate_with_free_api(prompt, system_prompt=None):
    """
    Generate response using free Gemini API pool.
    Automatically rotates keys when limits reached.
    
    Returns (response_text, success) tuple.
    """
    import time
    pool = FreeGeminiKeyPool()
    
    # Build prompt
    full_prompt = prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{prompt}"
        
    for attempt in range(4):
        key, remaining = pool.get_available_key()
        if not key:
            print("   ⚠️ [FreeGeminiAPI] All keys exhausted for today!")
            return None, False
        
        try:
            # Create client with the available key; set API-level timeout to prevent
            # workers from hanging indefinitely on slow responses.
            from google.genai import types as _genai_types
            client = genai.Client(
                api_key=key,
                http_options=_genai_types.HttpOptions(timeout=90000),
            )
            
            # Try models in order (primary + fallbacks)
            models_to_try = [MODEL_NAME] + [m for m in MODEL_FALLBACKS if m != MODEL_NAME]
            last_error = None
            
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=f"models/{model_name}",
                        contents=full_prompt,
                        config={
                            "temperature": 0.7,
                            "max_output_tokens": 8192,
                        },
                    )
                    
                    # Record usage on success
                    pool.record_usage(key)
                    
                    if response and response.text:
                        print(f"   [FreeGeminiAPI] ✅ Success with model: {model_name}")
                        return response.text, True
                    else:
                        print(f"   [FreeGeminiAPI] Empty response from {model_name}")
                        continue
                        
                except Exception as model_err:
                    err_str = str(model_err)
                    # If "bad request" or "not found" → model doesn't exist, try next
                    if "400" in err_str or "404" in err_str or "not found" in err_str.lower() or "invalid" in err_str.lower():
                        print(f"   [FreeGeminiAPI] Model '{model_name}' not available ({err_str[:60]}), trying next...")
                        last_error = model_err
                        continue
                    else:
                        # Other error (rate limit, etc.) - raise to outer handler
                        raise model_err
            
            # All models failed for this key
            if last_error:
                raise last_error
            return None, False
                
        except Exception as e:
            error_msg = str(e)
            
            # Check if it's a rate limit or server overload
            err_lower = error_msg.lower()
            is_overload = (
                "503" in error_msg
                or "429" in error_msg
                or "quota" in err_lower
                or "rate limit" in err_lower
                or "resource_exhausted" in err_lower
                or "too many requests" in err_lower
            )
            
            if is_overload:
                wait_time = 5 * (2 ** attempt)
                print(f"   ⚠️ [FreeGeminiAPI] Overload/Rate limit ({error_msg[:60]}). Waiting {wait_time}s... (Attempt {attempt+1}/4)")
                time.sleep(wait_time)
                continue
            
            # Bad request = likely all models invalid
            if "400" in error_msg:
                print(f"   ❌ [FreeGeminiAPI] Bad Request - all model names may be invalid: {error_msg[:100]}")
                return None, False
                
            print(f"   ❌ [FreeGeminiAPI] Error: {error_msg[:100]}")
            return None, False

    print("   ❌ [FreeGeminiAPI] Failed after max retries due to rate limiting/overload.")
    return None, False


def check_available():
    """Check if any free API keys are available."""
    pool = FreeGeminiKeyPool()
    remaining = pool.get_total_remaining()
    
    if remaining > 0:
        print(f"   [FreeGeminiAPI] {remaining} total uses available")
        return True
    else:
        print("   ⚠️ [FreeGeminiAPI] No uses remaining for today")
        return False


def get_reserved_key():
    """Get key 1 (reserved for JSON correction). Does not count against limits."""
    try:
        with open(KEYS_FILE, 'r') as f:
            all_keys = [line.strip() for line in f if line.strip()]
        
        if all_keys:
            return all_keys[0]
        return None
    except:
        return None
