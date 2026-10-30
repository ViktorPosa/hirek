"""
LEÍRÁS:
Hivatalos Google Gemini API kliens.
Egyszerűsített interfészt biztosít a Gemini API hívásához API kulcs használatával.
Kezeli a rátakorlátokat (429) és az újrapróbálkozásokat.

BEMENET:
- Promptok
- API kulcsok (Input/gemini_api_keys.txt vagy environment variable)

KIMENET:
- (válasz_szöveg, használt_modell) tuple
"""


import requests
import json
import sys
import os
import threading

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# --- CONFIGURATION ---
KEYS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_api_keys.txt')
INPUT_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'input.txt')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"

def _load_api_keys():
    """Load all available API keys from the keys file."""
    keys = []
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                keys = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except Exception as e:
            print(f"Warning: Could not read {KEYS_FILE}: {e}")
            
    # Also check input.txt for legacy support
    if os.path.exists(INPUT_FILE):
        try:
            with open(INPUT_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("API_KEY="):
                        legacy_key = line.replace("API_KEY=", "").strip()
                        if legacy_key and legacy_key not in keys:
                            keys.append(legacy_key)
                        break
        except Exception:
            pass
            
    # Fallback to hardcoded if no file keys found (legacy behavior preserved via env/default not strictly needed if file exists)
    if not keys:
         env_key = os.environ.get("GEMINI_API_KEY")
         if env_key:
             keys.append(env_key)
             
    return keys

def _load_api_key(key_index=None):
    """
    Load an API Key.
    If key_index is provided (0-based), returns that specific key.
    Otherwise returns a random key from the available pool.
    """
    keys = _load_api_keys()
    
    if not keys:
        # Fallback to hardcoded default for safety, though user asked to replace it
        return os.environ.get("GEMINI_API_KEY", "")
        
    if key_index is not None:
        if 0 <= key_index < len(keys):
            return keys[key_index]
        else:
            print(f"Warning: Requested API Key index {key_index} out of range (0-{len(keys)-1}). Using random key.")
    
    # Simple rotation/random selection
    import random
    return random.choice(keys)

def get_key_count():
    """Return number of available keys."""
    return len(_load_api_keys())

# Thread-safe counters for statistics
_stats_lock = threading.Lock()
_gemini_count = 0
_gemini_failures = 0


def reset_stats():
    """Reset the processing statistics."""
    global _gemini_count, _gemini_failures
    with _stats_lock:
        _gemini_count = 0
        _gemini_failures = 0


def get_stats():
    """Get current processing statistics."""
    with _stats_lock:
        return {
            'gemini_count': _gemini_count,
            'gemini_failures': _gemini_failures
        }


def print_stats():
    """Print processing statistics summary."""
    stats = get_stats()
    if stats['gemini_count'] > 0:
        print(f"\n📊 Processing Summary:")
        print(f"   ✅ Gemini: {stats['gemini_count']} batches")
        if stats['gemini_failures'] > 0:
            print(f"   ⚠️ Gemini failures: {stats['gemini_failures']}")


def call_gemini(prompt, api_key=None, system_prompt=None, timeout=600, max_retries=3):
    """
    Call Gemini API with rate limit handling.
    
    Args:
        prompt: User prompt/content
        api_key: Optional API key override
        system_prompt: Optional system instruction
        timeout: Request timeout in seconds
        max_retries: Maximum retries for rate limit errors
        
    Returns:
        (content, success) tuple
    """
    import time
    
    # Use provided key or fallback to dynamic loader
    key = api_key if api_key else _load_api_key()
    url = f"{GEMINI_API_URL}?key={key}"
    
    contents = []
    if system_prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[SYSTEM INSTRUCTION]\n{system_prompt}\n\n[USER MESSAGE]\n{prompt}"}]
        })
    else:
        contents.append({
            "role": "user", 
            "parts": [{"text": prompt}]
        })
    
    data = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192
        }
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, timeout=timeout)
            
            if response.status_code == 200:
                result = response.json()
                if 'candidates' in result and len(result['candidates']) > 0:
                    content = result['candidates'][0]['content']['parts'][0]['text']
                    return content, True
            
            if response.status_code == 429:
                # Rate limited - return failure to let orchestrator handle failover to enabled backends
                # Previously had hardcoded fallback to Gemini Chat API which bypassed menu settings
                wait_time = 5 * (2 ** attempt)  # 5s, 10s, 20s
                print(f"    ⏱️ Rate limited (429). Waiting {wait_time}s before retry ({attempt+1}/{max_retries})...")
                time.sleep(wait_time)
                continue
            
            print(f"    ⚠️ Gemini API error: {response.status_code}")
            return None, False
            
        except requests.exceptions.Timeout:
            print(f"    ⚠️ Gemini timeout after {timeout}s")
            return None, False
        except Exception as e:
            print(f"    ⚠️ Gemini error: {e}")
            return None, False
    
    print(f"    ⚠️ Gemini rate limit persists after {max_retries} retries")
    return None, False


def call_with_fallback(prompt, system_prompt=None, gemini_api_key=None, timeout=120, **kwargs):
    """
    Call Gemini API (kept for backward compatibility).
    
    Args:
        prompt: User prompt/content
        system_prompt: Optional system instruction
        gemini_api_key: Optional API key override
        timeout: Request timeout in seconds
        **kwargs: Ignored (for backward compatibility)
        
    Returns:
        (content, model_used) tuple where model_used is "gemini"
    """
    global _gemini_count, _gemini_failures
    
    content, success = call_gemini(prompt, api_key=gemini_api_key, system_prompt=system_prompt, timeout=timeout)
    if success and content:
        with _stats_lock:
            _gemini_count += 1
        return content, "gemini-3.1-flash-lite"
    else:
        with _stats_lock:
            _gemini_failures += 1
    
    return None, None


def clean_json_response(content):
    """Clean up potential markdown code blocks from JSON response."""
    if not content:
        return content
    
    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    return content.strip()


# JSON repair prompt template
JSON_REPAIR_PROMPT = """FELADAT: Javítsd a hibás JSON kimenetet!

A bemeneti szöveg egy érvénytelen vagy hibásan formázott JSON tömb. A feladatod, hogy kijavítsd és ÉRVÉNYES JSON listát adj vissza.

KÖTELEZŐ SZABÁLYOK:
1. A kimenet CSAK és KIZÁRÓLAG a javított JSON tömb legyen!
2. SOHA ne használj markdown blokkot (ne írd ki: ```json ... ```)!
3. Kerüld a bevezető szöveget ("Itt a javított JSON:")!
4. Az idézőjeleket escape-eld a stringeken belül (pl: \\"idézet\\")!
5. Az ékezetes karakterek UTF-8 kódolásban legyenek!

ELVÁRT STRUKTÚRA (minden objektumban):
{{
  "section": "tech" vagy "tudomany" vagy "belfold_kulfold" vagy "sport" vagy "uzlet" vagy "szorakozas" vagy "eletmod" vagy "bulvar",
  "title": "Tényközlő, informatív cím",
  "content": "A hír lényegi összefoglalója (min 3 mondat)",
  "tags": ["Tag1", "Tag2", "Tag3", ...],
  "importance": 1-5 skálán (1=érdektelen, 3=átlagos, 5=breaking news),
  "image": "",
  "sourceLink": "https://eredeti.url",
  "author": "Szerző neve"
}}

HIBÁS BEMENET:
{broken_json}

JAVÍTOTT JSON:"""

# Debug file for failed repairs
DEBUG_REPAIR_FILE = "debug_json_repair.txt"

def _log_failed_repair(broken_json, reason="Unknown"):
    """Log broken JSON that could not be repaired to a debug file."""
    import datetime
    try:
        with open(DEBUG_REPAIR_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"FAILED REPAIR - {datetime.datetime.now().isoformat()}\n")
            f.write(f"REASON: {reason}\n")
            f.write(f"{'='*80}\n")
            f.write(broken_json[:20000] if broken_json else "(empty)")
            f.write(f"\n{'='*80}\n\n")
        print(f"    📝 Logged failed repair to {DEBUG_REPAIR_FILE}")
    except Exception as e:
        print(f"    ⚠️ Could not log failed repair: {e}")


def repair_json_response(broken_json):
    """
    Attempt to repair malformed JSON using Gemini API with key rotation.
    Retries with different API keys if available, then falls back to Selenium.
    
    Args:
        broken_json: The malformed JSON string
        
    Returns:
        (repaired_content, model_used, success) tuple
    """
    if not broken_json:
        return None, None, False
    
    keys = _load_api_keys()
    
    print(f"    🔧 Attempting to repair malformed JSON (using {len(keys)} API keys + Selenium fallback)...")
    
    # Prepare the repair prompt
    repair_prompt = JSON_REPAIR_PROMPT.format(broken_json=broken_json[:15000])  # Limit size
    
    # 1. Try keys in sequence (Limit to 1 attempt as requested)
    if keys:
        # User requested: "don't try to repair 7 times, only 1, then rather drop it."
        keys_to_try = keys[:1] 
        for i, key in enumerate(keys_to_try):
            print(f"    🔧 Repair attempt {i+1}/{len(keys_to_try)} using Key ending in ...{key[-4:]}...")
            
            try:
                content, success = call_gemini(repair_prompt, api_key=key, timeout=60, max_retries=1)
                
                if success and content:
                    # Clean and validate
                    cleaned = clean_json_response(content)
                    
                    try:
                        # Try to parse as JSON to verify
                        parsed = json.loads(cleaned)
                        
                        if isinstance(parsed, list):
                            print(f"    ✅ JSON repaired successfully with Key {i+1}!")
                            return cleaned, f"gemini-flash-repair-key{i+1}", True
                        else:
                            print(f"    ⚠️ Repair produced non-array JSON, trying next key...")
                    except json.JSONDecodeError:
                        print(f"    ⚠️ Repair produced invalid JSON, trying next key...")
                else:
                    print(f"    ⚠️ Repair failed with Key {i+1}")
                    
            except Exception as e:
                print(f"    ⚠️ Repair exception with Key {i+1}: {e}")
    
    # 2. Fallback to Gemini-Selenium if enabled
    print("    🔧 API keys failed or exhausted. Trying Gemini Selenium fallback...")
    try:
        # Lazy import only when needed
        import gemini_selenium_client
        
        content, model = gemini_selenium_client.call_with_fallback(repair_prompt, use_pro=False)
        
        if content:
             cleaned = clean_json_response(content)
             try:
                 parsed = json.loads(cleaned)
                 if isinstance(parsed, list):
                     print(f"    ✅ JSON repaired successfully with Gemini Selenium!")
                     return cleaned, "gemini-selenium-repair", True
             except:
                 pass
        
        print("    ⚠️ Gemini Selenium repair failed")
        
    except ImportError:
        print("    ⚠️ Gemini Selenium client not available")
    except Exception as e:
        print(f"    ⚠️ Gemini Selenium repair error: {e}")
            
    print("    ❌ JSON repair failed after trying all options")
    
    # Log the failed repair for debugging
    _log_failed_repair(broken_json, "All repair methods failed")
    
    return None, None, False

