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
from chromedriver_updater import get_chromedriver_path

import datetime

# --- Configuration ---
PROFILE_PATH = "/Users/viktorposa/Library/Application Support/Google/Chrome"

# Determine dynamic paths based on today's date
current_date = datetime.date.today().strftime("%Y-%m-%d")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "Output", current_date)

TARGET_URL = "https://aistudio.google.com/generate-speech?model=gemini-2.5-pro-preview-tts"

# Settings
STYLE_INSTRUCTIONS = "Kedves, meleg, baráti stílusban olvasd fel tökéletes magyarsággal, ügyelve, hogy ne angolosan legyenek kiejtve a betűk, főleg a magyar betűk"
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
    chrome_options.add_experimental_option("detach", True)  # Keep Chrome open after script exits

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
        caffeinate_proc = subprocess.Popen(["caffeinate", "-u", "-d", "-i"])
        print("☕ Caffeinate activated: system will not sleep until the script finishes.")
        
        def stop_caffeinate():
            if caffeinate_proc:
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

    driver, temp_profile_path = setup_driver()
    
    try:
        parts = [
            ("Part 1: Időjárás és Piac", "tts_weather_market.txt", "idojaras"),
            ("Part 2: Hírcímek", "tts_headlines.txt", "hirek")
        ]
        
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
            
            if idx > 0:
                print(f"🆕 Opening a new tab for {part_name} so the previous audio can keep playing in the background...")
                driver.execute_script("window.open('');")
                driver.switch_to.window(driver.window_handles[-1])
            
            driver.get(TARGET_URL)
            print("🌍 Navigation started, page reloading for fresh generation...")
            
            # 1. Dismiss ALL overlays and popups (CDK overlays block clicks!)
            time.sleep(5)  # Let page fully settle including overlays
            try:
                # First: dismiss the CDK overlay that blocks everything
                driver.execute_script("""
                    // Remove all CDK overlays that block clicks
                    document.querySelectorAll('.cdk-overlay-pane, .cdk-overlay-container').forEach(el => {
                        el.style.display = 'none';
                    });
                    // Also try clicking Dismiss buttons
                    document.querySelectorAll('button').forEach(btn => {
                        if (btn.textContent.includes('Dismiss') && btn.offsetParent !== null) {
                            btn.click();
                        }
                    });
                """)
                print("✅ Cleared overlay popups via JS")
                time.sleep(1)
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
                    
                    # For long texts, use JS clipboard to avoid slow typing
                    if len(tts_text) > 500:
                        driver.execute_script("""
                            var el = arguments[0];
                            var text = arguments[1];
                            if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                                el.value = text;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            } else {
                                el.innerText = text;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                            }
                        """, text_area, tts_text)
                        print("✅ Text inserted via JS (fast mode for long text).")
                    else:
                        text_area.send_keys(tts_text)
                        print("✅ Text inserted.")
                except Exception as e:
                    print(f"⚠️ Text input keystroke failed, trying JS: {e}")
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
                # New UI: audio element may be inside different containers
                for audio_sel in ["audio", "div.speech-prompt-footer-actions-player audio", "ms-speech-prompt audio"]:
                    audios = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                    if audios:
                        old_audio_src = audios[0].get_attribute("src")
                        break
            except:
                pass

            # 7. Run
            try:
                # New UI: Run button at bottom right
                run_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Run')]"))
                )
                run_btn.click()
                print("▶️ Generation started (Run clicked)...")
            except:
                # Fallback: try Cmd+Enter
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(driver).key_down(Keys.COMMAND).send_keys(Keys.ENTER).key_up(Keys.COMMAND).perform()
                    print("▶️ Generation started (Cmd+Enter)...")
                except Exception as e:
                    print(f"❌ Could not trigger Run: {e}")
            
            # 8. Wait and extract audio
            print(f"⏳ Waiting for {part_name} audio generation (up to 30 mins)...")
            
            try:
                audio_src = None
                max_wait_time = 1800
                start_wait = time.time()
                
                while time.time() - start_wait < max_wait_time:
                    try:
                        # Search for audio elements in multiple possible locations
                        for audio_sel in ["audio", "div.speech-prompt-footer-actions-player audio", "ms-speech-prompt audio"]:
                            audio_elements = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                            if audio_elements:
                                src = audio_elements[0].get_attribute("src")
                                if src and len(src) > 15 and src != old_audio_src:
                                    audio_src = src
                                    break
                        if audio_src:
                            break
                    except Exception:
                        pass
                    
                    # Also check for download button as indicator that audio is ready
                    try:
                        dl_btns = driver.find_elements(By.XPATH, "//button[@aria-label='Download']")
                        if dl_btns and dl_btns[0].is_displayed() and dl_btns[0].is_enabled():
                            # Audio might be ready even if we can't get src
                            if not audio_src:
                                # Try to get src again
                                for audio_sel in ["audio"]:
                                    audio_elements = driver.find_elements(By.CSS_SELECTOR, audio_sel)
                                    for ae in audio_elements:
                                        src = ae.get_attribute("src")
                                        if src and len(src) > 15:
                                            audio_src = src
                                            break
                                if audio_src:
                                    break
                    except:
                        pass
                    
                    try:
                        errors = driver.find_elements(By.XPATH, "//*[contains(text(), 'Error') or contains(text(), 'quota') or contains(text(), 'failed')]")
                        for err in errors:
                            if err.is_displayed():
                                err_text = err.text.strip()
                                if err_text and len(err_text) > 3:
                                    print(f"⚠️ Possible UI Error detected in {part_name}: {err_text}")
                    except:
                        pass
                        
                    time.sleep(1.0)
                    
                if not audio_src:
                    raise Exception(f"Timed out waiting for new audio element src to populate in {part_name}.")

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
                        subprocess.run(
                            ["ffmpeg", "-i", temp_wav, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_filepath, "-y"],
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

    except Exception as e:
        print(f"❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        print("✅ Done! Chrome marad nyitva.")
        # driver.quit() - NE zárjuk be, maradjon nyitva a user kérésére
        # Clean up temp profile?
        # shutil.rmtree(temp_profile_path)

if __name__ == "__main__":
    main()
