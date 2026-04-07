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
            if not os.path.exists(input_file):
                print(f"ℹ️ Skipping {part_name}, file not found: {input_file}")
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
            
            # 1. Mode: Single-speaker audio
            try:
                mode_btn = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Single-speaker audio')]"))
                )
                mode_btn.click()
                print("✅ Mode set to: Single-speaker audio")
            except:
                 print("⚠️ Could not find/click Single-speaker audio button.")
                 if "accounts.google.com" in driver.current_url:
                     print("🛑 Hit Login Wall!")
                     time.sleep(60)
            
            time.sleep(2)

            # 2. Style instructions
            try:
                # Making this optional because Google Cloud TTS UI updates frequently 
                # and this field might disappear or be renamed.
                wait_and_send_keys(driver, By.CSS_SELECTOR, 'textarea[aria-label="Style instructions"]', STYLE_INSTRUCTIONS, name="Style Instructions", timeout=5)
            except Exception as e:
                print(f"⚠️ Style instructions field not found or timed out, skipping. Error: {e}")
            
            # 3. Voice
            wait_and_click(driver, By.CSS_SELECTOR, 'mat-select[role="combobox"]', name="Voice Dropdown")
            time.sleep(1)
            wait_and_click(driver, By.XPATH, f"//mat-option[contains(., '{VOICE_NAME}')]", name=f"Voice Option: {VOICE_NAME}")
            
            # 4. Temperature
            set_slider(driver, TEMPERATURE)
            
            # 5. Text Input
            text_area = WebDriverWait(driver, 10).until(
                 EC.presence_of_element_located((By.XPATH, "//textarea[contains(@placeholder, 'Start writing')]"))
            )
            try:
                text_area.send_keys(Keys.COMMAND + "a")
                text_area.send_keys(Keys.DELETE)
                time.sleep(0.5)
                text_area.send_keys(tts_text)
                print("✅ Text inserted.")
            except Exception as e:
                driver.execute_script("arguments[0].value = arguments[1];", text_area, tts_text)
                driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", text_area) 
                print("✅ Text inserted via JS fallback.")

            old_audio_src = None
            try:
                audios = driver.find_elements(By.CSS_SELECTOR, "div.speech-prompt-footer-actions-player audio")
                if audios:
                    old_audio_src = audios[0].get_attribute("src")
            except:
                pass

            # 6. Run
            run_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Run') or contains(., 'Generate')]"))
            )
            run_btn.click()
            print("▶️ Generation started (Run clicked)...")
            
            # 7. Wait and extract
            print(f"⏳ Waiting for {part_name} audio generation (up to 30 mins)...")
            
            try:
                audio_src = None
                max_wait_time = 1800
                start_wait = time.time()
                
                while time.time() - start_wait < max_wait_time:
                    try:
                        audio_elements = driver.find_elements(By.CSS_SELECTOR, "div.speech-prompt-footer-actions-player audio")
                        if audio_elements:
                            src = audio_elements[0].get_attribute("src")
                            if src and len(src) > 15 and src != old_audio_src:
                                audio_src = src
                                break
                    except Exception:
                        pass
                    
                    try:
                        errors = driver.find_elements(By.XPATH, "//*[contains(text(), 'Error') or contains(text(), 'quota')]")
                        for err in errors:
                            if err.is_displayed():
                                print(f"⚠️ Possible UI Error detected in {part_name}: {err.text}")
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
