#!/usr/bin/env python3
"""Write a local sidecar JSON of tags for every downloaded image.

For each article that has an image hosted in the Kepek repo and a list of tags,
a `<slug>.tags.json` file is written into a SEPARATE `ImageTags/` folder that
mirrors the image's date/section/slug path, so the tags can be reused later
(e.g. to re-purpose an image). These sidecars are LOCAL ONLY and must never be
pushed (ImageTags/ is git-ignored in the root repo).

Usage:
    python image_tags_sidecar.py               # today only (pipeline step)
    python image_tags_sidecar.py --date 2026-08-07
    python image_tags_sidecar.py --all         # retroactive: every Output day
"""
import argparse
import datetime
import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "Output")
IMAGE_REPO_DIR = os.path.join(BASE_DIR, "ImageRepo")
IMAGE_TAGS_DIR = os.path.join(BASE_DIR, "ImageTags")
KEPEK_MARKER = "/Derushir/Kepek/"
# data files that may carry image+tags pairs
DATA_FILES = ("data.json", "data_toplist.json", "data_youtube.json")
SIDECAR_SUFFIX = ".tags.json"


def _rel_from_url(url):
    """Extract the ImageRepo-relative path from a raw.githubusercontent Kepek URL."""
    if not url or KEPEK_MARKER not in url:
        return None
    rest = url.split(KEPEK_MARKER, 1)[1]        # e.g. main/2026-08-07/news/slug.jpg
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return None
    return parts[1]                              # drop the branch segment (main)


def _tags_path(rel):
    """Map an ImageRepo-relative image path to its sidecar path inside ImageTags/."""
    root, _ext = os.path.splitext(rel)          # e.g. 2026-08-07/news/slug
    return os.path.join(IMAGE_TAGS_DIR, root + SIDECAR_SUFFIX)


def _iter_articles(date_dir):
    for name in DATA_FILES:
        path = os.path.join(date_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            data = data.get("articles") or data.get("items") or []
        if isinstance(data, list):
            for art in data:
                if isinstance(art, dict):
                    yield art


def generate_for_date(date_str):
    """Write tag sidecars for all mappable images of a single day. Returns count written."""
    date_dir = os.path.join(OUTPUT_DIR, date_str)
    if not os.path.isdir(date_dir):
        return 0
    written = 0
    for art in _iter_articles(date_dir):
        rel = _rel_from_url(art.get("image"))
        tags = art.get("tags")
        if not rel or not tags:
            continue
        local_image = os.path.join(IMAGE_REPO_DIR, rel)
        if not os.path.exists(local_image):
            continue
        payload = {
            "image": rel,
            "tags": tags,
            "title": art.get("title"),
            "section": art.get("section"),
            "originalTitle": art.get("originalTitle"),
            "sourceLink": art.get("sourceLink"),
            "date": date_str,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        out_path = _tags_path(rel)
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            written += 1
        except OSError:
            continue
    return written


def _all_dates():
    dates = []
    for path in glob.glob(os.path.join(OUTPUT_DIR, "*")):
        name = os.path.basename(path)
        if os.path.isdir(path) and len(name) == 10 and name[4] == "-" and name[7] == "-":
            dates.append(name)
    return sorted(dates)


def main():
    parser = argparse.ArgumentParser(description="Generate per-image tag sidecar JSONs (local only)")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--all", action="store_true", help="Process every Output day (retroactive)")
    args = parser.parse_args()

    if args.all:
        dates = _all_dates()
    elif args.date:
        dates = [args.date]
    else:
        dates = [datetime.date.today().isoformat()]

    total = 0
    for d in dates:
        n = generate_for_date(d)
        total += n
        if args.all:
            print(f"  {d}: {n} sidecars")
    print(f"[image_tags_sidecar] Wrote {total} tag sidecar(s) across {len(dates)} day(s).")


if __name__ == "__main__":
    main()
