
"""
Gemini Selenium Client
======================
Wrapper for Gemini Selenium automation (based on pozitivhirekP/gemini_filter.py).
Provides a `call_with_fallback(prompt, use_pro)` method for integration with `backend_orchestrator`.

Uses a DEDICATED gemini_profile folder (not system Chrome) to avoid conflicts.
"""

import os
import sys
import time
import subprocess
from chromedriver_updater import get_chromedriver_path
import pyperclip
import threading
import atexit
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# Thread lock to prevent concurrent Selenium sessions
_selenium_lock = threading.Lock()

# --- CONFIGURATION ---

BASE_DIR = Path(__file__).parent

# DEDICATED Gemini profile (NOT system Chrome) - avoids lock conflicts
GEMINI_PROFILE_DIR = BASE_DIR / "gemini_profile"

PAGE_LOAD_TIMEOUT = 30
RESPONSE_WAIT_TIMEOUT = 480  # 8 minutes — must be LESS than external call_with_timeout wrapper (540s)
NO_CONTENT_ABORT_TIMEOUT = 120  # 2 minutes — abort early if Gemini generates zero content

GEMINI_URL = "https://gemini.google.com/"

# Global shared driver
_shared_driver = None


def cleanup_zombie_chrome_processes():
    """Stops zombie Chrome processes that use our dedicated gemini_profile."""
    try:
        profile_path = str(GEMINI_PROFILE_DIR)
        
        if sys.platform != 'win32':
            result = subprocess.run(
                ['pkill', '-f', f'user-data-dir={profile_path}'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("   🧹 Zombie Chrome processes stopped")
                time.sleep(1)
        else:
            result = subprocess.run(
                f'taskkill /F /FI "COMMANDLINE eq *{profile_path}*"',
                shell=True,
                capture_output=True,
                text=True
            )
            if 'SUCCESS' in result.stdout:
                print("   🧹 Zombie Chrome processes stopped")
                time.sleep(1)
                
    except Exception as e:
        pass  # Non-critical


def create_driver(headless=False):
    """Creates a Chrome WebDriver for Gemini using dedicated profile."""
    cleanup_zombie_chrome_processes()
    
    # FIX: Prevent 'cannot parse internal JSON template' by removing transient Local State
    try:
        local_state_path = GEMINI_PROFILE_DIR / "Local State"
        if local_state_path.exists():
            local_state_path.unlink()
    except Exception:
        pass

    
    options = Options()
    
    # Use EAGER strategy to not wait for analytics and images to fully load
    options.page_load_strategy = 'eager'
    
    # Use DEDICATED profile (not system Chrome!)
    options.add_argument(f"--user-data-dir={GEMINI_PROFILE_DIR}")
    
    if headless:
        options.add_argument('--headless=new')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=hu-HU')
    
    # Always use the custom auto-updater to strictly match installed Chrome version
    print("   ℹ️  Using Chromedriver Auto-updater (exact match Chrome version)")
    service = Service(get_chromedriver_path())

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    
    return driver


def _cleanup_shared_driver():
    """Gracefully close the global shared browser on script exit."""
    global _shared_driver
    if _shared_driver:
        try:
            _shared_driver.quit()
        except:
            pass

atexit.register(_cleanup_shared_driver)


def check_cookies():
    """Checks if we can access Gemini (are logged in). Thread-safe."""
    with _selenium_lock:
        try:
            driver = create_driver(headless=True)
            driver.get(GEMINI_URL)
            time.sleep(5)
            
            if "accounts.google.com" in driver.current_url:
                print("Gemini Selenium: Not logged in.")
                driver.quit()
                return False
                
            try:
                 driver.find_element(By.CSS_SELECTOR, "body")
                 print("Gemini Selenium: Access confirmed.")
                 driver.quit()
                 return True
            except:
                 driver.quit()
                 return False
        except Exception as e:
            print(f"Gemini Selenium check failed: {e}")
            return False


def wait_for_gemini_ready(driver, timeout=30):
    """Waits for Gemini to load."""
    print(f"   ⏳ Waiting for Gemini to load ({timeout}s)...")
    try:
        selectors = [
            "rich-textarea .ql-editor", 
            "div[contenteditable='true'][role='textbox']",
            "div[role='textbox']",
        ]
        
        WebDriverWait(driver, timeout).until(
            lambda d: any(d.find_elements(By.CSS_SELECTOR, s) for s in selectors)
        )
        time.sleep(0.5)
        print("   ✅ Gemini input found")
        return True
    except TimeoutException:
        print(f"   ❌ Gemini did not load in time ({timeout}s)!")
        return False


def ensure_model_mode(driver, use_pro=False):
    """Ensures the correct Gemini model is selected (Pro vs Fast/Flash)."""
    target_mode = "Pro" if use_pro else "Fast/Flash"
    print(f"   🦸 Checking model mode (Target: {target_mode})...")
    
    try:
        try:
             # Find switcher button
             switcher = WebDriverWait(driver, 5).until(
                 EC.presence_of_element_located((By.CSS_SELECTOR, "button.input-area-switch"))
             )
             current_text = switcher.text.lower()
             
             # Keywords for Pro/Advanced
             pro_keywords = ["pro", "advanced"]
             # Keywords for Fast/Flash (Hungarian: Gyors)
             fast_keywords = ["flash", "gyors", "gemini"] 
             
             is_currently_pro = any(k in current_text for k in pro_keywords)
             
             if use_pro:
                 if is_currently_pro:
                     print(f"   ✅ Already in Pro mode ({current_text})")
                     return True
             else:
                 # We want Fast mode (Not Pro)
                 # Note: "Gemini" is often the default name for Fast/Flash, 
                 # but "Gemini Advanced" contains "Gemini" too.
                 # So we rely on NOT containing Pro/Advanced keywords if possible, 
                 # or explicitly matching Fast keywords if they appear.
                 if not is_currently_pro:
                     print(f"   ✅ Already in Fast/Flash mode ({current_text})")
                     return True
             
             print(f"   ⚠️ Current mode: {current_text}. Switching to {target_mode}...")
             switcher.click()
             time.sleep(0.3)
             
             options = driver.find_elements(By.CSS_SELECTOR, "mat-option, button.bard-mode-list-button, div[role='menuitem']")
             target_option = None
             
             for opt in options:
                 txt = opt.text.lower()
                 if not txt.strip():
                     continue
                 print(f"   [DEBUG] Found model option: '{txt}'")
                 if use_pro:
                     if any(k in txt for k in pro_keywords) and "gondolkodó" not in txt and "thinking" not in txt:
                         target_option = opt
                         break
                 else:
                     # For Fast mode, prefer "Gyors", "Flash", or just "Gemini" (if it's not Advanced)
                     # Strategy: Pick the one that ISN'T Pro/Advanced and ISN'T Thinking/Gondolkodó (unless requested)
                     if not any(k in txt for k in pro_keywords) and "gondolkodó" not in txt and "thinking" not in txt:
                         target_option = opt
                         break
                         
             if target_option:
                 print(f"   [DEBUG] Selecting target: '{target_option.text}'")
                 try:
                     driver.execute_script("arguments[0].scrollIntoView(true);", target_option)
                     time.sleep(0.5)
                     driver.execute_script("arguments[0].click();", target_option)
                 except Exception as e:
                     print(f"   [DEBUG] JS click failed, falling back to standard click: {e}")
                     target_option.click()
                 time.sleep(1)
                 print(f"   ✅ Switched to {target_mode} mode")
                 # Verify
                 try:
                     # The switcher text might take a moment to update
                     time.sleep(0.5)
                     print(f"   [DEBUG] Mode after switch: {switcher.text}")
                 except:
                     pass
                 return True
             else:
                 print(f"   ⚠️ Target option not found for {target_mode}")
                 # Close menu
                 switcher.click()
                 return True # Assuming default might be fallback
                 
        except TimeoutException:
             print("   ⚠️ Switcher not found, assuming default model")
             return True

    except Exception as e:
        print(f"   ⚠️ Model check failed: {e}")
    return False


def click_new_chat(driver):
    """Navigates to a new chat using UI button to avoid reload."""
    try:
        # Try finding the "New Chat" button by aria-label (locale specific) or class
        selectors = [
            "a[aria-label='Új csevegés']",  # Hungarian
            "a[aria-label='New chat']",
            "a[aria-label='New Chat']",
            "div[data-test-id='new-chat-button']",
            ".side-nav-action-button" # Fallback, might need index
        ]
        
        for selector in selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_displayed():
                    btn.click()
                    print("   ✅ Clicked 'New Chat' button")
                    time.sleep(0.5)
                    return True
            except:
                continue
                
        # If UI click fails, use navigation
        print("   ⚠️ New Chat button not found, checking URL...")
        if "/app" not in driver.current_url:
             driver.get("https://gemini.google.com/app")
             time.sleep(3)
        return True
    except:
        return False


def submit_prompt(driver, prompt, use_pro=False):
    """Submits the prompt via JS injection."""
    try:
        ensure_model_mode(driver, use_pro=use_pro)
        
        # Find input element
        input_element = None
        selectors = [
             "div.ql-editor[contenteditable='true'][role='textbox']",
             "rich-textarea .ql-editor[contenteditable='true']",
             "div[role='textbox']"
        ]
        
        for selector in selectors:
            try:
                input_element = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if input_element:
                    break
            except:
                continue
                
        if not input_element:
             print("   ❌ Input element not found!")
             return False
        
        input_element.click()
        time.sleep(0.2)
        
        # Prepare text with standard newlines (no <br>)
        clean_prompt = prompt.replace('\r\n', '\n')
        
        # Method 3: Clean Injection (Focus -> Select All -> insertText)
        # Proven most robust for large text (10k+ chars) in browser tests
        print(f"   📋 Injecting prompt via Clean Injection ({len(prompt)} chars)...")
        try:
             driver.execute_script("""
                const editor = arguments[0];
                const text = arguments[1];
                
                editor.focus();
                
                // Select all content to ensure we replace properly (or append if empty)
                const range = document.createRange();
                const sel = window.getSelection();
                range.selectNodeContents(editor);
                sel.removeAllRanges();
                sel.addRange(range);
                
                // Use execCommand to insert text natively
                document.execCommand('insertText', false, text);
                
                // Dispatch input event to notify app
                editor.dispatchEvent(new Event('input', { bubbles: true }));
             """, input_element, clean_prompt)
             
             time.sleep(0.5) # Allow UI to process large text
             
        except Exception as js_err:
            print(f"   ⚠️ JS injection failed: {js_err}, using send_keys fallback...")
            # Fallback: send in larger chunks if execCommand fails
            chunk_size = 5000
            input_element.send_keys(Keys.COMMAND + "a") # Clear first
            input_element.send_keys(Keys.DELETE)
            for i in range(0, len(prompt), chunk_size):
                input_element.send_keys(prompt[i:i+chunk_size])
                time.sleep(0.2)
            time.sleep(0.5)

        # Send
        try:
             # Wait for button to be clickable
             send_selector = "button.send-button, button[aria-label='Üzenet küldése'], button[aria-label='Send message']"
             send_button = WebDriverWait(driver, 5).until(
                 EC.element_to_be_clickable((By.CSS_SELECTOR, send_selector))
             )
             
             # Click
             driver.execute_script("arguments[0].click();", send_button)
        except Exception as e:
             print(f"   ⚠️ Send click failed: {e}. Trying Enter key...")
             # Press Enter to send
             mod_key = Keys.COMMAND if sys.platform == 'darwin' else Keys.CONTROL
             input_element.send_keys(Keys.ENTER)
             
        time.sleep(0.5)
        print("   ✅ Prompt sent!")
        return True
        
    except Exception as e:
        print(f"   ❌ Submit failed: {e}")
        return False


def wait_for_response(driver, timeout=RESPONSE_WAIT_TIMEOUT):
    """Waits for response completion."""
    print(f"   ⏳ Waiting for response (max {timeout // 60} min)...")
    
    check_interval = 2
    start_time = time.time()
    last_content_length = 0
    stable_count = 0
    current_length = 0
    
    no_content_deadline = start_time + NO_CONTENT_ABORT_TIMEOUT
    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        
        try:
            # Check for Copy button
            copy_buttons = driver.find_elements(By.CSS_SELECTOR, 
                "button[data-test-id='copy-button'], button[aria-label='Másolás'], button[aria-label='Copy']")
            
            if copy_buttons:
                print(f"   ✅ Copy button appeared ({elapsed}s) - response ready!")
                time.sleep(2)
                return True
            
            # Check response length stability
            response_elements = driver.find_elements(By.CSS_SELECTOR, 
                "message-content, .response-container, .model-response-text")
            
            if response_elements:
                current_content = response_elements[-1].text
                current_length = len(current_content)
                
                if current_length > 0 and current_length == last_content_length:
                    stable_count += 1
                    if stable_count >= 4:  # 20s stable
                        print(f"   ✅ Response stable ({current_length} chars, {elapsed}s)")
                        return True
                else:
                    stable_count = 0
                    last_content_length = current_length
                    
                    if elapsed % 30 == 0:
                        print(f"   ⏳ Generating... ({current_length} chars, {elapsed}s)")
                        
        except Exception as e:
            pass
        
        # Early abort: if no content at all after NO_CONTENT_ABORT_TIMEOUT, Gemini is stuck
        if current_length == 0 and time.time() > no_content_deadline:
            print(f"   ❌ No content generated after {NO_CONTENT_ABORT_TIMEOUT}s — aborting early")
            return False
        time.sleep(check_interval)
    
    if last_content_length > 0:
        print(f"   ⏱️ Timeout, but response exists ({last_content_length} chars)")
        return True
    
    print(f"   ❌ Timeout ({timeout}s) - no response")
    return False


def copy_response(driver):
    """Copies the last response using DOM extraction (works in headless)."""
    try:
        # Method 1: Direct DOM extraction (preferred, works in headless)
        response_selectors = [
            "message-content",
            ".response-container", 
            ".model-response-text",
            "[data-message-content]",
            ".markdown-content"
        ]
        
        for selector in response_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                # Get the last response element
                last_elem = elements[-1]
                # Use JavaScript to get full text content including nested elements
                response = driver.execute_script("return arguments[0].innerText;", last_elem)
                if response and len(response.strip()) > 50:
                    print(f"   ✅ Response extracted via DOM ({len(response)} chars)")
                    return response.strip()
        
        # Method 2: Try copy button + clipboard (fallback for non-headless)
        try:
            pyperclip.copy("")
            time.sleep(0.3)
            
            buttons = driver.find_elements(By.CSS_SELECTOR, 
                "button[data-test-id='copy-button'], button[aria-label='Másolás'], button[aria-label='Copy']")
            if buttons:
                btn = buttons[-1]
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                time.sleep(0.5)
                
                actions = ActionChains(driver)
                actions.move_to_element(btn).pause(0.2).click().perform()
                time.sleep(1.5)
                
                response = pyperclip.paste()
                if response and len(response.strip()) > 50:
                    print(f"   ✅ Response copied via clipboard ({len(response)} chars)")
                    return response
        except Exception as clipboard_err:
            print(f"   ⚠️ Clipboard method failed: {clipboard_err}")
             
    except Exception as e:
        print(f"   ❌ Copy failed: {e}")
        
    return None


def _ensure_healthy_driver(force_headless=False):
    """
    Returns a healthy driver instance. If the global driver is dead 
    (InvalidSessionIdException), it tears it down and starts a new one.
    """
    global _shared_driver
    
    if _shared_driver is not None:
        try:
            # Active check to see if the session is actually alive
            _ = _shared_driver.current_url
            
            # Clean up zombie tabs if there are too many (memory leak prevention)
            if len(_shared_driver.window_handles) > 1:
                current_window = _shared_driver.current_window_handle
                for handle in _shared_driver.window_handles:
                    if handle != current_window:
                        _shared_driver.switch_to.window(handle)
                        _shared_driver.close()
                _shared_driver.switch_to.window(current_window)
                
            return _shared_driver
        except Exception as e:
            print(f"   ⚠️ Shared driver session dead ({e}). Recreating...")
            try:
                _shared_driver.quit()
            except:
                pass
            _shared_driver = None
            time.sleep(1) # Give OS a moment to clean handles
            
    # If we get here, we need a new driver
    _shared_driver = create_driver(headless=force_headless)
    return _shared_driver

def call_with_fallback(prompt, use_pro=False, timeout=None):
    """
    Main interface for BackendOrchestrator. Thread-safe.
    Args:
        prompt (str): The prompt to send.
        use_pro (bool): Whether to try enabling Pro mode.
        timeout (int): Response wait timeout in seconds (default: RESPONSE_WAIT_TIMEOUT).
    Returns:
        tuple: (content, model_name) or (None, None)
    """
    response_timeout = timeout or RESPONSE_WAIT_TIMEOUT
    
    # We allow up to 2 full driver restart attempts if the session unexpectedly dies mid-flight.
    max_attempts = 2
    
    with _selenium_lock:
        for attempt in range(max_attempts):
            try:
                # macOS Safety: Force headless if not main thread to avoid SIGABRT
                is_main_thread = threading.current_thread() is threading.main_thread()
                force_headless = not is_main_thread
                
                if force_headless and attempt == 0:
                    print("   ⚠️ Running in worker thread: FORCING HEADLESS mode (macOS safety)")
                
                # Get a verified healthy driver
                driver = _ensure_healthy_driver(force_headless)
                
                # We always navigate to the root Gemini URL to ensure we start fresh on a "New Chat" state 
                # rather than trying to reuse an old chat which can bloat memory.
                driver.get(GEMINI_URL)
                
                if "accounts.google.com" in driver.current_url:
                    print("Gemini Selenium: Login required! Run --setup first.")
                    return None, None
                    
                if not wait_for_gemini_ready(driver):
                    print("Gemini Selenium: Page did not load properly.")
                    if attempt < max_attempts - 1:
                         continue # Retry full cycle
                    return None, None
                    
                click_new_chat(driver)
                
                if submit_prompt(driver, prompt, use_pro=use_pro):
                     if wait_for_response(driver, timeout=response_timeout):
                         content = copy_response(driver)
                         if content:
                              # specialized cleaning for JSON
                              if "```json" in content:
                                  content = content.split("```json")[1].split("```")[0].strip()
                              elif "```" in content:
                                  content = content.replace("```", "").strip()
                              return content, "gemini-selenium-pro" if use_pro else "gemini-selenium"
                
                # If submit or wait failed but didn't throw an outright exception, just return None
                return None, None
                
            except Exception as e:
                print(f"Gemini Selenium Error (Attempt {attempt + 1}/{max_attempts}): {e}")
                # If we hit an exception like InvalidSessionId during the process, destroy the driver so the next loop recreates it
                global _shared_driver
                if _shared_driver:
                    try: _shared_driver.quit()
                    except: pass
                    _shared_driver = None
                    
        # If all attempts exhausted
        return None, None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
         print("Running setup mode. Close browser when done.")
         d = create_driver(headless=False)
         d.get(GEMINI_URL)
         input("Press Enter to close after login...")
         d.quit()
    else:
         print("Test run 1...")
         res1 = call_with_fallback("Hello, are you working via Selenium?", use_pro=True)
         print("Result 1:", res1)
         
         print("\n\nTest run 2: testing tab reuse without restarting Chrome...")
         res2 = call_with_fallback("Can you still hear me in the shared session? Reply short.", use_pro=True)
         print("Result 2:", res2)
