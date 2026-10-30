"""
LEÍRÁS:
Kliens wrapper az LM Studio lokális (127.0.0.1:1234) és hálózati (192.168.0.10:1234) szerver végpontjaihoz.
OpenAI API kompatibilis kommunikációt indít a kiválasztott végpontra.

Válaszvalidáció helyi modellekhez (Gemma 4, Qwen 3.5):
- finish_reason ellenőrzés (truncation detection)
- JSON struktúra validáció
- Automatikus retry csonkolt válasznál nagyobb max_tokens-szel

BEMENET:
- Prompt
- Választható lokális vagy távoli végpont

KIMENET:
- (válasz_szöveg, modell_név) tuple
"""

import requests
import json
import time
import re

# OpenAI-compatible endpoints (standard LM Studio API)
LOCAL_ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
REMOTE_ENDPOINT = "http://192.168.0.10:1234/v1/chat/completions"

# Model health endpoints
LOCAL_MODELS_URL = "http://127.0.0.1:1234/v1/models"
REMOTE_MODELS_URL = "http://192.168.0.10:1234/v1/models"

LOCAL_MODEL = "gemma-4-e4b-uncensored-hauhaucs-aggressive"
REMOTE_MODEL = "gemma-4-31b-it-claude-opus-distill"

# Default max tokens (helyi modelleknek fontos, hogy ne csonkoljanak)
DEFAULT_MAX_TOKENS = 8192
RETRY_MAX_TOKENS = 16384  # Nagyobb limit retry esetén

# --- Pipeline-szintű kizárás ---
# Ha egy endpoint 3x egymás után hibázik, az egész pipeline futás alatt ki lesz zárva.
MAX_CONSECUTIVE_FAILURES = 3
_failure_counts = {"local": 0, "remote": 0}
_disabled_endpoints = {"local": False, "remote": False}

def _check_server(models_url, timeout=5):
    """Check if LM Studio server is reachable and has a model loaded."""
    try:
        resp = requests.get(models_url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", [])
            if models:
                return True
            else:
                print(f"      [LM Studio] Server reachable but no model loaded at {models_url}")
                return False
        return False
    except Exception:
        return False

def check_local():
    """Check if local LM Studio is available."""
    return _check_server(LOCAL_MODELS_URL)

def check_remote():
    """Check if remote LM Studio is available."""
    return _check_server(REMOTE_MODELS_URL)

def _validate_lm_response(content, finish_reason, usage_info, attempt_num):
    """
    Validate LM Studio response quality.
    
    Returns:
        (is_valid, issue_description)
    """
    issues = []
    
    # 1. Check finish_reason for truncation
    if finish_reason == "length":
        completion_tokens = usage_info.get("completion_tokens", "?")
        issues.append(f"TRUNCATED (finish_reason='length', tokens={completion_tokens})")
    
    # 2. Empty response check
    if not content or not content.strip():
        return False, "Empty response"
    
    # 3. Check for obvious signs of truncation in content
    stripped = content.strip()
    
    # Check if JSON response was cut mid-way
    if '{' in stripped or '[' in stripped:
        open_braces = stripped.count('{') - stripped.count('}')
        open_brackets = stripped.count('[') - stripped.count(']')
        
        if open_braces > 0 or open_brackets > 0:
            issues.append(f"Unclosed JSON (braces: {open_braces}, brackets: {open_brackets})")
        
        # Check for cut-off mid-string (ends with incomplete escape or unfinished quote)
        if stripped.endswith('\\') or (stripped.count('"') % 2 != 0):
            issues.append("JSON string appears cut off")
    
    # 4. Check for common local model garbage patterns
    # Local models sometimes repeat themselves or output garbage
    if len(stripped) > 500:
        # Check for excessive repetition (same 50-char block appears 3+ times)
        for i in range(0, min(len(stripped) - 50, 500), 50):
            block = stripped[i:i+50]
            if block and stripped.count(block) >= 3:
                issues.append(f"Repetitive output detected")
                break
    
    # 5. Check for model refusal / off-topic
    refusal_patterns = [
        "I cannot", "I can't", "As an AI", "I'm sorry, but I",
        "I apologize", "not able to"
    ]
    first_100 = stripped[:100].lower()
    for pattern in refusal_patterns:
        if pattern.lower() in first_100:
            issues.append(f"Possible model refusal: '{pattern}'")
            break
    
    if issues:
        issue_str = "; ".join(issues)
        # Truncation is critical - return invalid
        if "TRUNCATED" in issue_str or "Unclosed JSON" in issue_str:
            return False, issue_str
        # Other issues are warnings only
        print(f"      [LM Studio] ⚠️ Response warnings: {issue_str}")
        return True, issue_str
    
    return True, None


def _call_lmstudio(prompt, endpoint_url, model_name, max_retries=3, max_tokens=DEFAULT_MAX_TOKENS):
    """
    Call LM Studio via OpenAI-compatible /v1/chat/completions endpoint.
    Includes finish_reason checking and truncation retry.
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "Te egy profi hírösszefoglaló és elemző asszisztens vagy."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": False
    }
    
    current_max_tokens = max_tokens
    
    for attempt in range(max_retries):
        try:
            print(f"      [LM Studio] Sending request to {endpoint_url} (Attempt {attempt+1}/{max_retries}, max_tokens={current_max_tokens})...")
            response = requests.post(endpoint_url, headers=headers, json=payload, timeout=400)
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse OpenAI-compatible response format
                choices = data.get("choices", [])
                if not choices:
                    print("      [LM Studio] No choices in response")
                    time.sleep(2)
                    continue
                
                choice = choices[0]
                content = ""
                finish_reason = choice.get("finish_reason", "unknown")
                
                # Extract content from message
                message = choice.get("message", {})
                if isinstance(message, dict):
                    content = message.get("content", "").strip()
                elif isinstance(message, str):
                    content = message.strip()
                
                # Get usage info
                usage = data.get("usage", {})
                model_used = data.get("model", model_name)
                
                # Log response stats
                prompt_tokens = usage.get("prompt_tokens", "?")
                completion_tokens = usage.get("completion_tokens", "?")
                print(f"      [LM Studio] Response: {len(content)} chars, finish_reason={finish_reason}, tokens: {prompt_tokens}/{completion_tokens}")
                
                # Validate response
                is_valid, issue = _validate_lm_response(content, finish_reason, usage, attempt)
                
                if is_valid and content:
                    if issue:
                        print(f"      [LM Studio] ⚠️ Valid but with warnings: {issue}")
                    return content, model_used
                
                elif not is_valid and "TRUNCATED" in (issue or ""):
                    # Truncation detected - retry with larger max_tokens
                    if current_max_tokens < RETRY_MAX_TOKENS:
                        current_max_tokens = RETRY_MAX_TOKENS
                        payload["max_tokens"] = current_max_tokens
                        print(f"      [LM Studio] 🔄 Response truncated! Retrying with max_tokens={current_max_tokens}...")
                        continue
                    else:
                        print(f"      [LM Studio] ❌ Response truncated even with max_tokens={current_max_tokens}. Returning as-is.")
                        # Try to salvage: return content anyway if it's substantial
                        if content and len(content) > 100:
                            return content, model_used
                
                elif not is_valid and "Unclosed JSON" in (issue or ""):
                    # JSON cut off - try to fix
                    print(f"      [LM Studio] 🔧 Attempting JSON repair for unclosed structure...")
                    repaired = _attempt_json_repair(content)
                    if repaired:
                        print(f"      [LM Studio] ✅ JSON repair successful")
                        return repaired, model_used
                    
                    # If repair failed, retry with more tokens
                    if current_max_tokens < RETRY_MAX_TOKENS:
                        current_max_tokens = RETRY_MAX_TOKENS
                        payload["max_tokens"] = current_max_tokens
                        print(f"      [LM Studio] 🔄 Retrying with max_tokens={current_max_tokens}...")
                        continue
                    else:
                        # Last resort: return content for upper-layer repair
                        if content and len(content) > 100:
                            return content, model_used
                
                else:
                    print(f"      [LM Studio] ❌ Invalid response: {issue}")
                    
            else:
                print(f"      [LM Studio] Server returned status code {response.status_code}: {response.text[:200]}")
                
        except requests.exceptions.ConnectionError:
            print(f"      [LM Studio] Elutasítva. Biztosan be van kapcsolva a szerver a {endpoint_url} címen?")
            break  # Nincs értelme próbálkozni, ha a gép sem elérhető
        except requests.exceptions.Timeout:
            print(f"      [LM Studio] Időtúllépés történt a hívásnál (400s timeout).")
        except Exception as e:
            print(f"      [LM Studio] Hiba az LM Studio hívás során: {e}")
            
        time.sleep(2)
        
    return None, None


def _attempt_json_repair(content):
    """
    Try to repair truncated JSON from local models.
    Closes unclosed brackets/braces and tries to parse.
    """
    if not content:
        return None
    
    stripped = content.strip()
    
    # Find the start of JSON
    json_start = -1
    for i, c in enumerate(stripped):
        if c in '[{':
            json_start = i
            break
    
    if json_start < 0:
        return None
    
    json_str = stripped[json_start:]
    
    # Try to close unclosed structures
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    
    # Remove trailing incomplete elements (e.g., partial key-value pairs)
    # Find the last complete element
    repaired = json_str.rstrip()
    
    # If ends with comma, remove it
    if repaired.endswith(','):
        repaired = repaired[:-1]
    
    # If ends mid-string (unmatched quote), try to close it
    if repaired.count('"') % 2 != 0:
        repaired += '"'
    
    # Close open structures
    repaired += '}' * max(0, open_braces)
    repaired += ']' * max(0, open_brackets)
    
    # Verify it parses
    try:
        parsed = json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        # More aggressive repair: find the last valid comma-separated item
        try:
            # Try truncating at the last complete object
            last_close_brace = repaired.rfind('}')
            if last_close_brace > 0:
                candidate = repaired[:last_close_brace + 1]
                # Re-close arrays
                open_brackets_2 = candidate.count('[') - candidate.count(']')
                candidate += ']' * max(0, open_brackets_2)
                parsed = json.loads(candidate)
                return candidate
        except:
            pass
    
    return None


def _record_success(endpoint_key):
    """Record successful call — reset failure counter."""
    _failure_counts[endpoint_key] = 0

def _record_failure(endpoint_key):
    """Record failed call. After MAX_CONSECUTIVE_FAILURES, disable for the rest of the pipeline run."""
    _failure_counts[endpoint_key] = _failure_counts.get(endpoint_key, 0) + 1
    count = _failure_counts[endpoint_key]
    if count >= MAX_CONSECUTIVE_FAILURES:
        _disabled_endpoints[endpoint_key] = True
        print(f"      [LM Studio] 🚫 '{endpoint_key}' KIZÁRVA a pipeline futásból ({count} egymás utáni hiba)")
    else:
        print(f"      [LM Studio] ⚠️ '{endpoint_key}' hiba #{count}/{MAX_CONSECUTIVE_FAILURES}")

def is_disabled(endpoint_key):
    """Check if an endpoint is disabled for this pipeline run."""
    return _disabled_endpoints.get(endpoint_key, False)

def reset_pipeline_state():
    """Reset failure counters — hívandó minden pipeline futás elején."""
    global _failure_counts, _disabled_endpoints
    _failure_counts = {"local": 0, "remote": 0}
    _disabled_endpoints = {"local": False, "remote": False}

def call_lmstudio_local(prompt, use_pro=False):
    if is_disabled("local"):
        print(f"      [LM Studio] 🚫 Local endpoint kizárva (3+ hiba) — kihagyás")
        return None, None
    result, model = _call_lmstudio(prompt, LOCAL_ENDPOINT, LOCAL_MODEL)
    if result:
        _record_success("local")
    else:
        _record_failure("local")
    return result, model

def call_lmstudio_remote(prompt, use_pro=False):
    if is_disabled("remote"):
        print(f"      [LM Studio] 🚫 Remote endpoint kizárva (3+ hiba) — kihagyás")
        return None, None
    result, model = _call_lmstudio(prompt, REMOTE_ENDPOINT, REMOTE_MODEL)
    if result:
        _record_success("remote")
    else:
        _record_failure("remote")
    return result, model


def call_with_fallback(prompt, use_pro=False, endpoint="local"):
    """
    Kompatibilitási burkoló. Endpoint választható: 'local' vagy 'remote'.
    """
    if endpoint == "remote":
        return call_lmstudio_remote(prompt, use_pro)
    return call_lmstudio_local(prompt, use_pro)

if __name__ == "__main__":
    print("Teszt: LM Studio hívások indítása...")
    
    print("\n--- Helyi szerver (127.0.0.1) ---")
    if check_local():
        print("✅ Szerver elérhető!")
        local_res, local_model = call_lmstudio_local("Szia! Csak egy rövid tesztüzenet.")
        if local_res:
             print(f"✅ Siker! Modell: {local_model}")
             print(f"Válasz:\n{local_res}")
        else:
             print("❌ A helyi szerver válasza hibás volt.")
    else:
        print("❌ A helyi szerver nem elérhető.")
         
    print("\n--- Távoli szerver (192.168.0.10) ---")
    if check_remote():
        print("✅ Szerver elérhető!")
        remote_res, remote_model = call_lmstudio_remote("Szia! Csak egy rövid tesztüzenet.")
        if remote_res:
             print(f"✅ Siker! Modell: {remote_model}")
             print(f"Válasz:\n{remote_res}")
        else:
             print("❌ A távoli szerver válasza hibás volt.")
    else:
        print("❌ A távoli szerver nem elérhető.")