"""
LEÍRÁS:
Automatikus süti kinyerő szkript a Gemini Pro-hoz (Selenium alapú).
Elindít egy Chrome böngészőt (ideiglenes profillal), megvárja a felhasználói bejelentkezést,
majd kiexportála a szükséges sütiket (__Secure-1PSID, __Secure-1PSIDTS) JSON formátumba.

BEMENET:
- Felhasználói bejelentkezés (interaktív)

KIMENET:
- Input/gemini_cookies.json
"""


import json
import os
import sys
import time
import tempfile

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def extract_cookies():
    """Extract Gemini cookies using Selenium with a temporary profile."""
    print("=" * 50)
    print("Automatic Gemini Cookie Extractor")
    print("=" * 50)
    print()
    
    # Check if selenium is installed
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        print("Installing dependencies...")
        os.system("pip install selenium webdriver-manager")
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
    
    # Setup Chrome options with a FIXED profile in project dir (avoids conflict with main Chrome)
    chrome_options = Options()
    # Use a fixed profile directory in the project folder
    profile_dir = os.path.join(os.path.dirname(__file__), 'ChromeSeleniumProfile')
    os.makedirs(profile_dir, exist_ok=True)
    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    
    # Hide Selenium detection from Google
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # Keep browser open
    chrome_options.add_experimental_option("detach", True)
    
    print("Starting Chrome (temporary profile)...")
    print()
    
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f"Failed to start Chrome: {e}")
        return False
    
    try:
        # Navigate to Gemini
        print("Opening gemini.google.com...")
        driver.get("https://gemini.google.com/app")
        
        print()
        print("=" * 50)
        print("LOG IN TO YOUR GOOGLE ACCOUNT IN THE BROWSER")
        print("=" * 50)
        print()
        print("Once you see the Gemini chat interface, the script will automatically detect cookies.")
        print("Waiting for login (max 5 minutes)...")
        print()
        
        # Poll for cookies loop
        start_time = time.time()
        max_wait = 300  # 5 minutes
        psid = None
        psidts = None
        
        while time.time() - start_time < max_wait:
            cookies = driver.get_cookies()
            
            psid = None
            psidts = None
            
            for cookie in cookies:
                if cookie['name'] == '__Secure-1PSID':
                    psid = cookie['value']
                elif cookie['name'] == '__Secure-1PSIDTS':
                    psidts = cookie['value']
            
            if psid and psidts:
                print("✅ Required cookies detected!")
                break
            
            time.sleep(2)
        
        driver.quit()
        
        if not psid or not psidts:
            print("❌ Timeout: Required cookies not found after 5 minutes.")
            print("   Make sure you logged in correctly.")
            return False
        
        # Save cookies
        gemini_cookies = [
            {"name": "__Secure-1PSID", "value": psid},
            {"name": "__Secure-1PSIDTS", "value": psidts}
        ]
        
        cookie_path = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_cookies.json')
        
        with open(cookie_path, 'w', encoding='utf-8') as f:
            json.dump(gemini_cookies, f, indent=4, ensure_ascii=False)
        
        print()
        print("Cookies extracted successfully!")
        print(f"Saved to: {cookie_path}")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        try:
            driver.quit()
        except:
            pass
        return False

if __name__ == "__main__":
    success = extract_cookies()
    print()
    if success:
        print("You can now run: py run_pipeline.py --geminipro-link --geminipro-summarize")
    input("\nPress Enter to exit...")
