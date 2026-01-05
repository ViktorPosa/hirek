import subprocess
import sys
import time
import os
import datetime
import argparse


# Script definitions with their corresponding skip flag name
# (script_file, description, arg_name)
PIPELINE_STEPS = [
    ("mimofilter.py", "Fetching and Filtering News", "filter"),
    ("sorter.py", "Sorting Links", "sort"),
    ("link_filter.py", "Pre-Filtering Negative Links", "linkfilter"),
    ("summarizer_json.py", "Summarizing to JSON", "summarize"),
    ("weather_generator.py", "Generating Weather Forecast", "weather"),
    ("market_generator.py", "Generating Market Analysis", "market"),
    ("tag_generator_json.py", "Generating Tags JSON", "tags"),
    ("ajanlott_generator.py", "Generating Recommendations", "ajanlott"),
]




def run_script(script_name, description):
    print(f"\n{'='*50}")
    print(f"STEP: {description} ({script_name})")
    print(f"{'='*50}\n")
    
    start_time = time.time()
    try:
        # Run the script using the current python interpreter
        result = subprocess.run([sys.executable, script_name], check=True)
        
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
    parser.add_argument("--skip-filter", action="store_true", help="Skip fetching and filtering (mimofilter.py)")
    parser.add_argument("--skip-sort", action="store_true", help="Skip sorting links (sorter.py)")
    parser.add_argument("--skip-linkfilter", action="store_true", help="Skip link pre-filtering (link_filter.py)")
    parser.add_argument("--skip-summarize", action="store_true", help="Skip summarization (summarizer_json.py)")
    parser.add_argument("--skip-weather", action="store_true", help="Skip weather generation (weather_generator.py)")
    parser.add_argument("--skip-market", action="store_true", help="Skip market analysis (market_generator.py)")
    parser.add_argument("--skip-ajanlott", action="store_true", help="Skip recommendation generation (ajanlott_generator.py)")


    
    args = parser.parse_args()

    print("Starting News Processing Pipeline (JSON Version)")
    print("=" * 50)
    total_start = time.time()

    
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

    
    for script, description, arg_name in PIPELINE_STEPS:
        # Check if we should skip this step
        arg_attr = f"skip_{arg_name}"
        if getattr(args, arg_attr, False):
            print(f"\n[SKIPPING] {description} ({script}) due to --skip-{arg_name}")
            continue

        if not os.path.exists(script):
            print(f"Error: Script {script} not found in {current_dir}")
            return
            
        success = run_script(script, description)
        if not success:
            return


    total_elapsed = time.time() - total_start
    print(f"\n{'='*50}")
    print(f"PIPELINE COMPLETED SUCCESSFULLY")
    print(f"Total time: {total_elapsed:.2f} seconds")
    print(f"Output format: JSON (data.json, piacok.json, idojaras.json)")
    print(f"{'='*50}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
    input("\nPress Enter to exit...")

