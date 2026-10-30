import os
import glob

def patch_file(filepath):
    try:
        with open(filepath, "r") as f:
            content = f.read()

        if "local_state.unlink()" in content:
            return

        # Insert after cleanup_zombie_chrome_processes()
        replacement = """    cleanup_zombie_chrome_processes()
    
    # FIX: Prevent 'cannot parse internal JSON template' by removing transient Local State
    try:
        local_state_path = os.path.join(str(GEMINI_PROFILE_DIR), "Local State")
        if os.path.exists(local_state_path):
            os.remove(local_state_path)
    except:
        pass
"""
        new_content = content.replace("    cleanup_zombie_chrome_processes()", replacement)
        
        # Add import os if not there
        if "import os" not in new_content:
            new_content = "import os\n" + new_content

        if content != new_content:
            with open(filepath, "w") as f:
                f.write(new_content)
            print(f"Patched {filepath}")
    except Exception as e:
        print(f"Failed {filepath}: {e}")

patch_file("gemini_selenium_client.py")
patch_file("gemini_tts_selenium.py")
