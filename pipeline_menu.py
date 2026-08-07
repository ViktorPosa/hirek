"""
LEÍRÁS:
Interaktív CLI menürendszer a hírfeldolgozó pipeline konfigurálásához és futtatásához.
Lehetővé teszi a lépések ki/bekapcsolását, backendek kiválasztását és egyéb globális beállítások kezelését.
A beállításokat JSON fájlba menti.

BEMENET:
- Felhasználói interakció (billentyűzet)
- Mentett beállítások: Input/pipeline_settings.json

KIMENET:
- Generált parancssori utasítás a run_pipeline.py számára
- Frissített beállítások fájl
"""


import os
import sys
import json

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')

# Default settings
DEFAULT_SETTINGS = {
    "global": {
        "proxy": None,
        "default_timeout": 120,
        "workers": 3,
        "summarizer_batch_size": 10,
        "debug_pairing": False
    },
    "g4f": {
        "model": "gpt-4o-mini",
        "provider": "auto",
        "web_search": False,
        "timeout": 120
    },
    "gemini": {
        "model": "unspecified",
        "timeout": 30,
        "auto_close": False,
        "close_delay": 300,
        "auto_refresh": True
    },
    "perplexity": {
        "mode": "auto",
        "model": None,
        "sources": ["web"],
        "language": "en-US",
        "incognito": False
    },
    "deeperseek": {
        "headless": True,
        "deep_think": False
    },
    "multi_model": {
        "enabled_backends": ["g4f", "gemini_api", "geminipro", "perplexity", "deeperseek"],
        "parallel_mode": "failover",  # "failover", "parallel", "all"
        "max_parallel": 3,
        "retry_failed": True,
        "failover_backends": {
            "free-gemini-api": ["perplexity", "gemini-selenium"]
        },
        "free_gemini_daily_limit": 20
    },
    "pipeline": {
        "push_enabled": False,
        "selected_backend": "geminipro-cli",  # For summarizer (main)
        "auxiliary_backend": "gemini-api",    # For weather, piacok, tts
        "linkfilter_backend": "g4f",          # For news_filter (link processing)
        "steps": {},
        "image_upload": True    # Upload images to ImgBB (separate from download step)
    }
}

# Available options for each setting
G4F_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4", "gpt-3.5-turbo", "deepseek-v3", "claude-3-haiku"]
G4F_PROVIDERS = ["auto", "DDG", "Bing", "OpenaiChat", "Gemini", "MetaAI", "PerplexityLabs"]

GEMINI_MODELS = ["unspecified", "gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-flash-latest"]

PERPLEXITY_MODES = ["auto", "pro", "reasoning", "deep research"]
PERPLEXITY_MODELS = {
    "auto": [None],
    "pro": [None, "sonar", "gpt-5.2", "claude-4.6-sonnet", "grok-4-1"],
    "reasoning": [None, "gpt-5.2-thinking", "claude-4.6-sonnet-thinking", "gemini-3.1-pro-preview", "kimi-k2-thinking"],
    "deep research": [None]
}
PERPLEXITY_SOURCES = ["web", "scholar", "social"]
LANGUAGES = ["en-US", "hu-HU", "de-DE", "fr-FR", "es-ES", "it-IT", "pl-PL", "pt-BR", "ja-JP", "zh-CN"]

# Pipeline steps configuration
PIPELINE_STEPS = [
    ("weather_generator.py", "Weather Forecast Generator", "weather", True),
    ("market_generator.py", "Market Analysis Generator", "market", True),
    ("rss_creator.py", "Custom RSS Feeds Generator", "rsscreator", True),
    ("news_filter.py", "RSS Link Collector", "filter", True),
    ("link_dedup.py", "Link Deduplication by Topic", "linkdedup", True),
    ("summarizer_json.py", "News Summarizer (JSON)", "summarize", True),
    ("toplist_generator.py", "Toplists from Specific URLs", "toplist", True),
    ("justwatch_scraper.py", "JustWatch Trending Scraper", "justwatch", True),
    ("dedup.py", "News Deduplication", "dedup", True),
    ("filter_news.py", "Negative News Filter", "newsfilter", True),
    ("image_downloader.py", "Image Downloader", "images", True),
    ("tag_generator_json.py", "Tag Generator", "tags", True),
    ("section_validator.py", "Section Validator", "validate", True),
    ("ajanlott_generator.py", "Recommendations Generator", "ajanlott", True),
    ("filter_importance.py", "Importance Splitter (4/5 stars)", "importance", True),
    ("randomize_sections.py", "Randomize Sections", "randomize", True),
    ("tts_generator.py", "TTS News Script Generator", "tts", False),
    ("gemini_tts_selenium.py", "TTS Audio Generation (Selenium)", "ttsaudio", False),
]

# Maintenance tools (not part of main pipeline)
MAINTENANCE_TOOLS = [
    ("retry_failed.py", "Retry Failed Links", "Reprocesses links that failed in previous runs"),
    ("clear_negative_history.py", "Clear Negative History", "Removes falsely negative-marked items from history"),
    ("clear_rss_negative.py", "Clear RSS Negatives", "Re-enables negative items found in current RSS"),
    ("refresh_cookies.py", "Refresh Cookies", "Updates authentication cookies for web clients"),
]

# Available AI backends
def get_ai_backends():
    base_backends = [
        ("multi-model", "🚀 Multi-Model (All with Failover)", "multi_model"),
        ("geminipro-cli", "Gemini Chat API (Cookie)", "geminipro_cli_summarize"),
        ("gemini-selenium", "Gemini Selenium (Default Profile)", "gemini-selenium"),
        ("gemini-api", "Gemini API (Key - Auto/Random)", "geminiapi_summarize"),
    ]
    
    # Dynamically add specific keys if available
    try:
        import gemini_api_client
        count = gemini_api_client.get_key_count()
        if count > 1:
            for i in range(count):
                key_name = f"gemini-api-{i+1}"
                # We'll use a special internal flag format that run_pipeline knows how to parse or we'll map it later
                base_backends.append((key_name, f"Gemini API (Key #{i+1})", f"geminiapi_key_{i}"))
    except Exception as e:
        pass
        
    base_backends.extend([
        ("g4f", "GPT4Free (Free)", "g4f_summarize"),
        ("free-gemini-api", "Free Gemini Pool (20/day/key)", "free_gemini_summarize"),
        ("deeperseek", "DeeperSeek (Free)", "deeperseek_summarize"),
        ("perplexity", "Perplexity Pro", "perplexity_summarize"),
        ("lmstudio-local", "LM Studio Helyi (127.0.0.1)", "lmstudio_local_summarize"),
        ("lmstudio-remote", "LM Studio Távoli (192.168.0.10)", "lmstudio_remote_summarize"),
    ])
    return base_backends

AI_BACKENDS = get_ai_backends()


def load_settings():
    """Load settings from file or return defaults."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                # Merge with defaults (in case new settings added)
                settings = DEFAULT_SETTINGS.copy()
                for key in saved:
                    if key in settings and isinstance(settings[key], dict):
                        settings[key].update(saved[key])
                    else:
                        settings[key] = saved[key]
                return settings
        except:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """Save settings to file."""
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)
    print("  ✓ Settings saved")


class PipelineMenu:
    def __init__(self):
        self.settings = load_settings()
        self._load_steps()
        
    def _load_steps(self):
        """Load step states from settings."""
        self.steps = {}
        saved_steps = self.settings.get('pipeline', {}).get('steps', {})
        for _, _, arg, default in PIPELINE_STEPS:
            self.steps[arg] = saved_steps.get(arg, default)
    
    def _save_steps(self):
        """Save step states to settings."""
        self.settings['pipeline']['steps'] = self.steps
        
    def clear_screen(self):
        os.system('clear' if os.name != 'nt' else 'cls')
    
    def print_header(self, title):
        width = 55
        print("╔" + "═" * width + "╗")
        print("║" + title.center(width) + "║")
        print("╠" + "═" * width + "╣")
    
    def print_footer(self):
        print("╚" + "═" * 55 + "╝")
    
    def print_menu_item(self, num, text, checked=None, value=None):
        width = 55
        if checked is not None:
            status = "[✓]" if checked else "[ ]"
            line = f"║ [{num}] {status} {text}"
        elif value is not None:
            line = f"║ [{num}] {text}: {value}"
        else:
            line = f"║ [{num}] {text}"
        print(line + " " * (width + 1 - len(line)) + "║")
    
    def print_separator(self):
        print("║" + " " * 55 + "║")
    
    # =========== MAIN MENU ===========
    def show_main_menu(self):
        self.clear_screen()
        backend = self.settings['pipeline']['selected_backend']
        aux_backend = self.settings['pipeline'].get('auxiliary_backend', 'gemini-api')
        workers = self.settings['global']['workers']
        push = self.settings['pipeline']['push_enabled']
        
        # Get history stats
        try:
            from history_manager import HistoryManager
            history = HistoryManager()
            stats = history.get_stats()
            stats_str = f"📊 {stats['total_links']} links | ✅{stats['summarized']} done | 🚫{stats['filtered']} filtered | ❌{stats['negative']} neg"
        except:
            stats_str = "📊 History not available"
        
        self.print_header("News Pipeline Configuration")
        print(f"  {stats_str}")
        self.print_separator()
        self.print_menu_item(1, "Run full pipeline")
        self.print_menu_item(2, f"Select steps ({sum(self.steps.values())}/{len(self.steps)} enabled)")
        self.print_separator()
        print("  📦 Backend Selection:")
        self.print_menu_item(3, f"  Summarizer backend (includes filtering)", value=backend)
        self.print_menu_item(4, f"  Auxiliary backend (weather/piacok/tts)", value=aux_backend)
        self.print_separator()
        self.print_menu_item(5, f"Configure workers", value=workers)
        self.print_menu_item(6, f"Toggle git push", value="ON" if push else "OFF")
        self.print_separator()
        self.print_menu_item(7, "⚙ Backend Settings →")
        self.print_menu_item(8, "🌐 Global Settings →")
        self.print_menu_item(9, "🔧 Maintenance Tools →")
        self.print_separator()
        self.print_menu_item("S", "▶ START PIPELINE")
        self.print_menu_item("P", "Preview command")
        self.print_menu_item("H", "📊 View History Stats")
        self.print_menu_item(0, "Exit (settings saved)")
        self.print_footer()
    
    # =========== BACKEND SETTINGS MENU ===========
    def show_backend_settings_menu(self):
        self.clear_screen()
        self.print_header("Backend Settings")
        self.print_menu_item(1, "🚀 Multi-Model Settings →")
        self.print_menu_item(2, "G4F (GPT4Free) Settings →")
        self.print_menu_item(3, "Gemini Settings →")
        self.print_menu_item(4, "Perplexity Settings →")
        self.print_menu_item(5, "DeeperSeek Settings →")
        self.print_separator()
        self.print_menu_item(0, "← Back to main menu")
        self.print_footer()
    
    # =========== G4F SETTINGS ===========
    def show_g4f_settings(self):
        self.clear_screen()
        s = self.settings['g4f']
        self.print_header("G4F (GPT4Free) Settings")
        self.print_menu_item(1, "Model", value=s['model'])
        self.print_menu_item(2, "Provider", value=s['provider'])
        self.print_menu_item(3, "Web Search", value="ON" if s['web_search'] else "OFF")
        self.print_menu_item(4, "Timeout (sec)", value=s['timeout'])
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_g4f_settings(self):
        while True:
            self.show_g4f_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self._select_from_list("G4F Model", G4F_MODELS, 'g4f', 'model')
            elif choice == '2':
                self._select_from_list("G4F Provider", G4F_PROVIDERS, 'g4f', 'provider')
            elif choice == '3':
                self.settings['g4f']['web_search'] = not self.settings['g4f']['web_search']
                save_settings(self.settings)
            elif choice == '4':
                self._input_number("Timeout (sec)", 'g4f', 'timeout', 10, 600)
    
    # =========== GEMINI SETTINGS ===========
    def show_gemini_settings(self):
        self.clear_screen()
        s = self.settings['gemini']
        self.print_header("Gemini Settings")
        self.print_menu_item(1, "Model", value=s['model'])
        self.print_menu_item(2, "Init Timeout (sec)", value=s['timeout'])
        self.print_menu_item(3, "Auto-close", value="ON" if s['auto_close'] else "OFF")
        self.print_menu_item(4, "Close Delay (sec)", value=s['close_delay'])
        self.print_menu_item(5, "Auto-refresh cookies", value="ON" if s['auto_refresh'] else "OFF")
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_gemini_settings(self):
        while True:
            self.show_gemini_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self._select_from_list("Gemini Model", GEMINI_MODELS, 'gemini', 'model')
            elif choice == '2':
                self._input_number("Init Timeout (sec)", 'gemini', 'timeout', 10, 120)
            elif choice == '3':
                self.settings['gemini']['auto_close'] = not self.settings['gemini']['auto_close']
                save_settings(self.settings)
            elif choice == '4':
                self._input_number("Close Delay (sec)", 'gemini', 'close_delay', 60, 3600)
            elif choice == '5':
                self.settings['gemini']['auto_refresh'] = not self.settings['gemini']['auto_refresh']
                save_settings(self.settings)
    
    # =========== PERPLEXITY SETTINGS ===========
    def show_perplexity_settings(self):
        self.clear_screen()
        s = self.settings['perplexity']
        sources_str = ", ".join(s['sources']) if s['sources'] else "none"
        self.print_header("Perplexity Settings")
        self.print_menu_item(1, "Mode", value=s['mode'])
        self.print_menu_item(2, "Model", value=s['model'] or "default")
        self.print_menu_item(3, "Sources", value=sources_str)
        self.print_menu_item(4, "Language", value=s['language'])
        self.print_menu_item(5, "Incognito", value="ON" if s['incognito'] else "OFF")
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_perplexity_settings(self):
        while True:
            self.show_perplexity_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self._select_from_list("Perplexity Mode", PERPLEXITY_MODES, 'perplexity', 'mode')
                # Reset model when mode changes
                self.settings['perplexity']['model'] = None
                save_settings(self.settings)
            elif choice == '2':
                mode = self.settings['perplexity']['mode']
                models = PERPLEXITY_MODELS.get(mode, [None])
                model_strs = [str(m) if m else "default" for m in models]
                idx = self._select_from_list_return_idx("Perplexity Model", model_strs)
                if idx is not None:
                    self.settings['perplexity']['model'] = models[idx]
                    save_settings(self.settings)
            elif choice == '3':
                self._toggle_list("Sources", PERPLEXITY_SOURCES, 'perplexity', 'sources')
            elif choice == '4':
                self._select_from_list("Language", LANGUAGES, 'perplexity', 'language')
            elif choice == '5':
                self.settings['perplexity']['incognito'] = not self.settings['perplexity']['incognito']
                save_settings(self.settings)
    
    # =========== DEEPERSEEK SETTINGS ===========
    def show_deeperseek_settings(self):
        self.clear_screen()
        s = self.settings['deeperseek']
        self.print_header("DeeperSeek Settings")
        self.print_menu_item(1, "Headless browser", value="ON" if s['headless'] else "OFF")
        self.print_menu_item(2, "DeepThink (R1 model)", value="ON" if s['deep_think'] else "OFF")
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_deeperseek_settings(self):
        while True:
            self.show_deeperseek_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self.settings['deeperseek']['headless'] = not self.settings['deeperseek']['headless']
                save_settings(self.settings)
            elif choice == '2':
                self.settings['deeperseek']['deep_think'] = not self.settings['deeperseek']['deep_think']
                save_settings(self.settings)
    
    # =========== MULTI-MODEL SETTINGS ===========
    def show_multi_model_settings(self):
        self.clear_screen()
        s = self.settings.get('multi_model', DEFAULT_SETTINGS['multi_model'])
        self.print_header("🚀 Multi-Model Settings")
        
        # Show enabled backends
        enabled = ", ".join(s['enabled_backends']) if s['enabled_backends'] else "none"
        self.print_menu_item(1, "Enabled Backends", value=enabled)
        
        # Parallel mode
        self.print_menu_item(2, "Mode", value=s['parallel_mode'])
        
        # Max parallel workers
        self.print_menu_item(3, "Max Parallel Workers", value=s['max_parallel'])
        
        # Retry failed
        self.print_menu_item(4, "Retry Failed Requests", value="ON" if s['retry_failed'] else "OFF")
        
        # Fallback Backend
        fallback = s.get('fallback_backend', 'none')
        self.print_menu_item(5, "Fallback Backend", value=fallback)
        
        # Show backend health status
        self.print_separator()
        print("  📊 Backend Health Status:")
        try:
            import backend_orchestrator
            orch = backend_orchestrator.get_orchestrator()
            status = orch.get_status()
            for backend, state in status.items():
                color = "🟢" if "healthy" in state else "🔴"
                print(f"     {color} {backend}: {state}")
        except:
            print("     (Run with --multi-model to see status)")
        
        self.print_separator()
        limit = s.get('free_gemini_daily_limit', 20)
        self.print_menu_item(6, "Free Gemini Pool Limit (requests/day)", value=limit)
        self.print_menu_item(7, "🔄 Reset All Backends (clear suspensions)")
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_multi_model_settings(self):
        # Ensure multi_model section exists
        if 'multi_model' not in self.settings:
            self.settings['multi_model'] = DEFAULT_SETTINGS['multi_model'].copy()
            save_settings(self.settings)
        
        while True:
            self.show_multi_model_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                # Toggle backends
                all_backends = ["g4f", "gemini-api", "geminipro-cli", "gemini-selenium", "free-gemini-api", "perplexity", "deeperseek", "lmstudio-local", "lmstudio-remote"]
                self._toggle_list("Enabled Backends", all_backends, 'multi_model', 'enabled_backends')
            elif choice == '2':
                modes = ["failover", "parallel", "all"]
                self._select_from_list("Parallel Mode", modes, 'multi_model', 'parallel_mode')
            elif choice == '3':
                self._input_number("Max Parallel Workers", 'multi_model', 'max_parallel', 1, 10)
            elif choice == '4':
                self.settings['multi_model']['retry_failed'] = not self.settings['multi_model']['retry_failed']
                save_settings(self.settings)
            elif choice == '5':
                # Select Fallback Backend
                all_backends = ["none", "g4f", "gemini-api", "geminipro-cli", "gemini-selenium", "free-gemini-api", "perplexity", "deeperseek", "lmstudio-local", "lmstudio-remote"]
                self._select_from_list("Fallback Backend", all_backends, 'multi_model', 'fallback_backend')
            elif choice == '6':
                self._input_number("Free Gemini limit/day", 'multi_model', 'free_gemini_daily_limit', 1, 1500)
            elif choice == '7':
                # Reset all backends
                try:
                    import backend_orchestrator
                    orch = backend_orchestrator.get_orchestrator()
                    orch.reset_all()
                    print("\n✅ All backends reset to healthy!")
                    input("Press Enter to continue...")
                except Exception as e:
                    print(f"\n❌ Error resetting backends: {e}")
                    input("Press Enter to continue...")
    
    # =========== GLOBAL SETTINGS ===========
    def show_global_settings(self):
        self.clear_screen()
        s = self.settings['global']
        self.print_header("Global Settings")
        self.print_menu_item(1, "Proxy URL", value=s['proxy'] or "none")
        self.print_menu_item(2, "Default Timeout (sec)", value=s['default_timeout'])
        self.print_menu_item(3, "Workers", value=s['workers'])
        self.print_menu_item(4, "Summarizer Batch Size", value=s.get('summarizer_batch_size', 5))
        self.print_menu_item(5, "Debug Pairing Mode (Stop on fail)", value="ON" if s.get('debug_pairing', False) else "OFF")
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_global_settings(self):
        while True:
            self.show_global_settings()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                proxy = input("Enter proxy URL (blank to clear): ").strip()
                self.settings['global']['proxy'] = proxy if proxy else None
                save_settings(self.settings)
            elif choice == '2':
                self._input_number("Default Timeout (sec)", 'global', 'default_timeout', 30, 600)
            elif choice == '3':
                self._input_number("Workers", 'global', 'workers', 1, 20)
            elif choice == '4':
                self._input_number("Summarizer Batch Size", 'global', 'summarizer_batch_size', 1, 50)
            elif choice == '5':
                current = self.settings['global'].get('debug_pairing', False)
                self.settings['global']['debug_pairing'] = not current
                save_settings(self.settings)
    
    # =========== STEPS MENU ===========
    def show_steps_menu(self):
        self.clear_screen()
        self.print_header("Toggle Pipeline Steps")
        
        for i, (script, name, arg, _) in enumerate(PIPELINE_STEPS, 1):
            self.print_menu_item(i, name, self.steps[arg])
            # Show upload sub-option under Image Downloader
            if arg == 'images':
                upload_on = self.settings['pipeline'].get('image_upload', True)
                status = "ON" if upload_on else "OFF"
                print(f"║       └─ Upload to ImgBB: {status}  (toggle with 'U')" + " " * 3 + "║")
        
        self.print_separator()
        self.print_menu_item("U", "Toggle ImgBB Upload")
        self.print_menu_item("A", "Enable All")
        self.print_menu_item("N", "Disable All")
        self.print_menu_item(0, "← Back")
        self.print_footer()
    
    def run_steps_menu(self):
        while True:
            self.show_steps_menu()
            choice = input("\nSelect option: ").strip().upper()
            
            if choice == '0':
                self._save_steps()
                save_settings(self.settings)
                break
            elif choice == 'A':
                for key in self.steps:
                    self.steps[key] = True
            elif choice == 'N':
                for key in self.steps:
                    self.steps[key] = False
            elif choice == 'U':
                current = self.settings['pipeline'].get('image_upload', True)
                self.settings['pipeline']['image_upload'] = not current
                status = "ON" if not current else "OFF"
                print(f"  ImgBB Upload toggled: {status}")
                save_settings(self.settings)
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(PIPELINE_STEPS):
                    arg = PIPELINE_STEPS[idx][2]
                    self.steps[arg] = not self.steps[arg]
    
    # =========== BACKEND SELECTION ===========
    def show_backend_menu(self):
        self.clear_screen()
        self.print_header("Select Summarizer Models")
        
        # Get enabled backends from multi_model settings
        # If current mode is NOT multi-model, we might need to infer or reset
        enabled = set(self.settings.get('multi_model', {}).get('enabled_backends', []))
        
        # Filter out 'multi-model' from options, we only want concrete backends
        backends = get_ai_backends()
        options = [b for b in backends if b[0] != 'multi-model']
        
        for i, (key, name, _) in enumerate(options, 1):
            checked = key in enabled
            self.print_menu_item(i, name, checked=checked)
        
        self.print_separator()
        self.print_menu_item("F", "Configure Failovers (per-LLM)")
        self.print_menu_item("A", "Select All")
        self.print_menu_item("N", "Select None")
        self.print_menu_item(0, "← Done (Save & Back)")
        self.print_footer()
    
    def run_backend_menu(self):
        while True:
            self.show_backend_menu()
            choice = input("\nSelect option to toggle: ").strip().upper()
            
            # Filter options again
            backends = get_ai_backends()
            options = [b for b in backends if b[0] != 'multi-model']
            current_enabled = self.settings.get('multi_model', {}).get('enabled_backends', [])
            
            if choice == '0':
                # Save and Exit
                # Ensure we are in multi-model mode if we used this menu
                self.settings['pipeline']['selected_backend'] = 'multi-model'
                save_settings(self.settings)
                break
            
            elif choice == 'A':
                # Select All
                self.settings['multi_model']['enabled_backends'] = [b[0] for b in options]
            
            elif choice == 'N':
                # Select None
                self.settings['multi_model']['enabled_backends'] = []
                
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    key = options[idx][0]
                    if key in current_enabled:
                        current_enabled.remove(key)
                    else:
                        current_enabled.append(key)
                    self.settings['multi_model']['enabled_backends'] = current_enabled
            
            elif choice == 'F':
                self.run_failover_menu()
    
    def run_failover_menu(self):
        while True:
            self.clear_screen()
            self.print_header("Configure Failovers (Per-LLM)")
            
            # Show enabled backends
            enabled = self.settings.get('multi_model', {}).get('enabled_backends', [])
            failovers = self.settings.get('multi_model', {}).get('failover_backends', {})
            
            if not enabled:
                print("  ⚠️ No backends enabled in Multi-Model mode.")
                print("  Please enable backends first.")
                self.print_separator()
                self.print_menu_item(0, "← Back")
                input("\nPress Enter to return: ")
                break
                
            backends = get_ai_backends()
            options = [b for b in backends if b[0] in enabled]
            
            for i, (key, name, _) in enumerate(options, 1):
                current_failovers = failovers.get(key, [])
                failover_text = " -> ".join(current_failovers) if current_failovers else "None (No Failover)"
                print(f"  [{i}] {name}")
                print(f"      Failover chain: {failover_text}")
            
            self.print_separator()
            self.print_menu_item(0, "← Back")
            self.print_footer()
            
            choice = input("\nSelect backend to configure failover: ").strip().upper()
            if choice == '0':
                save_settings(self.settings)
                break
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    key = options[idx][0]
                    self.run_specific_failover_menu(key, options[idx][1])
    
    def run_specific_failover_menu(self, target_backend_key, target_backend_name):
        while True:
            self.clear_screen()
            self.print_header(f"Failovers for: {target_backend_name}")
            
            if 'failover_backends' not in self.settings['multi_model']:
                self.settings['multi_model']['failover_backends'] = {}
            current_failovers = self.settings['multi_model']['failover_backends'].get(target_backend_key, [])
            
            print("  Current failover chain (executed in order):")
            for i, fb in enumerate(current_failovers, 1):
                print(f"    {i}. {fb}")
            if not current_failovers:
                print("    None (No failovers configured)")
            
            self.print_separator()
            
            # Available failovers (all backends except the target itself)
            backends = get_ai_backends()
            available = [b for b in backends if b[0] != 'multi-model' and b[0] != target_backend_key]
            
            for i, (key, name, _) in enumerate(available, 1):
                checked = key in current_failovers
                self.print_menu_item(i, name, checked=checked)
                
            self.print_separator()
            self.print_menu_item("C", "Clear All Failovers")
            self.print_menu_item(0, "← Done")
            self.print_footer()
            
            choice = input("\nSelect option: ").strip().upper()
            
            if choice == '0':
                break
            elif choice == 'C':
                self.settings['multi_model']['failover_backends'][target_backend_key] = []
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(available):
                    selected_key = available[idx][0]
                    if selected_key in current_failovers:
                        current_failovers.remove(selected_key)
                    else:
                        current_failovers.append(selected_key)
                    self.settings['multi_model']['failover_backends'][target_backend_key] = current_failovers
    
    def run_auxiliary_backend_menu(self):
        """Select backend for weather, piacok, tts scripts."""
        self.clear_screen()
        current = self.settings['pipeline'].get('auxiliary_backend', 'gemini-api')
        self.print_header("Auxiliary Backend (weather/piacok/tts)")
        backends = get_ai_backends()
        for i, (key, name, _) in enumerate(backends, 1):
            checked = key == current
            self.print_menu_item(i, name, checked=checked)
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
        
        choice = input("\nSelect option: ").strip()
        if choice.isdigit() and int(choice) > 0:
            idx = int(choice) - 1
            if 0 <= idx < len(backends):
                self.settings['pipeline']['auxiliary_backend'] = backends[idx][0]
                save_settings(self.settings)
    
    def run_linkfilter_backend_menu(self):
        """Select backend for news_filter (link processing)."""
        self.clear_screen()
        current = self.settings['pipeline'].get('linkfilter_backend', 'g4f')
        self.print_header("Link Filter Backend (news_filter)")
        backends = get_ai_backends()
        for i, (key, name, _) in enumerate(backends, 1):
            checked = key == current
            self.print_menu_item(i, name, checked=checked)
        self.print_separator()
        self.print_menu_item(0, "← Back")
        self.print_footer()
        
        choice = input("\nSelect option: ").strip()
        if choice.isdigit() and int(choice) > 0:
            idx = int(choice) - 1
            if 0 <= idx < len(backends):
                self.settings['pipeline']['linkfilter_backend'] = backends[idx][0]
                save_settings(self.settings)
    
    # =========== WORKERS MENU ===========
    def run_workers_menu(self):
        self.clear_screen()
        self.print_header("Configure Worker Count")
        current = self.settings['global']['workers']
        print(f"║  Current workers: {current}" + " " * (36 - len(str(current))) + "║")
        self.print_separator()
        print("║  More workers = faster but more rate limits       ║")
        print("║  Recommended: 2-3 for free APIs                   ║")
        self.print_separator()
        self.print_menu_item(1, "1 worker (safest)")
        self.print_menu_item(2, "2 workers")
        self.print_menu_item(3, "3 workers (recommended)")
        self.print_menu_item(5, "5 workers")
        self.print_menu_item("T", "10 workers (aggressive)")
        self.print_menu_item("C", "Custom value")
        self.print_menu_item(0, "← Back")
        self.print_footer()
        
        choice = input("\nSelect option: ").strip().upper()
        
        if choice == '0':
            return
        elif choice == 'T':
            self.settings['global']['workers'] = 10
        elif choice == 'C':
            self._input_number("Workers", 'global', 'workers', 1, 20)
            return
        elif choice.isdigit() and int(choice) in [1, 2, 3, 5]:
            self.settings['global']['workers'] = int(choice)
        
        save_settings(self.settings)
    
    # =========== HELPER METHODS ===========
    def _select_from_list(self, title, options, section, key):
        self.clear_screen()
        self.print_header(f"Select {title}")
        current = self.settings[section][key]
        
        for i, opt in enumerate(options, 1):
            indicator = "●" if opt == current else "○"
            line = f"║ [{i}] {indicator} {opt}"
            print(line + " " * (56 - len(line)) + "║")
        
        self.print_separator()
        self.print_menu_item(0, "← Cancel")
        self.print_footer()
        
        choice = input("\nSelect option: ").strip()
        if choice.isdigit() and 0 < int(choice) <= len(options):
            self.settings[section][key] = options[int(choice) - 1]
            save_settings(self.settings)
    
    def _select_from_list_return_idx(self, title, options):
        self.clear_screen()
        self.print_header(f"Select {title}")
        
        for i, opt in enumerate(options, 1):
            line = f"║ [{i}] {opt}"
            print(line + " " * (56 - len(line)) + "║")
        
        self.print_separator()
        self.print_menu_item(0, "← Cancel")
        self.print_footer()
        
        choice = input("\nSelect option: ").strip()
        if choice.isdigit() and 0 < int(choice) <= len(options):
            return int(choice) - 1
        return None
    
    def _input_number(self, title, section, key, min_val, max_val):
        current = self.settings[section][key]
        value = input(f"Enter {title} [{min_val}-{max_val}] (current: {current}): ").strip()
        if value.isdigit():
            num = int(value)
            if min_val <= num <= max_val:
                self.settings[section][key] = num
                save_settings(self.settings)
            else:
                print(f"  ⚠ Value must be between {min_val} and {max_val}")
                input("Press Enter...")
    
    def _toggle_list(self, title, options, section, key):
        while True:
            self.clear_screen()
            self.print_header(f"Toggle {title}")
            current = self.settings[section][key]
            
            for i, opt in enumerate(options, 1):
                checked = opt in current
                self.print_menu_item(i, opt, checked=checked)
            
            self.print_separator()
            self.print_menu_item(0, "← Done")
            self.print_footer()
            
            choice = input("\nToggle option: ").strip()
            if choice == '0':
                save_settings(self.settings)
                break
            elif choice.isdigit() and 0 < int(choice) <= len(options):
                opt = options[int(choice) - 1]
                if opt in current:
                    current.remove(opt)
                else:
                    current.append(opt)
                self.settings[section][key] = current
    
    # =========== COMMAND BUILDER ===========
    def build_command(self):
        """Build the command line string based on current configuration."""
        cmd_parts = [f'"{sys.executable}"', "run_pipeline.py"]
        
        # Add skip flags
        for script, name, arg, _ in PIPELINE_STEPS:
            if not self.steps.get(arg, True):
                cmd_parts.append(f"--skip-{arg}")
        
        # Add backend flag
        backend = self.settings['pipeline']['selected_backend']
        backend_flags = {
            'multi-model': '--multi-model',
            'geminipro-cli': '--geminipro-cli-summarize',
            'gemini-api': '--geminiapi-summarize',
            'gemini-selenium': '--gemini-selenium',
            'g4f': '--g4f-summarize',
            'free-gemini-api': '--free-gemini-summarize',
            'deeperseek': '--deeperseek-summarize',
            'perplexity': '--perplexity-summarize',
            'lmstudio-local': '--lmstudio-local-summarize',
            'lmstudio-remote': '--lmstudio-remote-summarize'
        }
        if backend in backend_flags:
            cmd_parts.append(backend_flags[backend])
        
        # Add push flag
        if self.settings['pipeline']['push_enabled']:
            cmd_parts.append("--push")
        
        return " ".join(cmd_parts)
    
    # =========== MAIN LOOP ===========
    def run(self):
        """Main menu loop."""
        while True:
            self.show_main_menu()
            choice = input("\nSelect option: ").strip().upper()
            
            if choice == '0':
                save_settings(self.settings)
                print("\n✓ Settings saved. Exiting...")
                return None
            elif choice == '1':
                # Enable all steps
                for key in self.steps:
                    self.steps[key] = True
                self._save_steps()
                save_settings(self.settings)
                return self.build_command()
            elif choice == '2':
                self.run_steps_menu()
            elif choice == '3':
                # Summarizer backend selection (includes filtering)
                self.run_backend_menu()
            elif choice == '4':
                # Auxiliary backend selection (weather, piacok, tts)
                self.run_auxiliary_backend_menu()
            elif choice == '5':
                self.run_workers_menu()
            elif choice == '6':
                self.settings['pipeline']['push_enabled'] = not self.settings['pipeline']['push_enabled']
                save_settings(self.settings)
            elif choice == '7':
                self.run_backend_settings_menu()
            elif choice == '8':
                self.run_global_settings()
            elif choice == '9':
                self.run_maintenance_menu()
            elif choice == 'S':
                self._save_steps()
                save_settings(self.settings)
                return self.build_command()
            elif choice == 'P':
                print(f"\nCommand: {self.build_command()}")
                input("\nPress Enter to continue...")
            elif choice == 'H':
                self.show_detailed_history_stats()
    
    def run_backend_settings_menu(self):
        while True:
            self.show_backend_settings_menu()
            choice = input("\nSelect option: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                self.run_multi_model_settings()
            elif choice == '2':
                self.run_g4f_settings()
            elif choice == '3':
                self.run_gemini_settings()
            elif choice == '4':
                self.run_perplexity_settings()
            elif choice == '5':
                self.run_deeperseek_settings()

    # =========== MAINTENANCE TOOLS MENU ===========
    def show_maintenance_menu(self):
        self.clear_screen()
        self.print_header("Maintenance Tools")
        
        for i, (script, name, description) in enumerate(MAINTENANCE_TOOLS, 1):
            self.print_menu_item(i, f"{name}")
            print(f"     └─ {description}")
        
        self.print_separator()
        self.print_menu_item(0, "← Back to main menu")
        self.print_footer()
    
    def run_maintenance_menu(self):
        import subprocess
        while True:
            self.show_maintenance_menu()
            choice = input("\nSelect tool to run: ").strip()
            
            if choice == '0':
                break
            elif choice.isdigit() and 0 < int(choice) <= len(MAINTENANCE_TOOLS):
                script, name, _ = MAINTENANCE_TOOLS[int(choice) - 1]
                print(f"\n🔧 Running {name}...")
                try:
                    result = subprocess.run(
                        [sys.executable, script],
                        cwd=os.path.dirname(__file__),
                        capture_output=False
                    )
                    if result.returncode == 0:
                        print(f"\n✅ {name} completed successfully!")
                    else:
                        print(f"\n⚠️ {name} finished with code {result.returncode}")
                except Exception as e:
                    print(f"\n❌ Error running {name}: {e}")
                input("\nPress Enter to continue...")
    
    # =========== HISTORY STATS ===========
    def show_detailed_history_stats(self):
        self.clear_screen()
        self.print_header("History Statistics")
        
        try:
            from history_manager import HistoryManager
            history = HistoryManager()
            stats = history.get_stats()
            
            print(f"  📊 Total Links in History:    {stats['total_links']}")
            print(f"  ✅ Summarized:                {stats['summarized']}")
            print(f"  👍 Positive/Neutral:          {stats['positive_neutral']}")
            print(f"  🚫 Filtered:                  {stats['filtered']}")
            print(f"  ❌ Negative:                  {stats['negative']}")
            
            # Calculate success rate
            if stats['total_links'] > 0:
                success_rate = (stats['summarized'] / stats['total_links']) * 100
                print(f"\n  📈 Success Rate:              {success_rate:.1f}%")
            
            self.print_separator()
            print("  💡 Tips:")
            print("  - Use 'Retry Failed Links' to reprocess errors")
            print("  - Use 'Clear Negative History' to unblock links")
            
        except Exception as e:
            print(f"  ❌ Error loading history: {e}")
        
        self.print_footer()
        input("\nPress Enter to continue...")


def run_menu():
    """Run the interactive menu and return the command to execute."""
    menu = PipelineMenu()
    return menu.run()


def get_settings():
    """Get current settings (for use by other modules)."""
    return load_settings()


if __name__ == "__main__":
    cmd = run_menu()
    if cmd:
        print(f"\nExecuting: {cmd}")
        import subprocess
        subprocess.run(cmd, shell=True)
