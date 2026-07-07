import json
import os
import shutil
import sys
import time
import glob
import subprocess
import atexit
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from chromedriver_updater import get_chromedriver_path, clear_chrome_caches

import datetime

# --- Configuration ---
PROFILE_PATH = "/Users/viktorposa/Library/Application Support/Google/Chrome"

# Determine dynamic paths based on today's date
current_date = datetime.date.today().strftime("%Y-%m-%d")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "Output", current_date)

TARGET_URL = "https://aistudio.google.com/generate-speech?model=gemini-2.5-pro-preview-tts"

# Settings
STYLE_INSTRUCTIONS = "Magyar nyelvű rádiós hírfelolvasás. A magyar szavakat tökéletes magyar kiejtéssel olvasd, az angol szavakat (pl. márkaneveket, tech kifejezéseket) természetes angol kiejtéssel, de a kettő ne keveredjen. A tempó legyen nyugodt, jól artikulált, magabiztos. A hangsúlyozás természetes, nem monoton és nem túl lelkes."
VOICE_NAME = "Zephyr"
TEMPERATURE = "0.8"

def setup_driver():
    """Sets up the Selenium driver with a PERSISTENT cloned Chrome profile."""
    # Use a persistent directory so login is saved after the first time
    profile_dir = os.path.join(os.path.dirname(__file__), "Input", "ChromeTTSProfile")
    
    # Only clone if it acts as a fresh start (doesn't exist yet)
    if not os.path.exists(profile_dir):
        print(f"📦 First run: Cloning Chrome profile to '{profile_dir}'...")
        os.makedirs(profile_dir)
        original_default = os.path.join(PROFILE_PATH, "Default")
        dest_default = os.path.join(profile_dir, "Default")
        
        try:
            # Copy essential parts
            ignore_func = shutil.ignore_patterns("Cache*", "Service Worker*", "Code Cache*", "Safe Browsing*", "History*")
            shutil.copytree(original_default, dest_default, ignore=ignore_func)
            
            # Copy 'Local State' for cookie encryption
            original_local_state = os.path.join(PROFILE_PATH, "Local State")
            if os.path.exists(original_local_state):
                 shutil.copy(original_local_state, os.path.join(profile_dir, "Local State"))
                 
            print("✅ Profile cloned successfully.")
        except Exception as e:
            print(f"⚠️ Warning: Could not clone profile perfectly: {e}")
    else:
        print(f"📂 Using existing persistent profile at '{profile_dir}' (Login should be remembered)")

        # Remove lock files and potentially corrupted Local State to prevent "session not created" errors
        lock_files = ['SingletonLock', 'SingletonSocket', 'SingletonCookie', 'Local State']
        for lock_file in lock_files:
            lock_path = os.path.join(profile_dir, lock_file)
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                    print(f"  🔓 Removed lock file: {lock_file}")
                except Exception as e:
                    print(f"  ⚠️ Could not remove {lock_file}: {e}")

    # Clear transient caches every launch. Prevents Chrome crashes from
    # corrupted GPU/Code/Service Worker caches and keeps the profile lean
    # (this profile had ballooned to 5GB+). Login state is preserved.
    clear_chrome_caches(profile_dir)
        
    chrome_options = Options()
    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    chrome_options.add_argument("--profile-directory=Default")
    
    # Enable downloads
    prefs = {
        "download.default_directory": OUTPUT_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    # Standard flags
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(get_chromedriver_path())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver, profile_dir

def wait_and_click(driver, by, value, timeout=10, name="Element"):
    print(f"⏳ Waiting for {name} ({value})...")
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )
        element.click()
        print(f"✅ Clicked {name}")
        return element
    except Exception as e:
        print(f"❌ Failed to click {name}: {e}")
        raise

def wait_and_send_keys(driver, by, value, keys, timeout=10, name="Element", clear=True):
    print(f"⏳ Waiting for data input {name} ({value})...")
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        if clear:
            element.clear()
            # Sometimes clear() isn't enough for complex fields
            element.send_keys(Keys.COMMAND + "a")
            element.send_keys(Keys.DELETE)
            
        element.send_keys(keys)
        print(f"✅ Input sent to {name}")
        return element
    except Exception as e:
        print(f"❌ Failed to input to {name}: {e}")
        raise

def set_slider(driver, value):
    print(f"Set temperature to {value}...")
    try:
        # First ensure section is expanded
        try:
            # Try to find the button to expand if needed. 
            # The button usually has aria-label="Expand or collapse Model settings"
            # Or check if input is visible.
            expand_btn = driver.find_elements(By.XPATH, "//button[@aria-label='Expand or collapse Model settings']")
            if expand_btn:
                # Check if already expanded? usually icon indicates.
                # Just click it if the input isn't visible?
                # Safer: try to access input. If fails, click button.
                 pass
        except:
             pass

        # Robust way found via browser control:
        # 1. Expand 'Model settings' if the input isn't found.
        try:
             driver.find_element(By.CSS_SELECTOR, "input.slider-number-input")
        except:
             print("🔽 Expanding Model Settings...")
             expand_btn = WebDriverWait(driver, 5).until(
                 EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Expand or collapse Model settings']"))
             )
             expand_btn.click()
             time.sleep(1)

        # 2. Use JS to set the value on the numeric input which is 'input.slider-number-input.small'
        # The key is dispatching input/change/blur events.
        script = """
        var input = document.querySelector('input.slider-number-input');
        if (input) {
            input.value = arguments[0];
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
        }
        return false;
        """
        success = driver.execute_script(script, str(value))
        
        if success:
            print(f"✅ Set temperature to {value} via JS.")
        else:
            print(f"❌ Failed to find temperature input for JS injection.")
            
    except Exception as e:
         print(f"❌ Failed to set temperature: {e}")

def main():
    print("🚀 Starting Gemini TTS Generator (2 parts)...")
    
    # --- Caffeinate setup: prevent system from sleeping while script runs ---
    caffeinate_proc = None
    try:
        # -u: wake display, -d: prevent display sleep, -i: prevent idle sleep
        # -w PID: automatically exit caffeinate when this script exits
        current_pid = str(os.getpid())
        caffeinate_proc = subprocess.Popen(["caffeinate", "-u", "-d", "-i", "-w", current_pid])
        print(f"☕ Caffeinate activated (watching PID {current_pid}): system will not sleep until the script finishes.")
        
        def stop_caffeinate():
            if caffeinate_proc and caffeinate_proc.poll() is None:
                caffeinate_proc.terminate()
                try:
                    caffeinate_proc.wait(timeout=2)
                except:
                    pass
                print("☕ Caffeinate stopped: system can sleep now after audio playback finishes.")
        
        atexit.register(stop_caffeinate)
    except Exception as e:
        print(f"⚠️ Could not start caffeinate, system might go to sleep: {e}")
    # ------------------------------------------------------------------------

    parts = [
        ("Part 1: Időjárás", "tts_idojaras.txt", "idojaras"),
        ("Part 2: Piacok és üzlet", "tts_piacok.txt", "piacok"),
        ("Part 3: Hírcímek", "tts_headlines.txt", "hirek"),
        ("Part 4: Hírcsárda", "tts_hircsarda.txt", "hircsarda"),
    ]

    try:
        for idx, (part_name, filename, suffix) in enumerate(parts):
            input_file = os.path.join(OUTPUT_DIR, filename)
            output_mp3 = os.path.join(OUTPUT_DIR, f"tts_{suffix}.mp3")

            if not os.path.exists(input_file):
                print(f"ℹ️ Skipping {part_name}, file not found: {input_file}")
                continue

            if os.path.exists(output_mp3):
                print(f"⏩ Skipping {part_name}, MP3 already exists: {output_mp3}")
                continue

            with open(input_file, 'r', encoding='utf-8') as f:
                tts_text = f.read()

            print(f"\n{'='*50}")
            print(f"--- Processing {part_name} ---")
            print(f"{'='*50}")
            print(f"📖 Read {len(tts_text)} chars.")

            try:  # BULLETPROOF: fresh driver per part so session errors don't cascade
                driver, temp_profile_path = setup_driver()

                driver.get(TARGET_URL)
                print("🌍 Navigation started, page reloading for fresh generation...")
            
                # 1. Dismiss ALL overlays and popups (CDK overlays block clicks!)
                time.sleep(5)  # Let page fully settle including overlays
                try:
                    # First: dismiss the CDK overlay that blocks everything, and accept Terms of Service
                    driver.execute_script("""
                        // Click any Dismiss, Continue, Got it, I agree buttons in modals
                        document.querySelectorAll('button, a, [role="button"], span').forEach(btn => {
                            var text = btn.textContent.toLowerCase().trim();
                            if ((text === 'dismiss' || text === 'continue' || text === 'got it' || text === 'i agree' || text === 'accept') && btn.offsetParent !== null) {
                                btn.click();
                                if(btn.parentElement) btn.parentElement.click();
                            }
                        });
                        // Fallback: hide all blocking containers just in case
                        document.querySelectorAll('.cdk-overlay-pane, .cdk-overlay-container, .mat-mdc-dialog-container').forEach(el => {
                            el.style.display = 'none';
                        });
                    """)
                    print("✅ Cleared overlay popups via JS")
                    time.sleep(2)
                except Exception as e:
                    print(f"⚠️ Overlay cleanup: {e}")


                # 1b. Check for login wall
                if "accounts.google.com" in driver.current_url:
                    print("🛑 Hit Login Wall! Waiting 60s for manual login...")
                    time.sleep(60)

                # 2. Click the main prompt area to enter editing mode (via JS to bypass overlays)
                # The page loads in a "landing" state with template cards.
                # The actual editing fields only appear after clicking the prompt area.
                try:
                    prompt_btn = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "button.text-input-container"))
                    )
                    # Use JS click to bypass any remaining overlay interceptions
                    driver.execute_script("arguments[0].click();", prompt_btn)
                    print("✅ Clicked main prompt area to enter editing mode (JS click)")
                    time.sleep(4)  # Wait for editing UI to fully load
                except:
                    # Fallback: try XPATH
                    try:
                        prompt_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Turn text into natural-sounding speech')]")
                        if prompt_btns:
                            driver.execute_script("arguments[0].click();", prompt_btns[0])
                            print("✅ Clicked main prompt area (fallback XPATH)")
                            time.sleep(4)
                        else:
                            print("⚠️ Main prompt button not found, may already be in editing mode")
                    except:
                        print("⚠️ Could not click main prompt area")
            
                # 2b. Mode: Click "Text" tab if visible (new UI uses Text/Composer tabs)
                try:
                    text_tabs = driver.find_elements(By.XPATH, "//button[normalize-space(.)='Text']")
                    if not text_tabs:
                        text_tabs = driver.find_elements(By.XPATH, "//button[contains(@class, 'tab') and contains(., 'Text')]")
                    if text_tabs:
                        for tab in text_tabs:
                            if tab.is_displayed():
                                driver.execute_script("arguments[0].click();", tab)
                                print("✅ Mode set to: Text (single-speaker)")
                                break
                    else:
                        print("ℹ️ Text tab not visible, may already be in Text mode")
                except:
                    print("⚠️ Could not find/click Text tab, may already be selected.")
            
                time.sleep(2)
            
                # 2c. Debug: dump what's available now
                try:
                    ta_count = len(driver.find_elements(By.TAG_NAME, "textarea"))
                    ce_count = len(driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']"))
                    btn_count = len([b for b in driver.find_elements(By.TAG_NAME, "button") if b.is_displayed()])
                    print(f"   📊 Debug: {ta_count} textareas, {ce_count} contenteditables, {btn_count} visible buttons")
                except:
                    pass

                # 3. Style instructions → "Scene" field (new UI)
                try:
                    scene_fields = driver.find_elements(By.XPATH, "//textarea[contains(@placeholder, 'bustling') or contains(@aria-label, 'Scene')]")
                    if not scene_fields:
                        # Try generic approach - look for first textarea visible
                        scene_fields = driver.find_elements(By.CSS_SELECTOR, "ms-speech-prompt textarea")
                    if scene_fields:
                        scene = scene_fields[0]
                        scene.click()
                        time.sleep(0.3)
                        scene.send_keys(Keys.COMMAND + "a")
                        scene.send_keys(Keys.DELETE)
                        scene.send_keys(STYLE_INSTRUCTIONS)
                        print("✅ Style instructions entered in Scene field")
                    else:
                        print("⚠️ Scene field not found, skipping style instructions")
                except Exception as e:
                    print(f"⚠️ Style instructions field not found, skipping: {e}")
            
                time.sleep(1)

                # 4. Voice: Click the Speaker chip to open Speaker settings panel
                try:
                    # The new UI shows voice as a chip like "Speaker 1 - Zephyr" inside the speech block
                    speaker_chip = driver.find_elements(By.XPATH, f"//button[contains(., 'Speaker')]")
                    if speaker_chip:
                        # Check if already set to desired voice
                        chip_text = speaker_chip[0].text
                        if VOICE_NAME in chip_text:
                            print(f"✅ Voice already set to {VOICE_NAME}")
                        else:
                            speaker_chip[0].click()
                            print("✅ Clicked Speaker chip to open voice selection")
                            time.sleep(1)
                        
                            # Look for voice option in the Speaker settings sliding panel
                            try:
                                voice_option = WebDriverWait(driver, 5).until(
                                    EC.element_to_be_clickable((By.XPATH, f"//*[contains(@class, 'voice') or contains(@class, 'speaker')]//*[contains(text(), '{VOICE_NAME}')]"))
                                )
                                voice_option.click()
                                print(f"✅ Voice set to {VOICE_NAME}")
                            except:
                                # Try clicking in the right panel Speaker settings
                                try:
                                    voice_cards = driver.find_elements(By.XPATH, f"//*[contains(text(), '{VOICE_NAME}')]")
                                    for vc in voice_cards:
                                        if vc.is_displayed():
                                            vc.click()
                                            print(f"✅ Voice set to {VOICE_NAME} via text match")
                                            break
                                except:
                                    print(f"⚠️ Could not select voice {VOICE_NAME}")
                            time.sleep(1)
                    else:
                        print("⚠️ No Speaker chip found, voice selection skipped")
                except Exception as e:
                    print(f"⚠️ Voice selection failed: {e}")

                # 5. Temperature
                set_slider(driver, TEMPERATURE)
            
                # 6. Text Input - the new UI has the text area inside a speech block
                text_area = None
                try:
                    # Try multiple selectors for the text input
                    selectors_to_try = [
                        # New UI: contenteditable or textarea in speech block
                        (By.CSS_SELECTOR, "ms-speech-prompt textarea"),
                        (By.CSS_SELECTOR, "div.speech-input-wrapper textarea"),
                        (By.XPATH, "//textarea[contains(@placeholder, 'Turn text') or contains(@placeholder, 'natural-sounding')]"),
                        (By.XPATH, "//textarea[contains(@placeholder, 'Enter') or contains(@placeholder, 'Type')]"),
                        # Fallback: any textarea that's not Scene/Sample Context
                        (By.XPATH, "(//ms-speech-prompt//textarea)[last()]"),
                        # Generic: the main large text area
                        (By.CSS_SELECTOR, "textarea.speech-text-input"),
                    ]
                
                    for by, selector in selectors_to_try:
                        try:
                            elements = driver.find_elements(by, selector)
                            for el in elements:
                                if el.is_displayed() and el.get_attribute("aria-label") not in ("Scene", "Sample Context"):
                                    text_area = el
                                    print(f"✅ Found text input via: {selector}")
                                    break
                            if text_area:
                                break
                        except:
                            continue
                
                    # Last resort: find all visible textareas and pick the biggest/last one
                    if not text_area:
                        all_textareas = driver.find_elements(By.TAG_NAME, "textarea")
                        visible = [t for t in all_textareas if t.is_displayed()]
                        if visible:
                            # Skip Scene and Sample Context fields (first two)
                            candidates = [t for t in visible if t.get_attribute("placeholder") and "bustling" not in t.get_attribute("placeholder") and "Previous speaker" not in t.get_attribute("placeholder")]
                            if candidates:
                                text_area = candidates[-1]
                                print(f"✅ Found text input via last-resort textarea scan")
                            elif len(visible) > 2:
                                text_area = visible[-1]
                                print(f"✅ Found text input as last visible textarea")
                
                    # Maybe it's a contenteditable div instead
                    if not text_area:
                        editables = driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']")
                        visible_editables = [e for e in editables if e.is_displayed()]
                        if visible_editables:
                            text_area = visible_editables[-1]
                            print(f"✅ Found text input as contenteditable div")
                        
                except Exception as e:
                    print(f"❌ Failed to find text input area: {e}")
            
                if text_area:
                    try:
                        text_area.click()
                        time.sleep(0.3)
                        text_area.send_keys(Keys.COMMAND + "a")
                        text_area.send_keys(Keys.DELETE)
                        time.sleep(0.5)
                    
                        # Insert text and trigger Angular change detection properly
                        if len(tts_text) > 0:
                            try:
                                # Method: Use native setter + Angular-aware event dispatching
                                # Angular uses a patched addEventListener via Zone.js, so we need to
                                # set the value via the native input setter and dispatch proper events
                                driver.execute_script("""
                                    var el = arguments[0];
                                    var text = arguments[1];
                                    el.focus();
                                    
                                    // Use the native HTMLTextAreaElement setter to bypass Angular's getter
                                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                        window.HTMLTextAreaElement.prototype, 'value'
                                    ).set;
                                    nativeInputValueSetter.call(el, text);
                                    
                                    // Dispatch events that Angular/Zone.js actually listens to
                                    el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                                    el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ' ' }));
                                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ' ' }));
                                    
                                    // Also try execCommand as secondary trigger
                                    try {
                                        el.select();
                                        document.execCommand('insertText', false, text);
                                    } catch(e) {}
                                """, text_area, tts_text)
                                print("✅ Text inserted via native setter + Angular events.")
                                time.sleep(1)
                                
                                # Verify the button became enabled
                                btn_state = driver.execute_script("""
                                    var btn = document.querySelector('button.ctrl-enter-submits');
                                    return btn ? btn.getAttribute('aria-disabled') : 'not-found';
                                """)
                                print(f"   🔍 Run button aria-disabled = {btn_state}")
                                
                                if btn_state == 'true':
                                    # Angular still didn't detect it - try send_keys with a small chunk
                                    print("   ⚠️ Button still disabled, trying send_keys to trigger Angular...")
                                    text_area.click()
                                    time.sleep(0.3)
                                    # Clear and re-type via send_keys (slower but Angular-safe)
                                    text_area.send_keys(Keys.COMMAND + "a")
                                    text_area.send_keys(Keys.DELETE)
                                    time.sleep(0.3)
                                    # Type first 20 chars via keys to wake up Angular, then paste the rest
                                    text_area.send_keys(tts_text[:20])
                                    time.sleep(0.5)
                                    text_area.send_keys(Keys.COMMAND + "a")
                                    time.sleep(0.2)
                                    # Now use execCommand for the full text
                                    driver.execute_script("""
                                        arguments[0].focus();
                                        arguments[0].select();
                                        document.execCommand('insertText', false, arguments[1]);
                                    """, text_area, tts_text)
                                    time.sleep(1)
                                    btn_state2 = driver.execute_script("""
                                        var btn = document.querySelector('button.ctrl-enter-submits');
                                        return btn ? btn.getAttribute('aria-disabled') : 'not-found';
                                    """)
                                    print(f"   🔍 Run button aria-disabled after send_keys = {btn_state2}")
                                    
                            except Exception as js_err:
                                print(f"⚠️ Text input via JS failed: {js_err}, falling back to send_keys.")
                                text_area.send_keys(tts_text)
                                print("✅ Text inserted via send_keys.")
                        else:
                            print("⚠️ No text to insert!")
                    except Exception as e:
                        print(f"⚠️ Text input failed: {e}")
                        try:
                            driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", text_area, tts_text)
                            print("✅ Text inserted via JS fallback.")
                        except Exception as e2:
                            print(f"❌ JS fallback also failed: {e2}")
                else:
                    print("❌ Could not find any text input area!")

                # Remember old audio state to detect new generation
                old_audio_src = None
                try:
                    for audio_sel in ["audio", "div.speech-prompt-footer-actions-player audio", "ms-speech-prompt audio"]:
                        audios = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                        if audios:
                            old_audio_src = audios[-1].get_attribute("src")
                            break
                except:
                    pass

                # 6.5 Select API key if missing (new AI Studio requirement causes 403)
                try:
                    time.sleep(2)
                    key_btns = driver.find_elements(By.XPATH, "//button[@aria-label='No API key selected']")
                    if key_btns:
                        print("⚠️ 'No API key selected' detected. Attempting to select one...")
                        driver.execute_script("arguments[0].click();", key_btns[0])
                        time.sleep(3)
                        keys = driver.find_elements(By.XPATH, "//*[contains(text(), 'nemrosszhirek') or contains(text(), 'gen-lang-client')]")
                        if keys:
                            driver.execute_script("arguments[0].click();", keys[0])
                            print("✅ API key selected successfully.")
                            time.sleep(3)
                            driver.execute_script("""
                                document.querySelectorAll('button, a, [role="button"], span').forEach(btn => {
                                    var text = btn.textContent.toLowerCase().trim();
                                    if((text === 'close' || text === 'dismiss' || text === 'continue') && btn.offsetParent !== null) {
                                        btn.click();
                                        if(btn.parentElement) btn.parentElement.click();
                                    }
                                });
                                document.querySelectorAll('.cdk-overlay-pane, .cdk-overlay-container, .mat-mdc-dialog-container, .cdk-overlay-backdrop, .cdk-overlay-backdrop-showing').forEach(el => {
                                    el.style.display = 'none';
                                });
                            """)
                            time.sleep(2)
                        else:
                            print("⚠️ Opened API key menu but could not find a valid key. Trying to dismiss it.")
                            driver.execute_script("document.body.click();")
                            time.sleep(1)
                except Exception as e:
                    print(f"⚠️ API key selection failed: {e}")

                # 7. Run - Use Selenium native click (Angular ignores synthetic JS clicks)
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    
                    # Wait a moment for any overlays to clear
                    time.sleep(1)
                    
                    # Find the Run button - try multiple strategies
                    run_btn = None
                    
                    # Strategy 1: Find by the unique class
                    run_btns = driver.find_elements(By.CSS_SELECTOR, "button.ctrl-enter-submits")
                    if run_btns:
                        run_btn = run_btns[0]
                        print("🎯 Found Run button via .ctrl-enter-submits class")
                    
                    # Strategy 2: Find by run-button-label span, then get parent button
                    if not run_btn:
                        labels = driver.find_elements(By.CSS_SELECTOR, "span.run-button-label")
                        if labels:
                            run_btn = driver.execute_script("return arguments[0].closest('button');", labels[0])
                            print("🎯 Found Run button via span.run-button-label parent")
                    
                    # Strategy 3: Find by type=submit
                    if not run_btn:
                        submits = driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")
                        if submits:
                            run_btn = submits[0]
                            print("🎯 Found Run button via type=submit")
                    
                    if run_btn:
                        # Scroll into view
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", run_btn)
                        time.sleep(0.3)
                        
                        # Force enable if disabled
                        driver.execute_script("""
                            arguments[0].removeAttribute('aria-disabled');
                            arguments[0].removeAttribute('disabled');
                            arguments[0].disabled = false;
                        """, run_btn)
                        time.sleep(0.2)
                        
                        # Try clicking and verify it switched to "Stop" (= generation started)
                        generation_started = False
                        for attempt in range(4):
                            if attempt == 0:
                                # Attempt 1: Selenium ActionChains native click
                                ActionChains(driver).move_to_element(run_btn).pause(0.3).click().perform()
                                print(f"   🖱️ Attempt {attempt+1}: Selenium ActionChains click")
                            elif attempt == 1:
                                # Attempt 2: Direct Selenium .click()
                                run_btn.click()
                                print(f"   🖱️ Attempt {attempt+1}: Direct Selenium .click()")
                            elif attempt == 2:
                                # Attempt 3: JS click + MouseEvent dispatch
                                driver.execute_script("""
                                    arguments[0].click();
                                    arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                                """, run_btn)
                                print(f"   🖱️ Attempt {attempt+1}: JS click + MouseEvent")
                            else:
                                # Attempt 4: Keyboard shortcut Cmd+Enter
                                text_area.click()
                                time.sleep(0.2)
                                ActionChains(driver).key_down(Keys.COMMAND).send_keys(Keys.ENTER).key_up(Keys.COMMAND).perform()
                                print(f"   🖱️ Attempt {attempt+1}: Cmd+Enter keyboard shortcut")
                            
                            # Wait and check if the button text changed to "Stop"
                            time.sleep(2)
                            btn_text = driver.execute_script("""
                                var btn = document.querySelector('button.ctrl-enter-submits');
                                if (!btn) return 'not-found';
                                return btn.textContent.trim().toLowerCase();
                            """)
                            print(f"   🔍 Button text after click: '{btn_text}'")
                            
                            if btn_text and 'stop' in btn_text:
                                generation_started = True
                                print("▶️ ✅ Generation confirmed started! (button shows 'Stop')")
                                break
                            else:
                                print(f"   ⚠️ Button still shows '{btn_text}', retrying...")
                                time.sleep(1)
                        
                        if not generation_started:
                            print("❌ Generation did NOT start after 4 attempts! Button never changed to Stop.")
                    else:
                        print("❌ Could not find Run button at all!")
                        
                except Exception as e:
                    print(f"❌ Could not trigger Run: {e}")
            
                # 8. Wait for generation to complete — Run button reappears when done
                print(f"⏳ Waiting for {part_name} audio generation (up to 10 mins)...")

                try:
                    audio_src = None
                    max_wait_time = 600  # 10 minutes
                    start_wait = time.time()

                    # Wait for generation to complete by checking audio element
                    generation_complete = False
                    while time.time() - start_wait < max_wait_time:
                        try:
                            current_audio_src = None
                            for audio_sel in ["audio", "div.speech-prompt-footer-actions-player audio", "ms-speech-prompt audio"]:
                                audio_elements = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                                for ae in audio_elements:
                                    src = ae.get_attribute("src")
                                    # If there's a new src that isn't the old one, and it's valid data/blob
                                    if src and len(src) > 15 and src != old_audio_src:
                                        current_audio_src = src
                                        break
                                if current_audio_src:
                                    break
                                    
                            if current_audio_src:
                                elapsed = int(time.time() - start_wait)
                                print(f"✅ Generation complete! New audio src detected ({elapsed}s)")
                                generation_complete = True
                                audio_src = current_audio_src
                                break

                            # Progress indicator
                            elapsed = int(time.time() - start_wait)
                            if elapsed > 0 and elapsed % 15 == 0:
                                print(f"   ⏳ {elapsed}s elapsed, generating...")
                        except Exception:
                            pass

                        # Check for errors
                        try:
                            errors = driver.find_elements(By.XPATH, "//*[contains(text(), 'Error') or contains(text(), 'quota') or contains(text(), 'failed')]")
                            for err in errors:
                                if err.is_displayed():
                                    err_text = err.text.strip()
                                    if err_text and len(err_text) > 3:
                                        print(f"⚠️ Possible UI Error detected: {err_text}")
                        except:
                            pass

                        time.sleep(2.0)

                    if not generation_complete:
                        print(f"⚠️ Timed out waiting for generation to complete, attempting extraction anyway...")

                    # Phase 2: Small delay to let audio element finalize
                    time.sleep(3)

                    # Phase 3: Extract audio src
                    for audio_sel in ["audio", "div.speech-prompt-footer-actions-player audio", "ms-speech-prompt audio"]:
                        audio_elements = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                        for ae in audio_elements:
                            src = ae.get_attribute("src")
                            if src and len(src) > 15 and src != old_audio_src:
                                audio_src = src
                                break
                        if audio_src:
                            break

                    if not audio_src:
                        raise Exception(f"No audio element src found after generation completed for {part_name}.")

                    print("✅ Audio generated! Extracting...")
                
                    def save_audio_data_with_suffix(raw_bytes, suffix):
                        import subprocess
                        temp_wav = os.path.join(OUTPUT_DIR, f"temp_{suffix}_{int(time.time())}.wav")
                        with open(temp_wav, "wb") as f:
                            f.write(raw_bytes)
                    
                        mp3_filename = f"tts_{suffix}.mp3"
                        mp3_filepath = os.path.join(OUTPUT_DIR, mp3_filename)
                    
                        try:
                            print(f"🔄 Converting to MP3 using ffmpeg...")
                            # Determine ffmpeg path, default to absolute path
                            ffmpeg_path = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
                            subprocess.run(
                                [ffmpeg_path, "-i", temp_wav, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_filepath, "-y"],
                                check=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            print(f"🎉 Successfully converted and saved: {mp3_filepath}")
                            os.remove(temp_wav)
                        except Exception as e:
                            print(f"⚠️ FFMPEG conversion failed: {e}")
                            print(f"Saving as WAV with mp3 extension as fallback.")
                            if os.path.exists(mp3_filepath):
                                os.remove(mp3_filepath)
                            os.rename(temp_wav, mp3_filepath)

                    if audio_src and audio_src.startswith("data:audio"):
                        import base64
                        header, encoded = audio_src.split(",", 1)
                        data = base64.b64decode(encoded)
                        save_audio_data_with_suffix(data, suffix)
                    
                    elif audio_src and audio_src.startswith("blob:"):
                        js_fetch = """
                        var uri = arguments[0];
                        var callback = arguments[1];
                        fetch(uri).then(res => res.blob()).then(blob => {
                            var reader = new FileReader();
                            reader.onloadend = function() {
                                callback(reader.result);
                            }
                            reader.readAsDataURL(blob);
                        }).catch(err => callback("ERROR: " + err));
                        """
                        driver.set_script_timeout(120)
                        print(f"ℹ️ Audio source is a blob. Extracting {part_name} via JS...")
                        b64_data_str = driver.execute_async_script(js_fetch, audio_src)
                    
                        if b64_data_str and not b64_data_str.startswith("ERROR"):
                            import base64
                            header, encoded = b64_data_str.split(",", 1)
                            data = base64.b64decode(encoded)
                            save_audio_data_with_suffix(data, suffix)
                        else:
                            print(f"❌ Failed to fetch blob: {b64_data_str}")
                    else:
                        print("❌ Audio element found but no valid src (neither data: nor blob:)? src=" + str(audio_src))

                except Exception as e:
                    print(f"❌ Failed to extract audio: {e}")
                
                print(f"👍 Finished processing {part_name}. Going to the next part...")
                time.sleep(2)


            except Exception as part_err:
                print(f"\n❌ PART FAILED: {part_name} — {part_err}")
                print(f"⏭️ Skipping to next part...")
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass
    except Exception as e:
        print(f"❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()

    print("✅ Done!")

    # --- BULLETPROOF CLEANUP ---
    _rescue_and_push()


def _rescue_and_push():
    """Auto-rescue WAV files from Downloads, convert leftover WAVs to MP3, push to git."""
    import subprocess as sp
    
    parts_suffixes = ["idojaras", "piacok", "hirek", "hircsarda"]
    downloads_dir = os.path.expanduser("~/Downloads")
    
    print("\n🔍 Checking for missing MP3s and rescuable WAV files...")
    for suffix in parts_suffixes:
        mp3_path = os.path.join(OUTPUT_DIR, f"tts_{suffix}.mp3")
        if os.path.exists(mp3_path):
            continue
        wav_candidates = glob.glob(os.path.join(OUTPUT_DIR, "*.wav"))
        wav_candidates += glob.glob(os.path.join(downloads_dir, "Generated Audio*.wav"))
        wav_candidates += glob.glob(os.path.join(downloads_dir, "*.wav"))
        recent_wavs = [wf for wf in wav_candidates 
                       if os.path.getmtime(wf) > time.time() - 1800 and os.path.getsize(wf) > 10000]
        if recent_wavs:
            recent_wavs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            wav_file = recent_wavs[0]
            print(f"  🆘 Found rescue WAV for tts_{suffix}: {os.path.basename(wav_file)}")
            try:
                ffmpeg_path = "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "ffmpeg"
                sp.run([ffmpeg_path, "-i", wav_file, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path, "-y"],
                       check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
                print(f"  ✅ Converted: {mp3_path}")
            except Exception as e:
                print(f"  ❌ Conversion failed: {e}")

    # Auto-push MP3s to git
    mp3_files = glob.glob(os.path.join(OUTPUT_DIR, "tts_*.mp3"))
    if mp3_files:
        print(f"\n📤 Auto-pushing {len(mp3_files)} TTS MP3 file(s) to git...")
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            sp.run(["git", "add", "-f"] + mp3_files, cwd=base_dir, check=True, capture_output=True)
            txt_files = glob.glob(os.path.join(OUTPUT_DIR, "tts_*.txt"))
            if txt_files:
                sp.run(["git", "add"] + txt_files, cwd=base_dir, check=False, capture_output=True)
            sp.run(["git", "commit", "-m", f"Auto-update: TTS audio {current_date}"],
                   cwd=base_dir, check=True, capture_output=True)
            sp.run(["git", "pull", "--rebase", "--autostash"], cwd=base_dir, check=False, capture_output=True)
            gh_token = os.environ.get('GITHUB_TOKEN')
            if gh_token:
                push_url = f"https://oauth2:{gh_token}@github.com/ViktorPosa/hirek.git"
                sp.run(["git", "-c", "credential.helper=", "push", push_url, "HEAD:main"],
                       cwd=base_dir, check=False, capture_output=True)
            else:
                sp.run(["git", "push"], cwd=base_dir, check=False)
            print("✅ TTS MP3s pushed to git!")
        except Exception as e:
            print(f"⚠️ Git push: {e}")

    print("\n📊 TTS Summary:")
    for suffix in parts_suffixes:
        mp3_path = os.path.join(OUTPUT_DIR, f"tts_{suffix}.mp3")
        if os.path.exists(mp3_path):
            size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
            print(f"  ✅ tts_{suffix}.mp3 ({size_mb:.1f} MB)")
        else:
            print(f"  ❌ tts_{suffix}.mp3 MISSING")


if __name__ == "__main__":
    main()
