import time
import subprocess
import datetime
import os
import sys
import signal

# Configuration
PIPELINE_SCRIPT = "run_pipeline.py"
TTS_SCRIPT = "run_tts_daily.py"
REPORT_SCRIPT = "pipeline_reporter.py"
PIPELINE_RUN_TIMES = ["06:00", "18:00"]

# Per-slot pipeline override: time slot -> (working_dir, script_filename).
# The 06:00 slot runs the more-parallel ScraperHU2 build (run_pipeline2.py);
# every other slot runs the default run_pipeline.py from this directory.
# The two share the same Output/ and the same .pipeline.lock, so they can never
# run concurrently. The LOGICAL job name stays PIPELINE_SCRIPT everywhere else.
SH2_DIR = "/Volumes/Apple/DerűsProjekt/ScraperHU2"
PIPELINE_SLOT_TARGETS = {
    "06:00": (SH2_DIR, "run_pipeline2.py"),
}
# All filenames that count as "a pipeline run" for finished-detection.
PIPELINE_SCRIPT_NAMES = ("run_pipeline.py", "run_pipeline2.py")
# Report is no longer time-scheduled — it runs automatically after every
# pipeline run (see the pipeline-finished handling in main()).
REPORT_RUN_TIMES = []
CHECK_INTERVAL = 30  # seconds
MAX_PIPELINE_RUNTIME = 90 * 60  # 90 minutes — hard limit for any pipeline run
IDLE_THRESHOLD = 300  # seconds of no user input required before auto-sleep
WAKE_LEAD = 120  # seconds to wake before the next scheduled slot

current_process = None
current_report_process = None  # separate track for report script (never killed by watchdog)

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

def run_script_process(script_name, slot=None):
    global current_process, _process_start_time
    stop_previous_process()

    # Resolve the actual working dir + script for this run. For the pipeline, a
    # per-slot override (e.g. 06:00 -> ScraperHU2/run_pipeline2.py) may apply;
    # everything else runs from this scheduler's own directory.
    work_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = script_name
    if script_name == PIPELINE_SCRIPT and slot in PIPELINE_SLOT_TARGETS:
        work_dir, script_path = PIPELINE_SLOT_TARGETS[slot]
        log(f"🚀 Starting script: {script_path} (slot {slot}, cwd={work_dir})")
    else:
        log(f"🚀 Starting script: {script_path}")

    # Run unbuffered output
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        cmd = [sys.executable, script_path]

        # If running as root via sudo, downgrade script execution to original user
        if os.environ.get('SUDO_USER'):
            orig_user = os.environ['SUDO_USER']
            log(f"Running script as user: {orig_user}")
            cmd = ["sudo", "-u", orig_user] + cmd

        current_process = subprocess.Popen(
            cmd,
            cwd=work_dir,
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

def run_report_process():
    """Run the report generator as a separate short-lived process (no watchdog)."""
    global current_report_process
    if current_report_process and current_report_process.poll() is None:
        log("⏭️ Report already running, skipping.")
        return
    log(f"📊 Starting report generator: {REPORT_SCRIPT}")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        cmd = [sys.executable, REPORT_SCRIPT]
        current_report_process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        log(f"✅ Report process started with PID {current_report_process.pid}")
    except Exception as e:
        log(f"❌ Failed to start report: {e}")

def get_all_jobs_for_today(now):
    """Returns a list of tuples: (datetime_target, script_name, time_str) for today."""
    jobs = []
    for t_str in PIPELINE_RUN_TIMES:
        h, m = map(int, t_str.split(':'))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        jobs.append((target, PIPELINE_SCRIPT, t_str))

    for t_str in REPORT_RUN_TIMES:
        h, m = map(int, t_str.split(':'))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        jobs.append((target, REPORT_SCRIPT, t_str))

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
    """Schedules a system wake event using pmset (macOS only). Returns True on success."""
    if not target_dt:
        return False

    # Format: "MM/dd/yyyy HH:mm:ss"
    time_str = target_dt.strftime("%m/%d/%Y %H:%M:%S")

    cmd = ["sudo", "-n", "/usr/bin/pmset", "schedule", "wake", time_str]

    try:
        # Precheck must be pmset-scoped: the sudoers rule grants NOPASSWD only for
        # /usr/bin/pmset, so a generic `sudo -n true` would still prompt and mislead us.
        check = subprocess.run(["sudo", "-n", "/usr/bin/pmset", "-g", "sched"], capture_output=True)
        if check.returncode != 0:
            log(f"⚠️ Cannot schedule auto-wake (passwordless sudo for pmset required).")
            return False

        log(f"⏰ Scheduling system wake for {time_str}...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            log(f"✅ Wake scheduled successfully.")
            return True
        else:
            log(f"❌ Failed to schedule wake: {result.stderr.strip()}")
            return False

    except Exception as e:
        log(f"❌ Error scheduling wake: {e}")
        return False


def system_idle_seconds():
    """Seconds since last user HID input (keyboard/mouse). 0.0 on error (fail safe: don't sleep)."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True
        ).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                ns = int(line.split("=")[-1].strip())
                return ns / 1_000_000_000.0
    except Exception as e:
        log(f"⚠️ Could not read idle time: {e}")
    return 0.0


def maybe_sleep_until_next(now):
    """Single decision point: if idle long enough, schedule the next wake and sleep.
    Never sleeps unless a wake was successfully scheduled (safety: avoid sleeping with no wake)."""
    next_run = get_next_run_time()
    if not next_run:
        return
    if (next_run - now).total_seconds() < WAKE_LEAD + 60:
        return  # next slot is too close — stay awake and let it trigger

    idle = system_idle_seconds()
    if idle < IDLE_THRESHOLD:
        log(f"⏸️ User active ({idle:.0f}s idle < {IDLE_THRESHOLD}s) — deferring sleep.")
        return

    if schedule_wake(next_run - datetime.timedelta(seconds=WAKE_LEAD)):
        log(f"💤 Idle {idle:.0f}s — sleeping until wake before {next_run.strftime('%Y-%m-%d %H:%M:%S')}.")
        subprocess.run(["pmset", "sleepnow"])
    else:
        log(f"⚠️ Wake could not be scheduled — NOT sleeping (staying awake to avoid missing a run).")

def main():
    global current_process, current_report_process
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
        TTS_SCRIPT: ("", None),
        REPORT_SCRIPT: ("", None),
    }

    for script, (target, t_str) in latest_missed.items():
        # Check if the output for today already exists (don't re-run unnecessarily)
        today_str = now.strftime('%Y-%m-%d')
        data_json = os.path.join('Output', today_str, 'data.json')

        skip = False
        if script == PIPELINE_SCRIPT and os.path.exists(data_json):
            skip = True
            log(f"  ⏩ Skipping catch-up for {script} (data.json already exists for {today_str})")

        if script == REPORT_SCRIPT:
            # Never skip report catch-up — it's fast and always useful
            pass

        if script == TTS_SCRIPT:
            tts_weather = os.path.join('Output', today_str, 'tts_weather_market.txt')
            tts_headlines = os.path.join('Output', today_str, 'tts_headlines.txt')
            if os.path.exists(tts_weather) or os.path.exists(tts_headlines):
                skip = True
                log(f"  ⏩ Skipping catch-up for {script} (TTS output files already exist for {today_str})")

        if not skip:
            log(f"⏰ Startup catch-up: missed {t_str} run for {script}. Triggering now.")
            if script == REPORT_SCRIPT:
                run_report_process()
                last_run_jobs[script] = (t_str, now.date())
            else:
                run_script_process(script, slot=t_str)
                last_run_jobs[script] = (t_str, now.date())
                # Wait for pipeline processes to finish before starting the next catch-up
                if current_process:
                    log(f"  ⏳ Waiting for {script} to complete before next catch-up...")
                    current_process.wait()
                    log(f"  ✅ {script} finished with code {current_process.returncode}")
                    current_process = None
                    # Report after every pipeline run
                    if script == PIPELINE_SCRIPT:
                        run_report_process()
    
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
                if script == REPORT_SCRIPT:
                    run_report_process()
                else:
                    run_script_process(script, slot=t_str)
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
                         if script == REPORT_SCRIPT:
                             run_report_process()
                         else:
                             run_script_process(script, slot=t_str)
                         last_run_jobs[script] = (t_str, now.date())

        last_check_time = now

        # Check if report process finished (just log it)
        if current_report_process and current_report_process.poll() is not None:
            log(f"📊 Report process finished with code {current_report_process.returncode}.")
            current_report_process = None

        # Check if pipeline process finished
        if current_process and current_process.poll() is not None:
            exit_code = current_process.returncode
            finished_args = current_process.args
            was_pipeline = any(name in finished_args for name in PIPELINE_SCRIPT_NAMES)
            log(f"ℹ️ {finished_args[1] if len(finished_args) > 1 else 'Script process'} finished with code {exit_code}.")
            current_process = None
            _process_start_time = None

            recovery_started = False
            # Exit code 75 (LOCK_BUSY_EXIT_CODE / EX_TEMPFAIL) means the run was
            # SKIPPED because another instance held the single-instance lock. This
            # is EXPECTED, not a failure: do NOT trigger a recovery run.
            if exit_code == 75:
                log("⚠️ WARNING: pipeline skipped (lock held) — no recovery. "
                    "Another instance is already running.")
            # Trigger recovery run if pipeline exited with errors
            elif exit_code != 0:
                today_str = now.strftime('%Y-%m-%d')
                data_json = os.path.join('Output', today_str, 'data.json')
                if os.path.exists(data_json):
                    log(f"🔧 Pipeline failed (code {exit_code}) but data.json exists — triggering recovery run to patch missing files...")
                    run_script_process(PIPELINE_SCRIPT)
                    recovery_started = True

            # Report after every pipeline run. Recovery runs trigger their own
            # report when they finish, so skip while a recovery is starting.
            if was_pipeline and not recovery_started:
                log("📊 Pipeline run finished — generating report...")
                run_report_process()

            # Sleep + next-wake are handled centrally at the end of the loop
            # (maybe_sleep_until_next), unless a recovery run was just started.

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
                # After watchdog kill: immediately trigger recovery if data.json exists
                today_str = now.strftime('%Y-%m-%d')
                data_json = os.path.join('Output', today_str, 'data.json')
                if os.path.exists(data_json):
                    log(f"🔧 Watchdog terminated pipeline — triggering recovery run to patch missing derived files...")
                    run_script_process(PIPELINE_SCRIPT)
                else:
                    log("📊 Watchdog terminated pipeline — generating report...")
                    run_report_process()

        # Heartbeat every ~30 minutes (60 checks * 30s)
        heartbeat_counter += 1
        if heartbeat_counter >= 60:
            log(f"💓 Scheduler is active. Current time: {current_time_str}. Next check in {CHECK_INTERVAL}s.")
            heartbeat_counter = 0

        # Central sleep decision: if nothing is running and the machine is idle,
        # schedule a wake for the next slot and sleep. Never sleeps without a wake.
        if current_process is None and current_report_process is None:
            maybe_sleep_until_next(now)

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
