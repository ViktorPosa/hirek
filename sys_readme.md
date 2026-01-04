# Nemrossz3 News Pipeline Documentation

## Overview
This system is an automated news processing pipeline that fetches articles from RSS feeds, filters them for positive/neutral sentiment, categorizes them, summarizes them using AI, and generates formatted output with tags.

## Architecture

The pipeline consists of several Python scripts executed sequentially by a master orchestrator.

### 1. `run_pipeline.py` (The Orchestrator)
- **Role**: Entry point. Manages the workflow.
- **Actions**:
    - Determines `DAILY_OUTPUT_DIR` (e.g., `Output/2026-01-04`).
    - Creates necessary directories.
    - Sets the `DAILY_OUTPUT_DIR` environment variable for child processes.
    - Executes steps sequentially: `mimofilter` -> `sorter` -> `summarizer` -> `post_processor` -> `tag_generator`.
    - Stops execution if any step fails.

### 2. `mimofilter.py` (Filtering)
- **Role**: Fetches RSS feeds and filters articles.
- **Input**: `Input/input.txt` (API Key, Prompts, RSS Feeds list).
- **Process**:
    - Fetches top 10 items from each RSS feed.
    - Checks `history.json` (via `history_manager.py`) to skip already processed or negative links.
    - Sends batches of new links to Xiaomi Mimo API (`mimo-v2-flash`).
    - API Classifies sentiment (POSITIVE/NEUTRAL/NEGATIVE) and Category.
    - Updates `history.json` with results.
- **Output**: Writes valid links to `[DAILY_OUTPUT_DIR]/output.txt`.

### 3. `sorter.py` (Sorting)
- **Role**: Sorts the filtered links into category-specific files.
- **Input**: `[DAILY_OUTPUT_DIR]/output.txt`.
- **Process**:
    - Reads line-by-line (`[Category][Link]`).
    - Maps categories to filenames (e.g., 'Technika' -> `tech.txt`, 'Belföld' -> `belfold_kulfold.txt`).
- **Output**: Creates `tech.txt`, `uzlet.txt`, etc. in `[DAILY_OUTPUT_DIR]`.

### 4. `summarizer.py` (Summarization)
- **Role**: Generates detailed summaries for grouped links.
- **Input**: Category files in `[DAILY_OUTPUT_DIR]`.
- **Process**:
    - Checks `history.json` to skip already summarized links.
    - Batches links (10 per batch) and sends to Xiaomi Mimo API.
    - Uses prompt from `Input/summarize.txt`.
    - Updates `history.json` upon success.
- **Output**: Writes summaries to `[DAILY_OUTPUT_DIR]/Tartalom/[filename]`.

### 5. `post_processor.py` (Formatting)
- **Role**: Polishes the output.
- **Process**:
    - Standardizes formatting (removes colons from headers).
    - Adds hashtags to the `[Tagek]` field (e.g., `#tag1, #tag2`).
    - Can perform additional cleanup.
- **Output**: Updates files in `[DAILY_OUTPUT_DIR]/Tartalom/`.

### 6. `tag_generator.py` (Tag Cloud)
- **Role**: Extracts top tags for display.
- **Process**:
    - Scans top 30 articles in each category file.
    - Extracts the first tag from each.
- **Output**: Generates `*_cimke.txt` files containing comma-separated hashtags.

### 7. `history_manager.py` (Cache)
- **Role**: Manages persistent state.
- **Storage**: `history.json`.
- **Function**: Tracks `status` (POSITIVE/NEGATIVE) and `summarized` (bool) to prevent re-fetching or re-summarizing content.

## Configuration

- **`Input/input.txt`**:
    - `API_KEY=...`
    - `PROMPT=...` (Filter prompt)
    - `FEEDS:` (List of RSS URLs)
- **`Input/summarize.txt`**: Prompt template for summarization.

## Usage

Run the full pipeline:
```bash
py run_pipeline.py
```

Skip specific steps (e.g., skip fetching and sorting, just re-summarize):
```bash
py run_pipeline.py --skip-filter --skip-sort
```

Available options:
- `--skip-filter`: Skip `mimofilter.py`.
- `--skip-sort`: Skip `sorter.py`.
- `--skip-summarize`: Skip `summarizer.py`.
- `--skip-process`: Skip `post_processor.py`.
- `--skip-tags`: Skip `tag_generator.py`.


Chat with Mimo (Test):
```bash
py chat_mimo.py
```
