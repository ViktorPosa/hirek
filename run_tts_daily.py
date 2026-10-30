#!/usr/bin/env python3
"""
TTS Daily Runner
================
Lefuttatja az időjárás, piacok, TTS szöveg és hanggenerálást.
Időzítéshez használd a mellékelt .plist fájlt.

Futtatás manuálisan:
    python3 run_tts_daily.py
"""

import subprocess
import sys
import os
import datetime
import time
import json
import glob
import vpn_control

# Change to script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

def run_script(script_name, description, extra_args=None):
    """Runs a Python script and returns success status."""
    print(f"\n{'='*50}")
    print(f"STEP: {description}")
    print(f"{'='*50}\n")
    
    start_time = time.time()
    try:
        cmd = [sys.executable, script_name]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(cmd, check=True)
        elapsed = time.time() - start_time
        print(f"✅ {script_name} completed in {elapsed:.1f}s")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {script_name} failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


def _load_custom_tts_items_for_sync():
    """Load custom_tts_items from Input/pipeline_settings.json for sync_mp3s name_map."""
    try:
        path = os.path.join(SCRIPT_DIR, "Input", "pipeline_settings.json")
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        items = s.get("pipeline", {}).get("custom_tts_items", [])
        return items if isinstance(items, list) else []
    except Exception:
        return []


def sync_mp3s(today, daily_output_dir):
    """Copy any MP3s from Output/today to ImageRepo/today and push to git."""
    import shutil
    repo_daily_dir = os.path.join(SCRIPT_DIR, 'ImageRepo', today)
    os.makedirs(repo_daily_dir, exist_ok=True)

    mp3_files = glob.glob(os.path.join(daily_output_dir, '*.mp3'))
    if not mp3_files:
        print("⚠️ No MP3s found to sync.")
        return

    base_raw_url = f"https://raw.githubusercontent.com/Derushir/Kepek/main/{today}"
    name_map = {
        "tts_idojaras.mp3":  ("Időjárás", "Mai időjárás-jelentés"),
        "tts_piacok.mp3":    ("Piacok és gazdaság", "Piaci összefoglaló és üzleti hírek"),
        "tts_headlines.mp3": ("Rövid hírek", "Tegnapról és ma reggelről"),
        "tts_hircsarda.mp3": ("Szatirikus hírek", "Válogatás a Hírcsárdáról"),
    }
    for _ci in _load_custom_tts_items_for_sync():
        _cid = _ci.get("id", "")
        if _cid:
            name_map[f"tts_{_cid}.mp3"] = (_ci.get("name", _cid), "")
    tts_data = []
    for mp3_src in mp3_files:
        basename = os.path.basename(mp3_src)
        mp3_dest = os.path.join(repo_daily_dir, basename)
        shutil.copy2(mp3_src, mp3_dest)
        print(f"📸 Copied {basename} to ImageRepo/{today}")
        if basename in name_map:
            title, subtitle = name_map[basename]
            tts_data.append({"title": title, "subtitle": subtitle,
                             "url": f"{base_raw_url}/{basename}"})

    # Always rewrite tts_data.json with all available MP3s
    order = list(name_map.keys())
    tts_data.sort(key=lambda x: order.index(
        next((k for k in order if name_map[k][0] == x["title"]), order[-1])))
    json_path = os.path.join(repo_daily_dir, "tts_data.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(tts_data, f, ensure_ascii=False, indent=2)
    print(f"📄 Updated {json_path}")

    try:
        subprocess.run([sys.executable, "git_sync_images.py"], cwd=SCRIPT_DIR, check=True)
    except Exception as e:
        print(f"⚠️ Failed to sync to ImageRepo git: {e}")


def main():
    # --sync-only: just copy today's MP3s to ImageRepo and push (no generation)
    if "--sync-only" in sys.argv:
        today = datetime.date.today().strftime('%Y-%m-%d')
        daily_output_dir = os.path.join(SCRIPT_DIR, 'Output', today)
        print(f"🔄 Sync-only mode: {today}")
        sync_mp3s(today, daily_output_dir)
        return

    today = datetime.date.today().strftime('%Y-%m-%d')
    print(f"🎙️ TTS Daily Runner - {today}")
    print(f"Working directory: {SCRIPT_DIR}")
    
    # Set up daily output directory
    daily_output_dir = os.path.join(SCRIPT_DIR, 'Output', today)
    os.makedirs(daily_output_dir, exist_ok=True)
    os.environ['DAILY_OUTPUT_DIR'] = daily_output_dir
    
    # Step 0: Clean up yesterday's MP3s
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 0.1 Clean from Output/
    yesterday_dir_out = os.path.join(SCRIPT_DIR, 'Output', yesterday)
    if os.path.exists(yesterday_dir_out):
        print(f"🧹 Clearing old MP3 files from yesterday Output: {yesterday_dir_out}")
        for f in glob.glob(os.path.join(yesterday_dir_out, '*.mp3')):
            try:
                os.remove(f)
                print(f"  - Deleted {os.path.basename(f)}")
            except Exception as e:
                print(f"  - Failed to delete {os.path.basename(f)}: {e}")
                
    # 0.2 Clean from ImageRepo/
    yesterday_dir_repo = os.path.join(SCRIPT_DIR, 'ImageRepo', yesterday)
    if os.path.exists(yesterday_dir_repo):
        print(f"🧹 Clearing old MP3 files from yesterday ImageRepo: {yesterday_dir_repo}")
        for f in glob.glob(os.path.join(yesterday_dir_repo, '*.mp3')):
            try:
                os.remove(f)
                print(f"  - Deleted {os.path.basename(f)} from git repo")
                # Unstage if previously committed
                subprocess.run(['git', 'rm', '--cached', f], cwd=os.path.join(SCRIPT_DIR, 'ImageRepo'), capture_output=True)
            except Exception as e:
                print(f"  - Failed to delete {os.path.basename(f)}: {e}")
                
    # Load auxiliary backend from pipeline settings
    aux_extra_args = []
    settings_path = os.path.join(SCRIPT_DIR, 'Input', 'pipeline_settings.json')
    aux_backend = None
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
            aux_backend = settings.get('pipeline', {}).get('auxiliary_backend')
            if aux_backend:
                print(f"📋 Loaded auxiliary backend from settings: {aux_backend}")
                backend_flag_map = {
                    'perplexity': '--use-perplexity',
                    'geminipro-cli': '--use-geminipro-cli',
                    'gemini-api': '--use-gemini-api',
                    'free-gemini-api': '--use-free-gemini-api',
                    'gemini-selenium': '--use-gemini-selenium',
                    'gemini': '--use-geminipro',
                    'lmstudio-local': '--use-lmstudio-local',
                    'lmstudio-remote': '--use-lmstudio-remote',
                }
                flag = backend_flag_map.get(aux_backend)
                if flag:
                    aux_extra_args = [flag]
        except Exception as e:
            print(f"⚠️ Could not load pipeline_settings.json: {e}")
    
    # Step 1: Generate Weather (required for TTS)
    success1 = run_script("weather_generator.py", "Generating Weather Data", aux_extra_args or None)
    
    # Step 2: Generate Market (required for TTS)
    success2 = run_script("market_generator.py", "Generating Market Data", aux_extra_args or None)
    
    # Step 3: Generate TTS text (needs weather + market) - Use Gemini Selenium PRO
    success3 = run_script("tts_generator.py", "Generating TTS Script (Text)", ["--gemini-selenium"])
    
    # Step 4: Generate TTS audio via ElevenLabs (all parts)
    if success3:
        txt_files = [
            os.path.join(daily_output_dir, "tts_idojaras.txt"),
            os.path.join(daily_output_dir, "tts_piacok.txt"),
            os.path.join(daily_output_dir, "tts_headlines.txt"),
            os.path.join(daily_output_dir, "tts_hircsarda.txt"),
        ] + [os.path.join(daily_output_dir, f"tts_{_ci['id']}.txt")
             for _ci in _load_custom_tts_items_for_sync() if _ci.get("id")]
        existing_txts = [f for f in txt_files if os.path.exists(f)]
        # Connect NordVPN right BEFORE the ElevenLabs TTS audio step runs.
        print("\n🔌 VPN: connecting NordVPN before ElevenLabs TTS step...")
        vpn_control.connect(wait=True, timeout=90)
        success4 = run_script(
            "elevenlabs_tts.py",
            "Generating TTS Audio (ElevenLabs)",
            existing_txts,
        )
    else:
        print("\n⚠️ Skipping audio generation: TTS text was not created.")
        success4 = False
    
    # Summary & Sync
    print(f"\n{'='*50}")
    all_success = success1 and success2 and success3 and success4
    if all_success:
        print("🎉 TTS generation completed successfully!")
    else:
        print("⚠️ TTS generation finished with errors.")
        if not success1: print("   - Weather generation failed")
        if not success2: print("   - Market generation failed")
        if not success3: print("   - TTS text generation failed")
        if not success4: print("   - TTS audio generation failed")

    # Copy MP3s to ImageRepo and push (even on partial success)
    sync_mp3s(today, daily_output_dir)

    repo_daily_dir = os.path.join(SCRIPT_DIR, 'ImageRepo', today)

    # Always sync to remote (even on partial success — images may be pending)
    print("🔄 Syncing to remote GitHub repositories...")

    # 1. Sync to MAIN repository (TTS text files)
    try:
        print("   -> Syncing to main news repository (TEXT only)...")
        subprocess.run(["git", "add", f"{repo_daily_dir}/*.txt"], cwd=SCRIPT_DIR, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Auto-update: {today} TTS text files"], cwd=SCRIPT_DIR, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=SCRIPT_DIR, capture_output=True)
    except Exception as e:
        print(f"⚠️ Failed to sync main repository: {e}")

    # 2. Always sync ImageRepo (images + MP3s)
    try:
        print("   -> Syncing to Kepek repository...")
        subprocess.run([sys.executable, "git_sync_images.py"], cwd=SCRIPT_DIR, check=True)
    except Exception as e:
        print(f"⚠️ Failed to sync changes to ImageRepo git: {e}")

    print(f"{'='*50}")

if __name__ == "__main__":
    main()
