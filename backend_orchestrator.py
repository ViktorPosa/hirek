"""
LEÍRÁS:
Backend Orchestrator: Párhuzamos multi-modell feldolgozás vezérlése.
Kezeli a különböző AI backendeket (Gemini, Perplexity, DeeperSeek, G4F), biztosítja az automatikus hibatűrést (failover)
és az exponenciális visszalépést (backoff) hiba esetén.

BEMENET:
- Promptok és feldolgozandó adatok a hívó szkriptektől (pl. news_filter, summarizer)
- Backend egészségi állapot (Input/backend_health.json)
- Beállítások (Input/pipeline_settings.json)

KIMENET:
- AI válaszok (szöveg, JSON)
- Frissített egészségi állapot fájl
"""


import os
import json
import time
import threading
import concurrent.futures
from datetime import datetime, timedelta
from threading import Lock
from pipeline_logger import log_pipeline_error

# State file path
HEALTH_FILE = os.path.join(os.path.dirname(__file__), 'Input', 'backend_health.json')

# Backoff times in minutes
BACKOFF_TIMES = [5, 10, 15, 30, 60, 120]

class BackendOrchestrator:
    """
    Orchestrates multiple AI backends with automatic failover.
    
    Usage:
        orchestrator = BackendOrchestrator()
        orchestrator.register("g4f", g4f_client)
        orchestrator.register("gemini_api", gemini_client)
        
        result, model = orchestrator.call_with_failover(prompt)
    """
    
    def __init__(self):
        self.backends = {}  # name -> client module
        self.health = {}    # name -> {"status", "failures", "suspended_until"}
        self.lock = Lock()
        self.load_health_state()
    
    def load_health_state(self):
        """Load backend health state from file."""
        if os.path.exists(HEALTH_FILE):
            try:
                with open(HEALTH_FILE, 'r') as f:
                    self.health = json.load(f)
                # Clean expired suspensions
                now = datetime.now()
                for name, state in self.health.items():
                    if state.get('suspended_until'):
                        suspended_until = datetime.fromisoformat(state['suspended_until'])
                        if now >= suspended_until:
                            state['status'] = 'healthy'
                            state['suspended_until'] = None
            except Exception as e:
                print(f"  [Orchestrator] Warning: Could not load health state: {e}")
                self.health = {}
    
    def save_health_state(self):
        """Save backend health state to file."""
        try:
            os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
            with open(HEALTH_FILE, 'w') as f:
                json.dump(self.health, f, indent=2, default=str)
        except Exception as e:
            print(f"  [Orchestrator] Warning: Could not save health state: {e}")
    
    def register(self, name, client_module, check_fn=None):
        """
        Register a backend client.
        
        Args:
            name: Unique backend name
            client_module: Module with call_with_fallback(prompt) function
            check_fn: Optional function to check if backend is available
        """
        self.backends[name] = {
            'client': client_module,
            'check': check_fn or getattr(client_module, 'check_cookies', lambda: True)
        }
        
        if name not in self.health:
            self.health[name] = {
                'status': 'healthy',
                'failures': 0,
                'suspended_until': None,
                'last_success': None
            }
        
        print(f"  [Orchestrator] Registered backend: {name}")
    
    def get_available_backends(self):
        """Return list of currently available (non-suspended) backends."""
        available = []
        now = datetime.now()
        
        for name, backend in self.backends.items():
            state = self.health.get(name, {})
            
            # Check if suspended
            suspended_until = state.get('suspended_until')
            if suspended_until:
                suspended_until = datetime.fromisoformat(suspended_until) if isinstance(suspended_until, str) else suspended_until
                if now < suspended_until:
                    remaining = (suspended_until - now).total_seconds() / 60
                    print(f"    [{name}] Suspended ({remaining:.0f} min remaining)")
                    continue
                else:
                    # Suspension expired, reset
                    state['status'] = 'healthy'
                    state['suspended_until'] = None
            
            # Check if backend is available
            check_fn = backend.get('check', lambda: True)
            try:
                if check_fn():
                    available.append(name)
                else:
                    print(f"    [{name}] Not available (check failed)")
            except Exception as e:
                print(f"    [{name}] Check error: {e}")
        
        return available
    
    def report_success(self, backend_name):
        """Report successful call - reset failure count."""
        with self.lock:
            if backend_name in self.health:
                self.health[backend_name]['failures'] = 0
                self.health[backend_name]['status'] = 'healthy'
                self.health[backend_name]['suspended_until'] = None
                self.health[backend_name]['last_success'] = datetime.now().isoformat()
            self.save_health_state()
    
    def report_failure(self, backend_name):
        """Report failed call - apply exponential backoff."""
        with self.lock:
            if backend_name not in self.health:
                self.health[backend_name] = {'failures': 0, 'status': 'healthy', 'suspended_until': None}
            
            state = self.health[backend_name]
            state['failures'] = state.get('failures', 0) + 1
            
            # Calculate backoff time
            failure_idx = min(state['failures'] - 1, len(BACKOFF_TIMES) - 1)
            backoff_minutes = BACKOFF_TIMES[failure_idx]
            
            # Apply suspension
            suspended_until = datetime.now() + timedelta(minutes=backoff_minutes)
            state['suspended_until'] = suspended_until.isoformat()
            state['status'] = 'suspended'
            
            print(f"    [{backend_name}] Failed ({state['failures']} failures) - suspended for {backoff_minutes} min")
            
            self.save_health_state()
            return backoff_minutes
    
    # Selenium-based backends need longer timeouts because browser automation is slow (5-8 min per response)
    SELENIUM_BACKENDS = {'gemini_selenium', 'gemini_selenium_pro'}
    SELENIUM_TIMEOUT = 540  # 9 min — must be > gemini_selenium_client.RESPONSE_WAIT_TIMEOUT (480s)
    DEFAULT_TIMEOUT = 180   # 3 min — for API-based backends

    def call_single(self, backend_name, prompt, use_pro=False, timeout=None):
        """
        Call a single backend with timeout protection.
        Selenium backends automatically get a longer timeout (540s) to allow
        for browser response generation (typically 5-8 min).
        
        Returns:
            (content, model_name, success) tuple
        """
        if backend_name not in self.backends:
            return None, None, False
        
        # Auto-select appropriate timeout based on backend type
        if timeout is None:
            timeout = self.SELENIUM_TIMEOUT if backend_name in self.SELENIUM_BACKENDS else self.DEFAULT_TIMEOUT
        
        backend = self.backends[backend_name]
        client = backend['client']
        
        def _do_call():
            return client.call_with_fallback(prompt, use_pro=use_pro)
        
        try:
            # Use ThreadPoolExecutor with timeout to prevent indefinite hangs
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_call)
                try:
                    content, model = future.result(timeout=timeout)
                    if content:
                        self.report_success(backend_name)
                        return content, model, True
                    else:
                        self.report_failure(backend_name)
                        return None, None, False
                except concurrent.futures.TimeoutError:
                    print(f"    [{backend_name}] ⏰ TIMEOUT ({timeout}s) - skipping to next backend")
                    log_pipeline_error(f"Backend_Orchestrator: [{backend_name}] API TIMEOUT ({timeout}s)")
                    self.report_failure(backend_name)
                    return None, None, False
        except Exception as e:
            print(f"    [{backend_name}] 💥 Exception: {e}")
            log_pipeline_error(f"Backend_Orchestrator: [{backend_name}] Exception: {e}")
            self.report_failure(backend_name)
            return None, None, False
    
    def call_with_failover(self, prompt, use_pro=False, preferred_backend=None):
        """
        Call backends with automatic failover.
        Tries preferred backend first, then others.
        
        Returns:
            (content, model_name) tuple
        """
        available = self.get_available_backends()
        
        if not available:
            print("  [Orchestrator] No backends available!")
            return None, None
        
        # Order: preferred first, then by last success
        order = []
        if preferred_backend and preferred_backend in available:
            order.append(preferred_backend)
            available.remove(preferred_backend)
        order.extend(available)
        
        print(f"  [Orchestrator] Trying backends: {order}")
        
        for backend_name in order:
            print(f"    Trying {backend_name}...")
            content, model, success = self.call_single(backend_name, prompt, use_pro)
            
            if success:
                print(f"    ✓ {backend_name} succeeded")
                return content, model
            else:
                print(f"    ✗ {backend_name} failed")
        
        print("  [Orchestrator] All backends failed!")
        log_pipeline_error("Backend_Orchestrator: All backends failed or exhausted during failover!")
        return None, None
    
    def call_parallel(self, prompt, use_pro=False, max_workers=3):
        """
        Call multiple backends in parallel, return first success.
        Uses a shared cancel_event to signal other threads to stop.
        
        Returns:
            (content, model_name) tuple
        """
        available = self.get_available_backends()
        
        if not available:
            print("  [Orchestrator] No backends available!")
            return None, None
        
        print(f"  [Orchestrator] Parallel call to: {available}")
        
        cancel_event = threading.Event()
        
        def call_backend(backend_name):
            if cancel_event.is_set():
                return (backend_name, None, None, False)
            return (backend_name, *self.call_single(backend_name, prompt, use_pro))
        
        result_content, result_model = None, None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(available))) as executor:
            futures = {executor.submit(call_backend, name): name for name in available}
            
            for future in concurrent.futures.as_completed(futures):
                backend_name, content, model, success = future.result()
                
                if success and result_content is None:
                    result_content, result_model = content, model
                    # Signal all other threads to stop
                    cancel_event.set()
                    # Cancel not-yet-started futures
                    for f in futures:
                        f.cancel()
                    print(f"    ✓ First success from {backend_name}")
        
        if result_content is None:
            print("  [Orchestrator] All parallel calls failed!")
        return result_content, result_model
    
    def call_all_parallel(self, prompt, use_pro=False):
        """
        Call ALL available backends in parallel, collect all results.
        
        Returns:
            List of (backend_name, content, model_name, success) tuples
        """
        available = self.get_available_backends()
        
        if not available:
            return []
        
        print(f"  [Orchestrator] Calling all backends: {available}")
        
        def call_backend(backend_name):
            return (backend_name, *self.call_single(backend_name, prompt, use_pro))
        
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(available)) as executor:
            futures = [executor.submit(call_backend, name) for name in available]
            
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        
        return results
    
    def get_status(self):
        """Return current health status of all backends."""
        status = {}
        now = datetime.now()
        
        for name in self.backends:
            state = self.health.get(name, {})
            suspended_until = state.get('suspended_until')
            
            if suspended_until:
                suspended_until = datetime.fromisoformat(suspended_until) if isinstance(suspended_until, str) else suspended_until
                if now < suspended_until:
                    remaining = (suspended_until - now).total_seconds() / 60
                    status[name] = f"suspended ({remaining:.0f} min)"
                else:
                    status[name] = "healthy"
            else:
                status[name] = state.get('status', 'unknown')
            
            failures = state.get('failures', 0)
            if failures > 0:
                status[name] += f" (failures: {failures})"
        
        return status
    
    def reset_all(self):
        """Reset all backends to healthy state."""
        for name in self.health:
            self.health[name] = {
                'status': 'healthy',
                'failures': 0,
                'suspended_until': None
            }
        self.save_health_state()
        print("  [Orchestrator] All backends reset to healthy")
    
    def process_batches_parallel(self, batches, batch_processor, use_pro=False, mode="race"):
        """
        Process multiple batches in parallel using all available backends.
        
        Args:
            batches: List of batch items to process
            batch_processor: Function(batch, backend_name, client) -> (results, model)
            use_pro: Whether to use pro mode
            mode: "race" (workers grab from shared queue) or "balanced" (round-robin assignment)
        
        Returns:
            List of (batch_index, results, model) tuples
        """
        available = self.get_available_backends()
        
        if not available:
            print("  [Orchestrator] No backends available!")
            return []
        
        print(f"  [Orchestrator] Parallel batch processing with {len(available)} workers")
        print(f"  [Orchestrator] Backends: {', '.join(available)}")
        print(f"  [Orchestrator] Mode: {mode}")
        
        # Thread-safe work queue and results
        from queue import Queue
        from threading import Thread, Lock
        
        results_list = []
        results_lock = Lock()
        
        # Create separate queues for balanced mode, or one shared for race
        queues = {}
        if mode == "balanced":
            # Round-robin assignment
            for name in available:
                queues[name] = Queue()
                
            for i, batch in enumerate(batches):
                target_backend = available[i % len(available)]
                queues[target_backend].put((i, batch))
                # print(f"    [Orchestrator] Assigned batch {i} to {target_backend}")
        else:
            # Race execution - shared queue
            shared_queue = Queue()
            for i, batch in enumerate(batches):
                shared_queue.put((i, batch))
            for name in available:
                queues[name] = shared_queue
        
        def worker(backend_name):
            """Worker thread - processes batches from assigned queue."""
            client = self.backends[backend_name]['client']
            my_queue = queues[backend_name]
            
            while not my_queue.empty():
                try:
                    batch_idx, batch = my_queue.get_nowait()
                except:
                    break
                
                try:
                    result, model = batch_processor(batch, backend_name, client)
                    
                    if result is not None:
                        self.report_success(backend_name)
                        with results_lock:
                            results_list.append((batch_idx, result, model or backend_name))
                    else:
                        self.report_failure(backend_name)
                        print(f"    [{backend_name}] Batch {batch_idx} failed")
                        
                        # In balanced mode, if one fails, we should ideally reassign to others
                        # But simplistic approach: put back to queue? 
                        # If balanced, put to specific queue? 
                        # Better: fallback to others.
                        # For now, let's just mark failed and maybe retry logic at top level will handle it?
                        # News filter defines retry loop? No.
                        # Let's try to pass it to another available backend if possible.
                        
                        # Simple retry mechanism:
                        # If failed, try to put into another randomly chosen backend's queue?
                        # Or just fail and let the outer loop handle "retrying failed items".
                        pass
                        
                except Exception as e:
                    print(f"    [{backend_name}] Error: {e}")
                    self.report_failure(backend_name)
                
                my_queue.task_done()
        
        # Start 1 worker thread per available backend
        threads = []
        for backend_name in available:
            t = Thread(target=worker, args=(backend_name,), daemon=True)
            t.start()
            threads.append(t)
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # Sort results by batch index
        results_list.sort(key=lambda x: x[0])
        
        print(f"  [Orchestrator] Completed {len(results_list)}/{len(batches)} batches successfully")
        
        return results_list
    
    def print_status(self):
        """Print current backend status."""
        print("\n  === Backend Status ===")
        for name, state in self.get_status().items():
            print(f"    {name}: {state}")
        print()


# Global orchestrator instance
_orchestrator = None

def get_orchestrator():
    """Get or create the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BackendOrchestrator()
    return _orchestrator

def setup_default_backends():
    """Register all available backends with the orchestrator, respecting settings."""
    orchestrator = get_orchestrator()
    
    # Load settings to see which backends are enabled
    enabled_backends = None
    settings_path = os.path.join(os.path.dirname(__file__), 'Input', 'pipeline_settings.json')
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
                enabled_backends = settings.get('multi_model', {}).get('enabled_backends')
        except Exception as e:
            print(f"  [Orchestrator] Failed to load settings from {settings_path}: {e}")
            pass
            
    # Default to all if not specified
    if not enabled_backends:
        print("  [Orchestrator] ⚠️ Settings not found or empty. Using DEFAULT backends.")
        enabled_backends = ["g4f", "gemini_api", "geminipro", "perplexity", "deeperseek"]
    else:
        print("  [Orchestrator] ✅ Loaded enabled backends from settings.")
        
    print(f"  [Orchestrator] Enabled backends: {', '.join(enabled_backends)}")
    
    
    # Import and register backends
    if "g4f" in enabled_backends:
        try:
            import g4f_client
            orchestrator.register("g4f", g4f_client, g4f_client.check_cookies)
        except ImportError:
            print("  [Orchestrator] g4f_client not available")
    
    if "gemini_api" in enabled_backends:
        try:
            import gemini_api_client
            orchestrator.register("gemini_api", gemini_api_client)
        except ImportError:
            print("  [Orchestrator] gemini_api_client not available")
    
    if "geminipro" in enabled_backends:
        try:
            import geminipro_client
            orchestrator.register("geminipro", geminipro_client, geminipro_client.check_cookies)
        except ImportError:
            print("  [Orchestrator] geminipro_client not available")
    
    if "perplexity" in enabled_backends:
        try:
            import perplexity_client
            orchestrator.register("perplexity", perplexity_client, perplexity_client.check_cookies)
        except ImportError:
            print("  [Orchestrator] perplexity_client not available")
    
    if "deeperseek" in enabled_backends:
        try:
            import deeperseek_client
            orchestrator.register("deeperseek", deeperseek_client, deeperseek_client.check_cookies)
        except ImportError:
            print("  [Orchestrator] deeperseek_client not available")
            
    if "gemini-selenium" in enabled_backends:
        try:
            import gemini_selenium_client
            orchestrator.register("gemini_selenium", gemini_selenium_client, gemini_selenium_client.check_cookies)
            print("  [Orchestrator] gemini_selenium registered")
        except ImportError as e:
            print(f"  [Orchestrator] gemini_selenium_client not available: {e}")
            
    if "lmstudio-local" in enabled_backends:
        try:
            import lmstudio_client
            class LocalWrapper:
                 @staticmethod
                 def call_with_fallback(prompt, use_pro=False):
                     return lmstudio_client.call_lmstudio_local(prompt, use_pro)
                 @staticmethod
                 def check_cookies():
                     if lmstudio_client.is_disabled("local"):
                         return False  # 3-strike kizárás aktív
                     return lmstudio_client.check_local()
            orchestrator.register("lmstudio-local", LocalWrapper, LocalWrapper.check_cookies)
        except ImportError:
            print("  [Orchestrator] lmstudio_client not available")

    if "lmstudio-remote" in enabled_backends:
        try:
            import lmstudio_client
            class RemoteWrapper:
                 @staticmethod
                 def call_with_fallback(prompt, use_pro=False):
                     return lmstudio_client.call_lmstudio_remote(prompt, use_pro)
                 @staticmethod
                 def check_cookies():
                     if lmstudio_client.is_disabled("remote"):
                         return False  # 3-strike kizárás aktív
                     return lmstudio_client.check_remote()
            orchestrator.register("lmstudio-remote", RemoteWrapper, RemoteWrapper.check_cookies)
        except ImportError:
            print("  [Orchestrator] lmstudio_client not available")
    
    return orchestrator


if __name__ == "__main__":
    # Test the orchestrator
    print("Testing Backend Orchestrator...")
    
    orchestrator = setup_default_backends()
    orchestrator.print_status()
    
    # Test a simple call
    result, model = orchestrator.call_with_failover("Say hello in Hungarian in one word.")
    if result:
        print(f"\n✅ Success! Model: {model}")
        print(f"Response: {result[:100]}...")
    else:
        print("\n❌ All backends failed")
    
    orchestrator.print_status()
