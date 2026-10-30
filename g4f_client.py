"""
LEÍRÁS:
GPT4Free (G4F) kliens wrapper.
Ingyenes hozzáférést biztosít különböző LLM-ekhez (pl. GPT-4, Claude 3) a 'g4f' könyvtáron keresztül.
Másodlagos/tartalék backendként szolgál.

BEMENET:
- Promptok
- Beállítások (Input/pipeline_settings.json)

KIMENET:
- (válasz_szöveg, sikeresség) tuple
"""


import time
import os
import json

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')

def _load_settings():
    """Load g4f settings from config file."""
    defaults = {
        "model": "gpt-4o-mini",
        "provider": "auto",
        "web_search": False,
        "timeout": 120
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                return {**defaults, **settings.get('g4f', {})}
    except:
        pass
    return defaults

# Stats tracking
_stats = {"calls": 0, "successes": 0, "failures": 0}

def reset_stats():
    """Reset call statistics."""
    global _stats
    _stats = {"calls": 0, "successes": 0, "failures": 0}

def print_stats():
    """Print call statistics."""
    print(f"  G4F Stats: {_stats['calls']} calls, {_stats['successes']} successes, {_stats['failures']} failures")

def check_cookies():
    """
    Check if g4f is available (no cookies needed for most providers).
    Returns True if g4f can be imported.
    """
    try:
        from g4f.client import Client
        return True
    except ImportError:
        print("⚠️ g4f not installed. Run: pip install g4f[all]")
        return False

def call_g4f(prompt, model=None, web_search=None, max_retries=3):
    """
    Call g4f with the given prompt.
    
    Args:
        prompt: The text prompt to send
        model: Model to use (None = use from settings)
        web_search: Enable web search (None = use from settings)
        max_retries: Number of retry attempts
        
    Returns:
        (content, success) tuple
    """
    global _stats
    _stats["calls"] += 1
    
    # Load settings
    settings = _load_settings()
    if model is None:
        model = settings['model']
    if web_search is None:
        web_search = settings['web_search']
    
    try:
        from g4f.client import Client
        from g4f.Provider import PollinationsAI
        
        # Use PollinationsAI provider (most reliable)
        client = Client(provider=PollinationsAI)
        
        # Map models to Pollinations-compatible names
        model_map = {
            'gpt-4o-mini': 'openai',
            'gpt-4o': 'openai',
            'gpt-4': 'openai',
            'claude-3-haiku': 'claude',
            'deepseek-v3': 'deepseek',
        }
        pollinations_model = model_map.get(model, 'openai')
        
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=pollinations_model,
                    messages=[{"role": "user", "content": prompt}],
                )
                
                content = response.choices[0].message.content
                
                if content:
                    _stats["successes"] += 1
                    return content, True
                else:
                    print(f"      [G4F] Empty response (attempt {attempt+1}/{max_retries})")
                    
            except Exception as e:
                error_str = str(e).lower()
                if "rate" in error_str or "limit" in error_str:
                    wait_time = 30 * (attempt + 1)
                    print(f"      [G4F] Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"      [G4F] Error (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(5)
                    
        _stats["failures"] += 1
        return None, False
        
    except (ImportError, SyntaxError, UnicodeDecodeError) as e:
        print(f"      [G4F] g4f library not usable: {e}")
        _stats["failures"] += 1
        return None, False
    except Exception as e:
        print(f"      [G4F] Unexpected error: {e}")
        _stats["failures"] += 1
        return None, False

def call_with_fallback(prompt, use_pro=False, api_key=None):
    """
    Wrapper to match the interface of other clients.
    
    Args:
        prompt: The text prompt
        use_pro: If True, use a more capable model (overrides settings)
        api_key: Ignored (g4f doesn't need API keys for most providers)
        
    Returns:
        (content, model_name) tuple
    """
    settings = _load_settings()
    
    # Model selection - use_pro overrides settings
    if use_pro:
        model = "gpt-4o"  # More capable
    else:
        model = settings['model']
    
    content, success = call_g4f(prompt, model=model, web_search=settings['web_search'])
    
    if success:
        return content, f"g4f-{model}"
    return None, None


# Async support
async def call_g4f_async(prompt, model="gpt-4o-mini"):
    """
    Async version of call_g4f.
    """
    global _stats
    _stats["calls"] += 1
    
    try:
        from g4f.client import AsyncClient
        client = AsyncClient()
        
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = response.choices[0].message.content
        
        if content:
            _stats["successes"] += 1
            return content, True
        
        _stats["failures"] += 1
        return None, False
        
    except Exception as e:
        print(f"      [G4F Async] Error: {e}")
        _stats["failures"] += 1
        return None, False


if __name__ == "__main__":
    # Quick test
    print("Testing G4F client...")
    if check_cookies():
        content, model = call_with_fallback("Say 'Hello from G4F!' in Hungarian.")
        if content:
            print(f"✅ Success! Model: {model}")
            print(f"Response: {content[:200]}...")
        else:
            print("❌ Failed to get response")
    else:
        print("❌ G4F not available")
