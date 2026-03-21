import time
import datetime
import subprocess
import os
import sys

def run_tts():
    print(f"[{datetime.datetime.now()}] Running run_tts_daily.py...")
    try:
        # Use the same python executable that is running the scheduler
        subprocess.run([sys.executable, "run_tts_daily.py"], check=True)
        print(f"[{datetime.datetime.now()}] Successfully completed run_tts_daily.py")
    except subprocess.CalledProcessError as e:
        print(f"[{datetime.datetime.now()}] Error: run_tts_daily.py exited with non-zero status {e.returncode}")
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Unexpected error running run_tts_daily.py: {e}")

def get_next_run_time():
    now = datetime.datetime.now()
    
    # 0 = Monday, ..., 6 = Sunday
    is_weekend = now.weekday() >= 5
    
    # Set target time for today based on weekday/weekend
    if is_weekend:
        target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        target_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
        
    # If the target time for today has already passed, schedule for tomorrow
    if now >= target_time:
        next_day = now + datetime.timedelta(days=1)
        is_next_weekend = next_day.weekday() >= 5
        if is_next_weekend:
            target_time = next_day.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            target_time = next_day.replace(hour=8, minute=0, second=0, microsecond=0)
            
    return target_time

def main():
    print("==================================================")
    print("🎙️ Starting TTS Scheduler")
    print("   Weekdays: 08:00 AM")
    print("   Weekends: 09:00 AM")
    print("==================================================")
    
    while True:
        next_run = get_next_run_time()
        now = datetime.datetime.now()
        sleep_seconds = (next_run - now).total_seconds()
        
        # Ensure we don't have negative sleep time (just in case due to execution delays)
        if sleep_seconds < 0:
            sleep_seconds = 1
            
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Zzz... Sleeping for {sleep_seconds/3600:.2f} hours...")
        
        # Sleep until the next run time
        try:
            time.sleep(sleep_seconds)
            run_tts()
            # Sleep a bit extra after running to ensure we don't double-trigger in the same minute
            time.sleep(60) 
        except KeyboardInterrupt:
            print("\nScheduler stopped by user. Exiting.")
            sys.exit(0)

if __name__ == "__main__":
    main()
