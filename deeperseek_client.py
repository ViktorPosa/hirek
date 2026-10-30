"""
LEÍRÁS:
DeeperSeek kliens wrapper - OPTIMIZED VERSION.
Egységes interfészt biztosít a DeeperSeek könyvtárhoz AI összegzéshez és DeepThink (R1) modell használatához.
Böngésző automatizációt használ a háttérben.

OPTIMIZATIONS:
- Persistent browser session (no restart between calls)
- Shared event loop
- Fast session check (skip Cloudflare if already logged in)

BEMENET:
- Promptok
- Beállítások (Input/pipeline_settings.json)
- Böngésző profil és hitelesítés

KIMENET:
- (válasz_szöveg, sikeresség) tuple
"""


import os
import time
import json
import asyncio
import threading

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')

def _load_settings():
    """Load deeperseek settings from config file."""
    defaults = {
        "headless": True,
        "deep_think": False
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                return {**defaults, **settings.get('deeperseek', {})}
    except:
        pass
    return defaults

# Stats tracking
_stats = {"calls": 0, "successes": 0, "failures": 0}

# Cache path for auth state
AUTH_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'Input', 'deeperseek_auth.json')

# ============= PERSISTENT CLIENT SINGLETON =============
_persistent_client = None
_persistent_loop = None
_client_lock = threading.Lock()
_initialized = False

def reset_stats():
    """Reset call statistics."""
    global _stats
    _stats = {"calls": 0, "successes": 0, "failures": 0}

def print_stats():
    """Print call statistics."""
    print(f"  DeeperSeek Stats: {_stats['calls']} calls, {_stats['successes']} successes, {_stats['failures']} failures")

def check_cookies():
    """
    Check if DeeperSeek is available and authenticated.
    Returns True if we can import and have cached auth.
    """
    try:
        from DeeperSeek import DeepSeek
        
        # Check for cached auth
        if os.path.exists(AUTH_CACHE_PATH):
            print("  [DeeperSeek] Auth cache found.")
            return True
        
        # No cache - will need browser auth on first call
        print("  [DeeperSeek] No auth cache. Browser login will be required on first use.")
        return True
        
    except ImportError as e:
        print(f"⚠️ DeeperSeek import failed: {e}")
        print("   Debugging: Ensure 'zendriver' is installed (pip install zendriver)")
        return False

def _create_smart_deepseek_class():
    """Factory to create SmartDeepSeek class with all imports."""
    from DeeperSeek import DeepSeek
    from bs4 import BeautifulSoup
    from inscriptis import get_text
    from DeeperSeek.internal.objects import Response
    from DeeperSeek.internal.exceptions import ServerDown, MissingInitialization
    
    class SmartDeepSeek(DeepSeek):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Monkey patch selectors
            self.selectors.interactions.textbox = "textarea"
            self.selectors.interactions.send_button = "div[role='button']._7436101"
            self.selectors.interactions.deepthink_content = ".ds-think-content"
            self._session_valid = False

        async def initialize(self, chat_id: str = None) -> None:
            """
            Custom initialize that skips base _login (which crashes on missing creds).
            Uses fast_login for persistent session handling.
            """
            import zendriver as zd
            
            print("      [DeeperSeek] Starting browser...")
            # Add --no-sandbox to chrome args to fix connection issues
            browser_args = self._chrome_args if self._chrome_args else []
            if "--no-sandbox" not in browser_args:
                browser_args.append("--no-sandbox")
            if "--disable-dev-shm-usage" not in browser_args:
                browser_args.append("--disable-dev-shm-usage")
            
            self.browser = await zd.start(
                headless=self._headless,
                browser_args=browser_args,
                no_sandbox=True # As requested by error message
            )
            
            self._initialized = True
            self._chat_id = chat_id
            
            # Navigate to DeepSeek
            await self.browser.get("https://chat.deepseek.com")
            await asyncio.sleep(2)
            
            # Use our fast_login instead of base _login
            await self.fast_login()

        async def send_message(
            self,
            message: str,
            slow_mode: bool = False,
            deepthink: bool = False,
            search: bool = False,
            timeout: int = 120,
            slow_mode_delay: float = 0.25
        ) -> Response:
            if not self._initialized:
                raise MissingInitialization("You must run the initialize method before using this method.")

            timeout += 120 if deepthink else 0
            timeout += 60 if search else 0

            print(f"      [SmartDeepSeek] Typing message...")
            textbox = await self.browser.main_tab.select(self.selectors.interactions.textbox)
            await textbox.send_keys(message)

            # Toggle Features
            # DeepThink (R1)
            try:
                dt_btn = await self.browser.main_tab.select("div.ds-toggle-button:nth-child(1)", timeout=2)
                is_active = False
                try:
                    await self.browser.main_tab.select("div.ds-toggle-button.ds-toggle-button--selected:nth-child(1)", timeout=0.1)
                    is_active = True
                except:
                    is_active = False
                
                if deepthink != is_active:
                    print(f"      [SmartDeepSeek] Toggling DeepThink {'ON' if deepthink else 'OFF'}...")
                    await dt_btn.click()
                    await asyncio.sleep(0.5)
            except Exception as e:
                print(f"      [SmartDeepSeek] ⚠️ Failed to toggle DeepThink: {e}")

            # Search
            try:
                search_btn = await self.browser.main_tab.select("div.ds-toggle-button:nth-child(2)", timeout=2)
                is_active = False
                try:
                    await self.browser.main_tab.select("div.ds-toggle-button.ds-toggle-button--selected:nth-child(2)", timeout=0.1)
                    is_active = True
                except:
                    is_active = False
                
                if search != is_active:
                    print(f"      [SmartDeepSeek] Toggling Search {'ON' if search else 'OFF'}...")
                    await search_btn.click()
                    await asyncio.sleep(0.5)
            except Exception as e:
                print(f"      [SmartDeepSeek] ⚠️ Failed to toggle Search: {e}")

            print("      [SmartDeepSeek] Sending...")
            # Count messages before sending (JS based for robustness)
            await asyncio.sleep(2)
            try:
                msg_count_before = await self.browser.main_tab.evaluate("document.querySelectorAll('div.ds-message').length")
            except:
                msg_count_before = 0
                
            send_button = await self.browser.main_tab.select(self.selectors.interactions.send_button)
            await send_button.click()

            return await self._get_response(timeout=timeout, msg_count_before=msg_count_before)

        async def _get_response(
            self,
            timeout: int = 60,
            regen: bool = False,
            msg_count_before: int = 0,
        ) -> Response:
            from DeeperSeek.internal.exceptions import MissingInitialization
            from DeeperSeek.internal.objects import Response
            from bs4 import BeautifulSoup
            from inscriptis import get_text
            
            if not self._initialized:
                raise MissingInitialization("You must run the initialize method before using this method.")

            end_time = time.time() + timeout

            # Wait till the response starts generating (New message appears)
            print("      [SmartDeepSeek] Waiting for response generation...")
            started = False
            while time.time() < end_time:
                try:
                    current_count = await self.browser.main_tab.evaluate("document.querySelectorAll('div.ds-message').length")
                    if current_count > msg_count_before:
                        started = True
                        break
                except:
                    pass
                await asyncio.sleep(0.5)
            
            if not started:
                print("      [SmartDeepSeek] Timed out waiting for generation start.")
                return None

            # Wait for completion
            print("      [SmartDeepSeek] Waiting for completion...")
            last_check = time.time()
            last_text_len = 0
            stable_count = 0
            
            while time.time() < end_time:
                elapsed = time.time() - (end_time - timeout)
                remaining = end_time - time.time()
                
                # Progress indicator every 30s
                if time.time() - last_check > 30:
                    print(f"      [SmartDeepSeek] Still waiting... ({elapsed:.0f}s elapsed, {remaining:.0f}s remaining)")
                    last_check = time.time()
                
                try:
                    btn = await self.browser.main_tab.select("div._7436101", timeout=2)
                    classes = await btn.get_attribute("class")
                    if "ds-icon-button--disabled" in classes:
                        break
                except:
                    pass
                
                # Fallback: check if text has stabilized (not growing for 5 checks)
                try:
                    msgs = await self.browser.main_tab.select_all("div.ds-message")
                    if msgs and len(msgs) > msg_count_before:
                        current_text = repr(msgs[-1])
                        if len(current_text) == last_text_len:
                            stable_count += 1
                            if stable_count >= 5:
                                print("      [SmartDeepSeek] Response stabilized (fallback detection)")
                                break
                        else:
                            stable_count = 0
                            last_text_len = len(current_text)
                except:
                    pass
                    
                await asyncio.sleep(1)
                
            # Extract text
            try:
                msgs = await self.browser.main_tab.select_all("div.ds-message")
                if not msgs or len(msgs) <= msg_count_before:
                    return None
                last_msg = msgs[-1]
            except:
                return None
            
            print("      [SmartDeepSeek] Extracting text...")
            soup = BeautifulSoup(repr(last_msg), 'html.parser')
            
            # Check for Server Busy
            response_text = get_text(str(last_msg)).strip()
            from DeeperSeek.internal.exceptions import ServerDown
            if "server is busy" in response_text.lower():
                 raise ServerDown("The server is busy. Please try again later.")

            markdown_blocks = soup.find_all("div", class_ = lambda x: x and "ds-markdown" in x)
            response_text = "\n\n".join(get_text(str(block)).strip() for block in markdown_blocks)

            search_results = None
            deepthink_content = None

            # Extract DeepThink Content
            try:
                think_content_div = soup.find("div", class_="ds-think-content")
                if think_content_div:
                    deepthink_content = get_text(str(think_content_div)).strip()
            except:
                pass
                
            # Check for empty response
            if not response_text or not response_text.strip():
                print("      [SmartDeepSeek] ⚠️ Extracted text is empty.")
                return None

            return Response(
                text = response_text,
                chat_id = self._chat_id,
                deepthink_duration = None,
                deepthink_content = deepthink_content,
                search_results = search_results
            )

        async def fast_login(self) -> None:
            """
            Optimized login:
            1. Check if already logged in (fastest path)
            2. If not, inject auth and reload
            """
            if not self._initialized:
                raise Exception("You must run the initialize method before using this method.")

            # FAST PATH: Check if already logged in
            print("      [DeeperSeek] Quick session check...")
            try:
                await self.browser.main_tab.wait_for("textarea", timeout=3)
                print("      [DeeperSeek] ✅ Session already active!")
                self._session_valid = True
                return
            except:
                pass

            # SLOW PATH: Need to inject auth
            try_paths = [
                os.path.join(os.path.dirname(__file__), 'Input', 'deeperseek_auth_manual.json'),
                os.path.join(os.getcwd(), 'Input', 'deeperseek_auth_manual.json')
            ]
            manual_auth_file = None
            for p in try_paths:
                if os.path.exists(p):
                    manual_auth_file = p
                    break
            
            if manual_auth_file:
                try:
                    print("      [DeeperSeek] 📥 Injecting auth...")
                    with open(manual_auth_file, 'r') as f:
                        auth_data = json.load(f)
                    
                    ls_data = auth_data.get('localStorage', {})
                    user_token = ls_data.get('userToken')
                    
                    if user_token:
                         safe_token = json.dumps(user_token)
                         await self.browser.main_tab.evaluate(f"localStorage.setItem('userToken', {safe_token})")
                         await self.browser.main_tab.reload()
                         await asyncio.sleep(2)
                except Exception as e:
                    print(f"      [DeeperSeek] ⚠️ Auth injection failed: {e}")

            # Final check
            try:
                await self.browser.main_tab.wait_for("textarea", timeout=5)
                print("      [DeeperSeek] ✅ Login successful!")
                self._session_valid = True
            except:
                print("      [DeeperSeek] ❌ Login failed. Manual intervention needed.")
                self._session_valid = False

    return SmartDeepSeek


async def _get_or_create_client():
    """Get persistent client, creating if needed."""
    global _persistent_client, _initialized
    
    with _client_lock:
        if _persistent_client is not None and _initialized:
            # Reuse existing client
            return _persistent_client
    
    settings = _load_settings()
    
    SmartDeepSeek = _create_smart_deepseek_class()
    
    profile_path = os.path.join(os.path.dirname(__file__), 'Input', 'deeperseek_chrome_profile')
    os.makedirs(profile_path, exist_ok=True)
    
    token = settings.get('token')
    email = settings.get('email')
    password = settings.get('password')
    
    if not token and not (email and password):
        token = "EXISTING_SESSION_PLACEHOLDER"

    print("      [DeeperSeek] Creating persistent client...")
    client = SmartDeepSeek(
        headless=settings['headless'],
        email=email,
        password=password,
        token=token,
        chrome_args=[f"--user-data-dir={profile_path}"]
    )
    
    await client.initialize()
    await client.fast_login()
    
    with _client_lock:
        _persistent_client = client
        _initialized = True
    
    return client


async def _send_message_async(prompt, use_deepthink=False):
    """Send message using persistent client."""
    client = await _get_or_create_client()
    
    if not client._session_valid:
        # Try to recover session
        await client.fast_login()
        if not client._session_valid:
            return None, False
    
    response_obj = await client.send_message(prompt, deepthink=use_deepthink, search=False)
    
    if response_obj and response_obj.text:
        return response_obj.text, True
    return None, False


def call_deeperseek(prompt, max_retries=3, use_deepthink=False):
    """
    Call DeeperSeek with the given prompt.
    Uses persistent browser session for speed.
    """
    global _stats
    _stats["calls"] += 1
    
    settings = _load_settings()
    if use_deepthink is None:
        use_deepthink = settings['deep_think']

    # Use shared event loop for persistence
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        for attempt in range(max_retries):
            try:
                content, success = loop.run_until_complete(_send_message_async(prompt, use_deepthink))
                
                if success:
                    _stats["successes"] += 1
                    return content, True
                else:
                    print(f"      [DeeperSeek] Empty response (attempt {attempt+1}/{max_retries})")
                    time.sleep(3)
                    
            except Exception as e:
                error_str = str(e).lower()
                if "rate" in error_str or "limit" in error_str:
                    wait_time = 30 * (attempt + 1)
                    print(f"      [DeeperSeek] Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                elif "server is busy" in error_str:
                     print(f"      [DeeperSeek] Server busy, waiting 20s...")
                     time.sleep(20)
                elif "missingcredentials" in error_str:
                     print(f"      [DeeperSeek] ⚠️ Missing credentials. Skipping.")
                     return None, False
                else:
                    print(f"      [DeeperSeek] Error (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(5)
    finally:
        # Don't close the loop - we want persistence
        pass
    
    _stats["failures"] += 1
    return None, False

def call_with_fallback(prompt, use_pro=False, api_key=None):
    """
    Wrapper to match the interface of other clients.
    
    Args:
        prompt: The text prompt
        use_pro: If True, use DeepThink (R1) for better reasoning (overrides settings)
        api_key: Ignored (DeeperSeek uses browser auth)
        
    Returns:
        (content, model_name) tuple
    """
    settings = _load_settings()
    
    # use_pro overrides settings
    deep_think = use_pro or settings['deep_think']
    content, success = call_deeperseek(prompt, use_deepthink=deep_think)
    
    if success:
        model_name = "deepseek-r1" if deep_think else "deepseek-v3"
        return content, model_name
    return None, None


def cleanup():
    """Cleanup persistent client (call at end of pipeline)."""
    global _persistent_client, _initialized
    with _client_lock:
        if _persistent_client:
            try:
                # Best-effort cleanup
                if hasattr(_persistent_client, 'browser') and _persistent_client.browser:
                    asyncio.get_event_loop().run_until_complete(_persistent_client.browser.close())
            except:
                pass
            _persistent_client = None
            _initialized = False
    print("  [DeeperSeek] Client cleaned up.")


if __name__ == "__main__":
    # Quick test
    print("Testing DeeperSeek client (OPTIMIZED)...")
    if check_cookies():
        print("\n--- Test 1 ---")
        content, model = call_with_fallback("Say 'HELLO' in English.")
        if content:
            print(f"✅ Success! Model: {model}")
            print(f"Response: {content[:100]}...")
        else:
            print("❌ Failed")
            
        print("\n--- Test 2 (FAST - reusing session) ---")
        start = time.time()
        content2, model2 = call_with_fallback("Say 'WORLD' in English.")
        elapsed = time.time() - start
        if content2:
            print(f"✅ Success! Model: {model2} | Time: {elapsed:.2f}s")
            print(f"Response: {content2[:100]}...")
        else:
            print("❌ Failed")
            
        cleanup()
    else:
        print("❌ DeeperSeek not available")
