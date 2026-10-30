#!/usr/bin/env python3
"""daily_filter_words.py

Ensures a per-day copy of the negative-keywords (filter words) JSON exists at
`Output/{date}/negative_keywords.json` so the mobile apps can fetch "today's"
default exclusion list.

Behaviour (see ensure_daily_filter_words):
- On the FIRST push of a given day, the file is a COPY of the previous day's
  file (most recent existing Output/<date>/negative_keywords.json).
- If no previous day's file exists, it falls back to Input/negative_keywords.json.
- If the target already exists, it is left untouched (idempotent), so any
  same-day user edits already staged are preserved.

Kept as a standalone module on purpose: the pipeline owner prefers risky logic
in a new file rather than inline in run_pipeline.py.
"""

import os
import re
import json


_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(data, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find_previous_source(output_dir: str, date: str):
    """Return the newest Output/<prevdate>/negative_keywords.json with prevdate < date, or None."""
    if not os.path.isdir(output_dir):
        return None
    candidates = []
    for name in os.listdir(output_dir):
        if not _DATE_DIR_RE.match(name):
            continue
        if name >= date:  # ISO date strings sort lexicographically
            continue
        candidate = os.path.join(output_dir, name, "negative_keywords.json")
        if os.path.isfile(candidate):
            candidates.append((name, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])  # ascending by date
    return candidates[-1][1]  # latest


def ensure_daily_filter_words(base_dir: str, date: str) -> str:
    """Ensure Output/<date>/negative_keywords.json exists; return its path.

    date: "YYYY-MM-DD".
    """
    output_dir = os.path.join(base_dir, "Output")
    target_dir = os.path.join(output_dir, date)
    target = os.path.join(target_dir, "negative_keywords.json")

    # 2. Idempotent: keep existing (preserves same-day user edits already staged).
    if os.path.exists(target):
        print(f"[daily_filter_words] Target already exists, leaving unchanged: {target}")
        return target

    # 3. Determine source: (a) latest previous day's file, else (b) Input fallback.
    source = _find_previous_source(output_dir, date)
    if source is None:
        source = os.path.join(base_dir, "Input", "negative_keywords.json")

    # 4. Copy source -> target, preserving/normalising JSON formatting.
    os.makedirs(target_dir, exist_ok=True)
    data = _load_json(source)
    _dump_json(data, target)

    print(f"[daily_filter_words] Copied filter words from {source} -> {target}")
    return target


if __name__ == "__main__":
    import datetime

    _base_dir = os.path.dirname(os.path.abspath(__file__))
    _today = datetime.date.today().strftime("%Y-%m-%d")
    ensure_daily_filter_words(_base_dir, _today)
