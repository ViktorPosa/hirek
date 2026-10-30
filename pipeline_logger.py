import os
import datetime

def log_pipeline_error(message):
    try:
        daily_out = os.environ.get('DAILY_OUTPUT_DIR')
        if not daily_out:
            today = datetime.date.today().strftime('%Y-%m-%d')
            daily_out = os.path.join('Output', today)
        
        os.makedirs(daily_out, exist_ok=True)
        log_path = os.path.join(daily_out, 'pipeline_errors.log')
        
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except:
        pass
