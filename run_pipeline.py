import subprocess
import sys
import time
import os
import datetime
import argparse
import json
import fcntl
import signal
import vpn_control
from daily_filter_words import ensure_daily_filter_words

# Fix for "malloc: *** error for object ... pointer being freed was not allocated"
# This issue is related to gRPC fork support on macOS
os.environ['GRPC_ENABLE_FORK_SUPPORT'] = '0'
os.environ['GRPC_POLL_STRATEGY'] = 'poll'

# Prevent Git from hanging on authentication prompts
os.environ['GIT_TERMINAL_PROMPT'] = '0'
os.environ['GCM_INTERACTIVE'] = 'never'

# Kept open for the whole run so the single-instance flock stays held (see main()).
_PIPELINE_LOCK_FH = None

"""
LEÍRÁS:
Ez a fő vezérlő szkript (pipeline orchestrator), amely egymás után futtatja a hírfeldolgozó folyamat lépéseit.
Kezeli a parancssori argumentumokat, a hibatűrést, az időzítést és a Git szinkronizációt.

BEMENET:
- Parancssori argumentumok (pl. --skip-filter, --multi-model)
- Konfigurációs fájl: Input/pipeline_settings.json
- API kulcsok: Input/input.txt

KIMENET:
- A teljes feldolgozási folyamat végrehajtása (szűrők, összefoglalók, generátorok futtatása)
- Konzol kimenet a folyamat állapotáról
- Git push (opcionális) a frissített fájlokkal
"""

# Script definitions with their corresponding skip flag name
# (script_file, description, arg_name)
PIPELINE_STEPS = [
    ("rss_creator.py", "Generating Custom RSS Feeds", "rsscreator"),
    ("news_filter.py", "Collecting RSS Links", "filter"),
    ("link_dedup.py", "Deduplicating Links by Topic", "linkdedup"),
    ("summarizer_json.py", "Summarizing to JSON (Filter + Categorize)", "summarize"),
    ("toplist_generator.py", "Generating Toplists from specific URLs", "toplist"),
    ("justwatch_scraper.py", "Scraping JustWatch SkyShowtime Trending", "justwatch"),
    ("youtube_scraper.py", "Scraping YouTube Creators for New Videos", "youtube"),
    ("dedup_fast.py", "Deduplicating News (Multi-file)", "dedup"),
    ("filter_news.py", "Filtering Negative News", "newsfilter"),
    ("image_downloader.py", "Downloading Images", "images"),
    ("tag_generator_json.py", "Generating Tags JSON", "tags"),
    ("section_validator.py", "Validating Section Fields", "validate"),
    ("ajanlott_generator.py", "Generating Recommendations", "ajanlott"),
    ("filter_importance.py", "Splitting Importance 4/5 into Separate Files", "importance"),
    ("randomize_sections.py", "Randomizing Sections", "randomize"),
]




def run_script(script_name, description, extra_args=None):
    print(f"\n{'='*50}")
    print(f"STEP: {description} ({script_name})")
    print(f"{'='*50}\n")
    
    start_time = time.time()
    try:
        # Build command with extra arguments
        cmd = [sys.executable, script_name]
        if extra_args:
            cmd.extend(extra_args)
        
        result = subprocess.run(cmd, check=True)
        
        elapsed_time = time.time() - start_time
        print(f"\n>>> {script_name} completed successfully in {elapsed_time:.2f} seconds.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n!!! ERROR running {script_name}: {e}")
        print("Pipeline stopped due to error.")
        return False
    except Exception as e:
        print(f"\n!!! UNEXPECTED ERROR: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Run the News Processing Pipeline.")
    parser.add_argument("--skip-rsscreator", action="store_true", help="Skip RSS creation (rss_creator.py)")
    parser.add_argument("--skip-filter", action="store_true", help="Skip fetching and filtering (news_filter.py)")
    parser.add_argument("--skip-sort", action="store_true", help="Skip sorting links (sorter.py)")
    parser.add_argument("--skip-linkfilter", action="store_true", help="Skip link pre-filtering (link_filter.py)")
    parser.add_argument("--skip-linkdedup", action="store_true", help="Skip link topic deduplication (link_dedup.py)")
    parser.add_argument("--skip-summarize", action="store_true", help="Skip summarization (summarizer_json.py)")
    parser.add_argument("--skip-toplist", action="store_true", help="Skip generating toplists (toplist_generator.py)")
    parser.add_argument("--skip-justwatch", action="store_true", help="Skip scraping JustWatch (justwatch_scraper.py)")
    parser.add_argument("--skip-youtube", action="store_true", help="Skip YouTube creator scraping (youtube_scraper.py)")
    parser.add_argument("--skip-dedup", action="store_true", help="Skip deduplication (dedup.py)")
    parser.add_argument("--skip-weather", action="store_true", help="Skip weather generation (weather_generator.py)")
    parser.add_argument("--skip-market", action="store_true", help="Skip market analysis (market_generator.py)")
    parser.add_argument("--skip-images", action="store_true", help="Skip image downloading (image_downloader.py)")
    parser.add_argument("--skip-tags", action="store_true", help="Skip tag generation (tag_generator_json.py)")
    parser.add_argument("--skip-ajanlott", action="store_true", help="Skip recommendation generation (ajanlott_generator.py)")
    parser.add_argument("--skip-newsfilter", action="store_true", help="Skip news filtering from JSON (filter_news.py)")
    parser.add_argument("--skip-validate", action="store_true", help="Skip section validation (section_validator.py)")
    parser.add_argument("--skip-importance", action="store_true", help="Skip importance splitting (filter_importance.py)")
    parser.add_argument("--skip-randomize", action="store_true", help="Skip section randomization (randomize_sections.py)")
    parser.add_argument("--skip-tts", action="store_true", help="Skip TTS script generation (tts_generator.py)")
    parser.add_argument("--skip-ttsaudio", action="store_true", help="Skip TTS audio generation (gemini_tts_selenium.py)")
    parser.add_argument("--gemini-link", action="store_true", help="Use Gemini (Cookie Fallback) for link filtering")
    parser.add_argument("--gemini-summarize", action="store_true", help="Use Gemini (Cookie Fallback) for summarization")
    parser.add_argument("--geminiapi-link", action="store_true", help="Use Gemini API Key for link filtering")
    parser.add_argument("--geminiapi-summarize", action="store_true", help="Use Gemini API Key for summarization")
    parser.add_argument("--geminipro-link", action="store_true", help="Use Gemini 3 Pro for link filtering (news_filter.py)")
    parser.add_argument("--geminipro-summarize", action="store_true", help="Use Gemini 3 Pro for summarization (summarizer_json.py)")
    parser.add_argument("--geminipro-cli-summarize", action="store_true", help="Use Gemini Pro CLI for summarization (via gemini-cli)")
    parser.add_argument("--perplexity-summarize", action="store_true", help="Use Perplexity Pro for summarization (summarizer_json.py)")
    parser.add_argument("--g4f-summarize", action="store_true", help="Use GPT4Free for summarization (free providers)")
    parser.add_argument("--gemini-selenium", action="store_true", help="Use Gemini Selenium automation (pozitivhirekP style)")
    parser.add_argument("--deeperseek-summarize", action="store_true", help="Use DeeperSeek for summarization (free DeepSeek)")
    parser.add_argument("--free-gemini-summarize", action="store_true", help="Use Free Gemini API Pool for summarization (with rotation)")
    parser.add_argument("--lmstudio-local-summarize", action="store_true", help="Use LM Studio local endpoint for summarization")
    parser.add_argument("--lmstudio-remote-summarize", action="store_true", help="Use LM Studio remote endpoint for summarization")
    parser.add_argument("--multi-model", action="store_true", help="Use all backends in parallel with auto-failover")
    parser.add_argument("--waitrefresh", action="store_true", help="Wait for rate limit refresh (2h initial, then 1h retries)")
    parser.add_argument("--push", action="store_true", help="Automatically push to git after pipeline completion")
    parser.add_argument("--time", type=str, metavar="HH:MM", help="Start pipeline at specified time (e.g., --time 06:00)")
    parser.add_argument("--stop", action="store_true", help="Gracefully stop the running pipeline")
    parser.add_argument("--gemini-api-key", type=str, help="Manually provide Gemini API Key")
    parser.add_argument("--menu", action="store_true", help="Launch interactive menu for configuration")
    parser.add_argument("--debug-pairing", action="store_true", help="Enable debug pairing mode (stop on failure, save details)")

    
    args = parser.parse_args()

    # --- LOAD DEFAULTS FROM SETTINGS FILE ---
    # This allows "python3 run_pipeline.py" to use the saved menu configuration
    image_upload_enabled = True  # Default: upload images to ImgBB
    settings_path = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')
    if os.path.exists(settings_path) and not args.menu:
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
            
            print("📋 Loaded configuration from Input/pipeline_settings.json")
            
            # 1. Pipeline Steps (Translate 'steps' to --skip flags)
            # If a step is False in settings, we set skip_xyz = True, UNLESS user explicitly set flags (complex, so we just trust settings if no args)
            # We will prioritize settings if CLI arg is not present (which is default False usually)
            saved_steps = settings.get('pipeline', {}).get('steps', {})
            step_mapping = {
                "weather": "skip_weather",
                "market": "skip_market",
                "tts": "skip_tts",
                "rsscreator": "skip_rsscreator",
                "filter": "skip_filter",
                "linkfilter": "skip_linkfilter",
                "linkdedup": "skip_linkdedup",
                "sort": "skip_sort",
                "summarize": "skip_summarize",
                "toplist": "skip_toplist",
                "justwatch": "skip_justwatch",
                "youtube": "skip_youtube",
                "dedup": "skip_dedup",
                "images": "skip_images",
                "tags": "skip_tags",
                "validate": "skip_validate",
                "ajanlott": "skip_ajanlott",
                "newsfilter": "skip_newsfilter",
                "importance": "skip_importance",
                "randomize": "skip_randomize",
                "ttsaudio": "skip_ttsaudio"
            }
            for json_key, arg_key in step_mapping.items():
                if json_key in saved_steps and not saved_steps[json_key]:
                    # Step is disabled in settings -> Enable skip
                    setattr(args, arg_key, True)

            # 1.5 Global Settings
            if settings.get('global', {}).get('debug_pairing', False):
                args.debug_pairing = True

            # 1.6 Image Upload Setting
            image_upload_enabled = settings.get('pipeline', {}).get('image_upload', True)

            # 2. Push Setting
            if settings.get('pipeline', {}).get('push_enabled', False):
                args.push = True

            # 3. Backend Selection
            # Only apply if no specific backend flag is already set by user
            backend_flags = [
                args.multi_model, args.geminipro_cli_summarize, args.geminiapi_summarize,
                args.g4f_summarize, args.deeperseek_summarize, args.perplexity_summarize,
                args.geminipro_summarize, args.gemini_summarize, args.free_gemini_summarize,
                args.lmstudio_local_summarize, args.lmstudio_remote_summarize
            ]
            if not any(backend_flags):
                selected = settings.get('pipeline', {}).get('selected_backend', 'geminipro-cli')
                if selected == 'multi-model':
                    args.multi_model = True
                elif selected == 'geminipro-cli':
                    args.geminipro_cli_summarize = True
                elif selected == 'gemini-api':
                    args.geminiapi_summarize = True
                elif selected == 'g4f':
                    args.g4f_summarize = True
                elif selected == 'deeperseek':
                    args.deeperseek_summarize = True
                elif selected == 'perplexity':
                    args.perplexity_summarize = True
                elif selected == 'free-gemini-api':
                    args.free_gemini_summarize = True
                elif selected == 'lmstudio-local':
                    args.lmstudio_local_summarize = True
                elif selected == 'lmstudio-remote':
                    args.lmstudio_remote_summarize = True
                elif selected == 'geminipro':
                    args.geminipro_summarize = True
                elif selected == 'gemini': # cookie legacy
                    args.gemini_summarize = True
                elif selected.startswith('gemini-api-'): # gemini-api-1, gemini-api-2...
                    key_idx = int(selected.split('-')[-1])
                    setattr(args, f"geminiapi_key_{key_idx}", True)
                
                print(f"   Using saved backend: {selected}")
                
        except Exception as e:
            print(f"⚠️ Error loading settings: {e}")
            
    # ----------------------------------------

    # Launch interactive menu if requested
    if args.menu:
        import pipeline_menu
        cmd = pipeline_menu.run_menu()
        if cmd:
            import shlex
            subprocess.run(cmd, shell=True)
        return

    # Handle Stop Request
    if args.stop:
        with open("STOP_PIPELINE", "w") as f:
            f.write("STOP requested by user")
        print("\n🛑 Stop signal sent! The running pipeline will finish its current batch and exit gracefully.")
        return

    # Clear any previous stop signal at startup
    if os.path.exists("STOP_PIPELINE"):
        os.remove("STOP_PIPELINE")

    # Wait for scheduled time if --time is specified
    if args.time:
        try:
            target_hour, target_minute = map(int, args.time.split(':'))
            now = datetime.datetime.now()
            target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            
            # If target time already passed today, schedule for tomorrow
            if target_time <= now:
                target_time += datetime.timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()
            print(f"⏰ Scheduled to start at {args.time}")
            print(f"   Current time: {now.strftime('%H:%M:%S')}")
            print(f"   Waiting approximately {wait_seconds/3600:.1f} hours until {target_time.strftime('%Y-%m-%d %H:%M')}...")
            print(f"   (Checking time every 30 seconds to handle system sleep/wake safely)")
            
            while True:
                now = datetime.datetime.now()
                if now >= target_time:
                    break
                time.sleep(30)  # Check every 30 seconds instead of one long sleep
            print(f"\n⏰ Scheduled time reached! Starting pipeline...")
        except ValueError:
            print(f"⚠️ Invalid time format: {args.time}. Use HH:MM format (e.g., 06:00)")
            return

    print("Starting News Processing Pipeline (JSON Version)")
    print("=" * 50)
    total_start = time.time()

    # --- (A) Single-instance lock (advisory flock, non-blocking) ---
    # Prevents overlapping runs (launchd + scheduler triggering at the same time).
    # flock auto-releases when the process exits, so no stale-lock issue even on kill.
    _lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.pipeline.lock')
    global _PIPELINE_LOCK_FH
    _PIPELINE_LOCK_FH = open(_lock_path, 'w')
    try:
        fcntl.flock(_PIPELINE_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("🔒 Another pipeline run is already in progress (lock held). Exiting cleanly.")
        sys.exit(0)

    # --- (D) Max-runtime watchdog (3h hard limit on main thread) ---
    def _watchdog_handler(signum, frame):
        print("\n⏱️ Pipeline exceeded 3h max runtime. Aborting run.")
        sys.exit(1)
    try:
        signal.signal(signal.SIGALRM, _watchdog_handler)
        signal.alarm(3 * 60 * 60)
    except (ValueError, AttributeError):
        # Not on main thread or platform without SIGALRM; skip watchdog.
        pass
    # Downloads also stop at the same deadline (see article_downloader.set_run_deadline).
    try:
        import article_downloader
        article_downloader.set_run_deadline(3 * 60 * 60)
    except Exception:
        pass

    # Reset LM Studio failure counters for this pipeline run
    try:
        import lmstudio_client
        lmstudio_client.reset_pipeline_state()
    except ImportError:
        pass

    
    current_dir = os.getcwd()
    print(f"Working directory: {current_dir}")

    # Set up daily output directory
    today = datetime.date.today().strftime('%Y-%m-%d')
    daily_output_dir = os.path.join(current_dir, 'Output', today)
    
    if not os.path.exists(daily_output_dir):
        os.makedirs(daily_output_dir)
        print(f"Created daily directory: {daily_output_dir}")
        
    # Pass this path to subprocesses via environment variable
    os.environ['DAILY_OUTPUT_DIR'] = daily_output_dir

    # Load API Keys from input.txt
    input_file = os.path.join(current_dir, 'Input', 'input.txt')
    if os.path.exists(input_file):
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("IMGBB_API_KEY="):
                        key = line.split('=', 1)[1].strip()
                        os.environ['IMGBB_API_KEY'] = key
                        print("Loaded ImgBB API Key from input.txt")
                    elif line.startswith("GITHUB_TOKEN=") or line.startswith("GIT_TOKEN="):
                        key = line.split('=', 1)[1].strip()
                        os.environ['GITHUB_TOKEN'] = key
                        print("Loaded GitHub Token from input.txt")
        except Exception as e:
            print(f"Warning: Could not read input.txt: {e}")

    # Set waitrefresh mode if requested
    if args.waitrefresh:
        os.environ['WAITREFRESH'] = '1'
        print("Wait-refresh mode enabled: will wait for rate limit refresh")


    # Phase 0: Recovery — patch previous day's missing derived files
    # If yesterday's data.json exists but i4/i5/toplist are missing, run them now.
    # This silently fixes the case where the pipeline was killed before those steps ran.
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_dir = os.path.join(current_dir, 'Output', yesterday)
    yesterday_data = os.path.join(yesterday_dir, 'data.json')
    if os.path.exists(yesterday_data):
        missing_steps = []
        if not os.path.exists(os.path.join(yesterday_dir, 'data_i4.json')):
            missing_steps.append(("filter_importance.py", "Patching missing Importance Split", "importance"))
        if not os.path.exists(os.path.join(yesterday_dir, 'data_toplist.json')):
            missing_steps.append(("toplist_generator.py", "Patching missing Toplist", "toplist"))
        if missing_steps:
            print(f"\n🔧 PHASE 0: Recovery — patching {yesterday} missing derived files...")
            os.environ['DAILY_OUTPUT_DIR'] = yesterday_dir
            for script, desc, _ in missing_steps:
                print(f"  ↳ Running {script} for {yesterday}...")
                run_script(script, desc)
            # Always restore today's directory
            os.environ['DAILY_OUTPUT_DIR'] = daily_output_dir

    # Phase 1: Generators (Weather & Market)
    phase1_steps = [
        ("weather_generator.py", "Generating Weather Forecast", "weather"),
        ("market_generator.py", "Generating Market Analysis", "market")
    ]
    
    print("\n🌤️  PHASE 1: Generators...")
    
    # Determine which backend to use for generators (Auxiliary Backend)
    # Default to whatever is used for summarization if not specified, 
    # but preferably use the 'auxiliary_backend' from settings if available.
    gen_extra_args = []
    
    # Try to get from settings first (if loaded)
    aux_backend = None
    if 'settings' in locals() and 'pipeline' in settings:
         aux_backend = settings.get('pipeline', {}).get('auxiliary_backend')
    
    if aux_backend:
        print(f"   Using auxiliary backend for generators: {aux_backend}")
        if aux_backend == 'geminipro-cli':
            gen_extra_args.append("--use-geminipro-cli")
        elif aux_backend == 'perplexity':
            gen_extra_args.append("--use-perplexity")
        elif aux_backend == 'lmstudio-local':
            gen_extra_args.append("--use-lmstudio-local")
        elif aux_backend == 'lmstudio-remote':
            gen_extra_args.append("--use-lmstudio-remote")
        elif aux_backend == 'gemini-selenium':
            gen_extra_args.append("--use-gemini-selenium")
        elif aux_backend == 'gemini-api':
            gen_extra_args.append("--use-gemini-api")
        elif aux_backend == 'free-gemini-api':
            gen_extra_args.append("--use-free-gemini-api")
        elif aux_backend == 'gemini':
            gen_extra_args.append("--use-geminipro")
    else:
        # Fallback to mirroring summarizer flags
        if args.geminipro_cli_summarize:
            gen_extra_args.append("--use-geminipro-cli")
        elif args.geminipro_summarize:
            gen_extra_args.append("--use-geminipro")
        elif args.perplexity_summarize:
            gen_extra_args.append("--use-perplexity")
            
    if args.gemini_api_key:
         gen_extra_args.append(f"--gemini-api-key={args.gemini_api_key}")

    phase1_success = True
    for script, desc, arg_name in phase1_steps:
        # Check skip
        if getattr(args, f"skip_{arg_name}", False):
            print(f"Skipping {desc}")
            continue
            
        if not run_script(script, desc, gen_extra_args):
            phase1_success = False
            # Don't break — continue with remaining Phase 1 generators
            print(f"⚠️ {desc} failed, continuing with remaining generators...")
    
    # Specific Push for Phase 1
    if phase1_success and args.push:
         print("\n🚀 Pushing Weather & Market data to git...")
         try:
             subprocess.run(f"git add Output/*/idojaras.json Output/*/piacok.json Output/*/tts_*.txt", shell=True, check=False, cwd=current_dir)
             subprocess.run(["git", "commit", "-m", "Auto-update: Weather and Market"], check=False, cwd=current_dir)

             # Pull first to integrate any remote changes
             subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False, cwd=current_dir)

             gh_token = os.environ.get('GITHUB_TOKEN')
             if gh_token:
                 push_url = f"https://oauth2:{gh_token}@github.com/ViktorPosa/hirek.git"
                 # Capture output to prevent token logging
                 res = subprocess.run(["git", "push", push_url, "HEAD:main"], check=False, cwd=current_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 if res.returncode != 0:
                     print(f"⚠️ Phase 1 Push Failed via Token (Error hidden for security)")
                 else:
                     print("✅ Phase 1 Push Complete using GitHub Token")
             else:
                 subprocess.run(["git", "push"], check=False, cwd=current_dir)
                 print("✅ Phase 1 Push Complete")
         except Exception as e:
             print(f"⚠️ Phase 1 Push Failed: {e}")

    # Phase 2: Main Content Pipeline
    print("\n📰  PHASE 2: Content Pipeline...")
    # Use the global PIPELINE_STEPS definition
    main_pipeline = PIPELINE_STEPS
    
    # --- PARALLEL RSS: Run rss_creator and news_filter simultaneously ---
    skip_rss = getattr(args, "skip_rsscreator", False)
    skip_filter = getattr(args, "skip_filter", False)
    
    if not skip_rss and not skip_filter:
        # Both steps enabled — run them in parallel with bucket pattern
        print("\n🔄 PARALLEL RSS MODE: Running RSS Creator and RSS Reader simultaneously...")
        
        signal_file = os.path.join(daily_output_dir, 'rss_creator_done.signal')
        # Clean up any stale signal file
        if os.path.exists(signal_file):
            try:
                os.remove(signal_file)
            except Exception:
                pass
        
        # Start rss_creator as a subprocess
        rss_cmd = [sys.executable, "rss_creator.py"]
        print(f"\n{'='*50}")
        print(f"STEP: Generating Custom RSS Feeds (rss_creator.py) [BACKGROUND]")
        print(f"{'='*50}\n")
        rss_start = time.time()
        rss_process = subprocess.Popen(
            rss_cmd,
            env=os.environ.copy()
        )
        
        # Start news_filter with --parallel-rss flag
        filter_cmd = [sys.executable, "news_filter.py", "--parallel-rss"]
        print(f"\n{'='*50}")
        print(f"STEP: Collecting RSS Links (news_filter.py) [PARALLEL-RSS]")
        print(f"{'='*50}\n")
        filter_start = time.time()
        filter_process = subprocess.Popen(
            filter_cmd,
            env=os.environ.copy()
        )
        
        # Wait for rss_creator to finish, then write the signal file
        rss_returncode = rss_process.wait()
        rss_elapsed = time.time() - rss_start
        
        if rss_returncode == 0:
            print(f"\n>>> rss_creator.py completed successfully in {rss_elapsed:.2f} seconds.")
        else:
            print(f"\n!!! ERROR: rss_creator.py exited with code {rss_returncode}")
        
        # Write the signal file so news_filter knows rss_creator is done
        try:
            with open(signal_file, 'w') as f:
                f.write(f"done at {time.time()}")
            print(f"  📡 Signal file written: {signal_file}")
        except Exception as e:
            print(f"  ⚠️ Failed to write signal file: {e}")
        
        # Wait for news_filter to finish processing all feeds
        filter_returncode = filter_process.wait()
        filter_elapsed = time.time() - filter_start
        
        if filter_returncode == 0:
            print(f"\n>>> news_filter.py completed successfully in {filter_elapsed:.2f} seconds.")
        else:
            print(f"\n!!! ERROR: news_filter.py exited with code {filter_returncode}")
            success = False
        
        # Clean up signal file
        try:
            if os.path.exists(signal_file):
                os.remove(signal_file)
        except Exception:
            pass
        
        print(f"\n✅ Parallel RSS phase completed (RSS Creator: {rss_elapsed:.1f}s, RSS Reader: {filter_elapsed:.1f}s)")
    
    # Track success across both parallel phase and sequential loop
    parallel_rss_failed = False
    if not skip_rss and not skip_filter:
        # rss_returncode and filter_returncode were set in the parallel block above
        if rss_returncode != 0 or filter_returncode != 0:
            parallel_rss_failed = True
    
    success = True
    for script, desc, arg_name in main_pipeline:
        # Skip rss_creator and news_filter if they were already run in parallel
        if not skip_rss and not skip_filter and script in ("rss_creator.py", "news_filter.py"):
            print(f"Skipping {desc} (already completed in parallel RSS phase)")
            continue
        
        if getattr(args, f"skip_{arg_name}", False):
            print(f"Skipping {desc}")
            continue
            
        extra_args = []
        # news_filter.py no longer uses AI - it just collects RSS links
        # No extra arguments needed
                
        if script == "summarizer_json.py":
            if args.multi_model:
                extra_args.append("--multi-model")
                extra_args.append("--workers=3")
            elif args.geminipro_summarize:
                extra_args.append("--use-geminipro")
            elif args.gemini_summarize:
                extra_args.append("--use-gemini-cookie")
            elif args.geminiapi_summarize:
                extra_args.append("--use-gemini-api")
            elif args.geminipro_cli_summarize:
                extra_args.append("--use-geminipro-cli")
                extra_args.append("--workers=3")
            elif args.gemini_selenium:
                extra_args.append("--gemini-selenium")
                extra_args.append("--workers=1") # Selenium is single-threaded usually
            elif args.g4f_summarize:
                extra_args.append("--use-g4f")
                extra_args.append("--workers=2")
            elif args.deeperseek_summarize:
                extra_args.append("--use-deeperseek")
                extra_args.append("--workers=1")
            elif args.free_gemini_summarize:
                extra_args.append("--use-free-gemini-api")
                # Use slightly more workers since we have a pool of keys
                extra_args.append("--workers=3")
            elif args.lmstudio_local_summarize:
                extra_args.append("--use-lmstudio-local")
                extra_args.append("--workers=2")
            elif args.lmstudio_remote_summarize:
                extra_args.append("--use-lmstudio-remote")
                extra_args.append("--workers=2")
            
            # Check for specific key usage
            for i in range(10):
                if getattr(args, f"geminiapi_key_{i}", False):
                    extra_args.append("--use-gemini-api")
                    extra_args.append(f"--gemini-api-key-index={i-1}") # 0-based index
                    break
                    
            if args.perplexity_summarize:
                extra_args.append("--use-perplexity")
            if args.gemini_api_key:
                extra_args.append(f"--gemini-api-key={args.gemini_api_key}")
            if args.push:
                extra_args.append("--push")
            if args.debug_pairing:
                extra_args.append("--debug-pairing")
            
            # If image downloader step is disabled via --skip-images (CLI) 
            # OR if we want to tie it to the image_upload setting...
            # The USER requested: "image upload and download is turned off in the pipeline menu, why is this even here?"
            # So if the 'images' step is skipped, we should also tell summarizer to skip images.
            if getattr(args, "skip_images", False):
                 extra_args.append("--skip-images")
                 print("   🚫 Image processing disabled in summarizer (due to skipped image step)")
        
        # Pass --skip-upload to image_downloader when upload is disabled
        if script == "image_downloader.py" and not image_upload_enabled:
            extra_args.append("--skip-upload")
            print("   📥 Image upload disabled via settings — download only")
        
        # Disconnect NordVPN right BEFORE the Gemini summarization step runs.
        if arg_name == "summarize" or script == "summarizer_json.py":
            print("\n🔌 VPN: disconnecting NordVPN before summarization step...")
            vpn_control.disconnect(wait=True, timeout=60)

        if not run_script(script, desc, extra_args if extra_args else None):
            success = False
            break
        
        # Immediately sync images to Git after image_downloader completes
        if script == "image_downloader.py":
            print("\n📸 Syncing images to dedicated Git repository (post-download)...")
            try:
                subprocess.run(
                    [sys.executable, "git_sync_images.py"],
                    cwd=current_dir,
                    check=True
                )
            except Exception as e:
                print(f"⚠️ Failed to sync images to ImageRepo: {e}")
            
        # Check for stop signal after each step
        if os.path.exists("STOP_PIPELINE"):
            print(f"\n🛑 Pipeline stopped securely after {script}.")
            print("To resume, simply run this script again.")
            try:
                os.remove("STOP_PIPELINE")
            except:
                pass
            return
    
    if parallel_rss_failed:
        success = False


    total_elapsed = time.time() - total_start
    print(f"\n{'='*50}")
    print(f"PIPELINE COMPLETED SUCCESSFULLY")
    print(f"Total time: {total_elapsed:.2f} seconds")
    print(f"Output format: JSON (data.json, piacok.json, idojaras.json)")
    print(f"{'='*50}")
    
    # --- Check for ImgBB Errors ---
    today = datetime.date.today().strftime('%Y-%m-%d')
    error_log_path = os.path.join(current_dir, "Output", today, "pipeline_errors.log")
    if os.path.exists(error_log_path):
        imgbb_errors = 0
        try:
            with open(error_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if "ImgBB returned 'Service Unavailable'" in line:
                        imgbb_errors += 1
            if imgbb_errors > 0:
                print("\n\033[91m" + "!" * 50)
                print(f"🚨 WARNING: {imgbb_errors} ImgBB 'Service Unavailable' errors detected!")
                print("These 'fake' image links were KEPT as requested, but might not load.")
                print("Check pipeline_errors.log for details.")
                print("!" * 50 + "\033[0m\n")
        except Exception as e:
            pass

    # Git push if requested
    if args.push:
        print("\n📤 Pushing to git...")
        try:
            today = datetime.date.today().strftime('%Y-%m-%d')

            # Ensure per-day default filter-words JSON exists so apps can fetch "today's" list
            ensure_daily_filter_words(current_dir, today)

            # Add all changes
            subprocess.run(["git", "add", "-A"], check=True, cwd=current_dir)

            # Commit with date-based message
            commit_msg = f"Auto-update: {today} news"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, cwd=current_dir)

            # Pull first to integrate any remote changes
            subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False, cwd=current_dir)

            # Push
            gh_token = os.environ.get('GITHUB_TOKEN')
            if gh_token:
                push_url = f"https://oauth2:{gh_token}@github.com/ViktorPosa/hirek.git"
                res = subprocess.run(["git", "-c", "credential.helper=", "push", push_url, "HEAD:main"], check=False, cwd=current_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0:
                    print(f"⚠️ Git push failed via Token (Error hidden for security)")
                else:
                    print("✅ Successfully pushed to git using GitHub Token!")
            else:
                subprocess.run(["git", "push"], check=True, cwd=current_dir)
                print("✅ Successfully pushed to git!")
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Git push failed: {e}")
        except Exception as e:
            print(f"⚠️ Git error: {e}")

    # Sync Images to the dedicated Git repository
    print("\n📸 Syncing images to dedicated Git repository...")
    try:
        subprocess.run(
            [sys.executable, "git_sync_images.py"],
            cwd=current_dir,
            check=True
        )
    except Exception as e:
        print(f"⚠️ Failed to sync images to ImageRepo: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
