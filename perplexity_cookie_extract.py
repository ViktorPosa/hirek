"""
LEÍRÁS:
Automatikus süti kinyerő szkript a Perplexity Pro-hoz (Selenium alapú).
Perzisztens Chrome profilt használ, hogy megjegyezze a bejelentkezést.
Kinyeri a hitelesítéshez szükséges sütiket.

BEMENET:
- Felhasználói bejelentkezés

KIMENET:
- Input/perplexity_cookies.json
"""


import json
import os
import sys
import time

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def extract_cookies():
    """Extract Perplexity cookies using Selenium with a temporary profile."""
    print("=" * 50)
    print("Automatic Perplexity Cookie Extractor")
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
    
    # Kill any lingering chrome instances to free up resources
    if sys.platform == "darwin": # macOS
        os.system("pkill -f 'Google Chrome'")
        os.system("pkill -f chromedriver")
        time.sleep(2)

    # Setup Chrome options with a PERSISTENT profile to save logins
    chrome_options = Options()
    
    # Use distinct persistent directory for Perplexity
    profile_dir = os.path.join(os.path.dirname(__file__), 'PerplexityChromeProfile')
    os.makedirs(profile_dir, exist_ok=True)
    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    
    # Hide Selenium detection
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # Keep browser open
    chrome_options.add_experimental_option("detach", True)
    
    print("Starting Chrome (fresh profile)...")
    print()
    
    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f"Failed to start Chrome: {e}")
        return False
    
    try:
        # Navigate to Perplexity
        print("Opening perplexity.ai...")
        driver.get("https://perplexity.ai")
        
        print()
        print("=" * 50)
        print("LOG IN TO YOUR PERPLEXITY PRO ACCOUNT IN THE BROWSER")
        print("=" * 50)
        print()
        print("Once you're logged in, the script will automatically detect cookies.")
        print("Waiting for login (max 5 minutes)...")
        print()
        
        # Poll for cookies loop
        start_time = time.time()
        max_wait = 300  # 5 minutes
        
        # Required Perplexity cookies
        found_cookies = {}
        
        while time.time() - start_time < max_wait:
            try:
                cookies = driver.get_cookies()
                
                for cookie in cookies:
                    name = cookie['name']
                    # Collect all relevant Perplexity cookies
                    if 'perplexity' in name.lower() or 'next-auth' in name.lower() or 'session' in name.lower():
                        found_cookies[name] = cookie['value']
                
                # Check for session token specifically (support both standard and secure variants)
                session_token_present = False
                for key in found_cookies.keys():
                    if 'next-auth.session-token' in key:
                        session_token_present = True
                        break
                
                if session_token_present:
                    print("✅ Session token detected!")
                    break
                
                time.sleep(2)
            except Exception as e:
                print(f"Browser closed or error: {e}")
                break
        
        driver.quit()
        
        # Verify we captured a session token
        has_token = any('next-auth.session-token' in k for k in found_cookies.keys())
        
        if not has_token:
            print("❌ Timeout: Session token not found after 5 minutes.")
            print("   Make sure you logged in correctly.")
            return False
        
        # Save cookies
        cookie_path = os.path.join(os.path.dirname(__file__), 'Input', 'perplexity_cookies.json')
        
        with open(cookie_path, 'w', encoding='utf-8') as f:
            json.dump(found_cookies, f, indent=4, ensure_ascii=False)
        
        print()
        print(f"✅ Cookies extracted successfully! ({len(found_cookies)} cookies)")
        print(f"   Saved to: {cookie_path}")
        print(f"   Profile saved to: {profile_dir}")
            
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        try:
            if driver: driver.quit()
        except:
            pass
        return False

if __name__ == "__main__":
    success = extract_cookies()
    print()
    if success:
        print("You can now run: py run_pipeline.py --perplexity-summarize")
    input("\nPress Enter to exit...")
