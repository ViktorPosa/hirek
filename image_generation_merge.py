"""
image_generation_merge.py — a párhuzamosan generált illusztrációk EGYBEFÉSÜLÉSE.

A downstream lánc VÉGE felé fut (a tag_generator UTÁN, hogy a `tags` már megvan, és a
filter_importance ELŐTT, hogy elég a data.json-t írni — a split + sidecar propagál).

Minden KÉP NÉLKÜLI cikkre (`image` üres/hiányzik) tag-alapon keres egy helyi képet a
~/Desktop/Hírképek könyvtárból (local_image_matcher.find_local_image, ≥5 kulcsszó-egyezés).
Ha talál, publikálja a Kepek repo-klónba (image_downloader.copy_to_git_repo) és beírja a
raw.githubusercontent URL-t a cikk `image` mezőjébe.

ALAPBÓL KIKAPCSOLVA: ha a pipeline.image_generation flag nem True a
Input/pipeline_settings.json-ban, ez valódi no-op (semmit nem ír, exit 0).
"""

import os
import sys
import json
import glob
import datetime

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_LANG = "hu"


def _image_generation_enabled():
    """True iff pipeline.image_generation igaz a settingsben (alap: False, hibára False)."""
    try:
        settings_path = os.path.join(_BASE_DIR, "Input", "pipeline_settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        return bool(settings.get("pipeline", {}).get("image_generation", False))
    except Exception:
        return False


def _daily_data_files(date_str):
    """A mai dátum-mappa összes feldolgozandó data fájlja (ami létezik)."""
    daily = os.path.join(_BASE_DIR, "Output", date_str)
    names = ["data.json", "data_i4.json", "data_i5.json", "data_toplist.json"]
    return [os.path.join(daily, n) for n in names if os.path.exists(os.path.join(daily, n))]


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    if not _image_generation_enabled():
        print("[imagegen-merge] disabled (pipeline.image_generation=false) — no-op.")
        return 0

    # A nehéz importok CSAK bekapcsolt állapotban (import-biztos marad kikapcsolva).
    try:
        from local_image_matcher import find_local_image
        from image_downloader import copy_to_git_repo
    except Exception as e:
        print(f"[imagegen-merge] import hiba: {e} — kihagyom.")
        return 0

    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    files = _daily_data_files(date_str)
    if not files:
        print(f"[imagegen-merge] nincs data fájl {date_str}-re — kihagyom.")
        return 0

    # Cikkenként cache-eljük a talált URL-t (sourceLink/cím szerint), hogy ha ugyanaz a
    # cikk több fájlban is szerepel, ne másoljunk fölöslegesen.
    resolved = {}   # match-kulcs -> url
    total_merged = 0

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception as e:
            print(f"[imagegen-merge] {os.path.basename(path)} olvasás hiba: {e} — kihagyom.")
            continue
        if not isinstance(items, list):
            continue

        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            img = (item.get("image") or "").strip()
            if img:
                continue  # már van képe
            tags = item.get("tags") or []
            if not tags:
                continue

            key = item.get("sourceLink") or item.get("title") or ""
            url = resolved.get(key) if key else None
            if url is None:
                png_path, details = find_local_image(tags, lang=PROJECT_LANG)
                if not png_path:
                    continue
                try:
                    url = copy_to_git_repo(png_path, date_str)
                except Exception as e:
                    print(f"[imagegen-merge] copy_to_git_repo hiba ({os.path.basename(png_path)}): {e}")
                    continue
                if not url:
                    continue
                if key:
                    resolved[key] = url
                mc = details.get("match_count") if isinstance(details, dict) else "?"
                print(f"[imagegen-merge] match ({mc} tag): {os.path.basename(png_path)} "
                      f"-> {item.get('title','')[:40]}")

            item["image"] = url
            changed = True
            total_merged += 1

        if changed:
            try:
                _atomic_write(path, items)
            except Exception as e:
                print(f"[imagegen-merge] {os.path.basename(path)} írás hiba: {e}")

    print(f"[imagegen-merge] merged {total_merged} images.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
