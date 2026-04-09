import time
import subprocess
import datetime
import os
import sys
import signal

# Configuration
PIPELINE_SCRIPT = "run_pipeline.py"
TTS_SCRIPT = "run_tts_daily.py"
PIPELINE_RUN_TIMES = ["00:00", "06:00", "12:00", "18:00"]
CHECK_INTERVAL = 30  # seconds
MAX_PIPELINE_RUNTIME = 90 * 60  # 90 minutes — hard limit for any pipeline run

current_process = None

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

def stop_previous_process():
    global current_process
    if current_process:
        if current_process.poll() is None:  # Still running
            log(f"⚠️ Previous process (PID {current_process.pid}) is still running. Terminating...")
            try:
                current_process.terminate()
                current_process.wait(timeout=10)
                log(f"✅ Process terminated.")
            except subprocess.TimeoutExpired:
                log(f"⚠️ Process did not terminate in time. Killing...")
                current_process.kill()
                current_process.wait()
                log(f"✅ Process killed.")
        else:
            log(f"ℹ️ Previous process finished with code {current_process.returncode}.")
        current_process = None

def run_script_process(script_name):
    global current_process, _process_start_time
    stop_previous_process()
    
    log(f"🚀 Starting script: {script_name}")
    
    # Run unbuffered output
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    try:
        cmd = [sys.executable, script_name]
        
        # If running as root via sudo, downgrade script execution to original user
        if os.environ.get('SUDO_USER'):
            orig_user = os.environ['SUDO_USER']
            log(f"Running script as user: {orig_user}")
            cmd = ["sudo", "-u", orig_user] + cmd
            
        current_process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        _process_start_time = time.time()
        log(f"✅ Script started with PID {current_process.pid}")
    except Exception as e:
        log(f"❌ Failed to start script: {e}")

def get_tts_target_for_date(date_obj):
    """Returns the exact TTS datetime target for a given date."""
    return date_obj.replace(hour=9, minute=0, second=0, microsecond=0)

def get_all_jobs_for_today(now):
    """Returns a list of tuples: (datetime_target, script_name, time_str) for today."""
    jobs = []
    for t_str in PIPELINE_RUN_TIMES:
        h, m = map(int, t_str.split(':'))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        jobs.append((target, PIPELINE_SCRIPT, t_str))
        
    tts_target = get_tts_target_for_date(now)
    jobs.append((tts_target, TTS_SCRIPT, tts_target.strftime("%H:%M")))
    
    return jobs

def get_next_run_time():
    """Calculates the next scheduled run globally."""
    now = datetime.datetime.now()
    next_runs = []
    
    # Get today's jobs and tomorrow's jobs
    all_jobs = get_all_jobs_for_today(now) + get_all_jobs_for_today(now + datetime.timedelta(days=1))
    
    for target, script, t_str in all_jobs:
        if target > now:
            next_runs.append((target, script))
            
    next_runs.sort(key=lambda x: x[0])
    return next_runs[0][0] if next_runs else None

def schedule_wake(target_dt):
    """Schedules a system wake event using pmset (macOS only)."""
    if not target_dt: return
    
    # Format: "MM/dd/yyyy HH:mm:ss"
    time_str = target_dt.strftime("%m/%d/%Y %H:%M:%S")
    
    cmd = ["sudo", "pmset", "schedule", "wake", time_str]
    
    try:
        check = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if check.returncode != 0:
            log(f"⚠️ Cannot schedule auto-wake (sudo required). Run script with 'sudo' to enable.")
            return

        log(f"⏰ Scheduling system wake for {time_str}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            log(f"✅ Wake scheduled successfully.")
        else:
            log(f"❌ Failed to schedule wake: {result.stderr.strip()}")
            
    except Exception as e:
        log(f"❌ Error scheduling wake: {e}")

def main():
    global current_process
    log("=== News Pipeline & TTS Scheduler Started ===")
    log(f"Pipeline times: {', '.join(PIPELINE_RUN_TIMES)}")
    log(f"TTS times: 09:00 (Daily)")
    
    # Initial Schedule
    next_run = get_next_run_time()
    if next_run:
        diff = (next_run - datetime.datetime.now()).total_seconds()
        log(f"📅 Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {diff/3600:.1f} hours)")
        schedule_wake(next_run)
    
    # Startup catch-up: check if we missed any jobs earlier today
    now = datetime.datetime.now()
    jobs_today = get_all_jobs_for_today(now)
    latest_missed = {}  # script -> (target, t_str) - keep only the LATEST missed job per script
    for target, script, t_str in jobs_today:
        if target <= now:
            latest_missed[script] = (target, t_str)
    
    last_run_jobs = {
        PIPELINE_SCRIPT: ("", None),
        TTS_SCRIPT: ("", None)
    }
    
    for script, (target, t_str) in latest_missed.items():
        # Check if the output for today already exists (don't re-run unnecessarily)
        today_str = now.strftime('%Y-%m-%d')
        data_json = os.path.join('Output', today_str, 'data.json')
        
        skip = False
        if script == PIPELINE_SCRIPT and os.path.exists(data_json):
            skip = True
            log(f"  ⏩ Skipping catch-up for {script} (data.json already exists for {today_str})")
        
        if not skip:
            log(f"⏰ Startup catch-up: missed {t_str} run for {script}. Triggering now.")
            run_script_process(script)
            last_run_jobs[script] = (t_str, now.date())
            # Wait for this process to finish before starting the next catch-up
            if current_process:
                log(f"  ⏳ Waiting for {script} to complete before next catch-up...")
                current_process.wait()
                log(f"  ✅ {script} finished with code {current_process.returncode}")
                current_process = None
    
    _process_start_time = None
    heartbeat_counter = 0
    last_check_time = datetime.datetime.now()
    
    while True:
        now = datetime.datetime.now()
        current_time_str = now.strftime("%H:%M")
        
        # 1. Exact match checking for all jobs today
        jobs_today = get_all_jobs_for_today(now)
        
        for target, script, t_str in jobs_today:
            # Check for exact trigger match
            last_time, last_date = last_run_jobs.get(script, ("", None))
            if current_time_str == t_str and (last_time != t_str or last_date != now.date()):
                log(f"⏰ It is {current_time_str}. Triggering scheduled run for {script}.")
                run_script_process(script)
                last_run_jobs[script] = (t_str, now.date())
        
        # 2. Catch-up for sleep (if we jumped more than 1 minute)
        if (now - last_check_time).total_seconds() > 65:
             # We slept/jumped. Check if we missed a time.
             for target, script, t_str in jobs_today:
                 # if target is between last_check and now
                 if last_check_time < target <= now:
                     # Check if we already ran it (unlikely if we jumped)
                     last_time, last_date = last_run_jobs.get(script, ("", None))
                     if last_time != t_str or last_date != now.date():
                         log(f"⏰ Woke up from sleep/jump. Missed scheduled run at {t_str} for {script}. Triggering now.")
                         run_script_process(script)
                         last_run_jobs[script] = (t_str, now.date())
                         
        last_check_time = now
        
        # Check if process finished
        if current_process and current_process.poll() is not None:
            log(f"ℹ️ {current_process.args[1] if len(current_process.args) > 1 else 'Script process'} finished with code {current_process.returncode}.")
            current_process = None
            _process_start_time = None

            # Schedule next wake
            next_run = get_next_run_time()
            if next_run:
                diff = (next_run - datetime.datetime.now()).total_seconds()
                log(f"📅 Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {diff/3600:.1f} hours)")
                schedule_wake(next_run)
        
        # Watchdog: kill pipeline if it exceeds MAX_PIPELINE_RUNTIME
        if current_process and _process_start_time:
            elapsed = time.time() - _process_start_time
            if elapsed > MAX_PIPELINE_RUNTIME:
                script_name = current_process.args[1] if len(current_process.args) > 1 else 'Script process'
                log(f"🚨 WATCHDOG: {script_name} exceeded {MAX_PIPELINE_RUNTIME//60} min runtime ({elapsed//60:.0f} min). Terminating...")
                try:
                    current_process.terminate()
                    current_process.wait(timeout=15)
                    log(f"✅ Process terminated by watchdog.")
                except subprocess.TimeoutExpired:
                    log(f"⚠️ Process did not terminate. Force killing...")
                    current_process.kill()
                    current_process.wait()
                    log(f"✅ Process killed by watchdog.")
                except Exception as e:
                    log(f"❌ Watchdog error: {e}")
                current_process = None
                _process_start_time = None

        # Heartbeat every ~30 minutes (60 checks * 30s)
        heartbeat_counter += 1
        if heartbeat_counter >= 60:
            log(f"💓 Scheduler is active. Current time: {current_time_str}. Next check in {CHECK_INTERVAL}s.")
            heartbeat_counter = 0

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        log("\n🛑 Scheduler stopping...")
        stop_previous_process()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    main()
