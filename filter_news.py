"""
LEÍRÁS:
JSON Utószűrő modul.
Ellenőrzi a már generált híreket (data.json) tiltott kulcsszavak alapján.
Ha egy hír címe vagy tartalma tiltott szót tartalmaz (pl. Trump, gyilkosság),
azt eltávolítja a végső JSON-ből és bejegyzi FILTERED-ként a history-ba.
Ez egy biztonsági háló a generálás után.

BEMENET:
- data.json

KIMENET:
- Tisztított data.json
- Frissített history.json
"""

import os
import sys
import json
import re
import datetime

# Fix Windows console encoding for Hungarian characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from history_manager import HistoryManager

# Negatív kulcsszavak — EGYETLEN forrás. Modul-szinten él, hogy a summarizer_json.py
# már az összefoglalás ELŐTT (a nyers, letöltött cikkre) tudja ugyanezt a listát
# alkalmazni, és ne pazaroljon összefoglalást olyan hírre, amit úgyis kidobnánk.
# Note: "war" removed - caused false positives on "software", "hardware", "warranty", etc.
# "háború" (Hungarian) is kept for actual war content
NEGATIVE_KEYWORDS = [
    "Trump", "politics", "death", "murder", "Orban Viktor", "Hadhazy",
    "Magyar Peter", "Meszaros Lőrinc",
    "tragedy", "rejtvény", "politika",
    "ukrán", "ukrajna", "Orbán Viktor", "Mészáros Lőrinc", "Magyar Péter",
    "katasztrófa", "tragédia", "börtön",
    "gyilkosság", "Putyin", "Putin", "Oroszország", "Pintér Sándor", "tűz ütött ki", "Wordle",
    "Hadházy", "tüntetés", "tiltakozás", "demonstráció", "hídfoglalás", "lázár", "Lázár János", "lazar", "kormany", "kormány",
    "halálos", "hahalos", "baleset", "javítóintézet", "botrány", "károsult", "károsultak", "panaszkodnak", "rablás", "késelés", "bűnügy",
    "fegyház"
]

# --- Szótő-alapú negatív kulcsszavak (SZÓHATÁRHOZ kötött illesztés) ---------------------
# A rövid tövek (pl. "adó", "akció") substring-illesztve tömegével adnának téves találatot
# ("kiadó", "feladó", "előadó", "reakció", "interakció"...). Ezért ezekre a szó ELEJÉHEZ kötött
# (\b) illesztést használunk: a tő a szó elején álljon, de magyar toldalék jöhet UTÁNA
# (pl. "adó", "adót", "adóügyek", "adózás" MATCH; "kiadó", "előadó" NEM).
NEGATIVE_KEYWORDS_WORDSTART = [
    "Nyomozás", "adó", "adóügyek", "nyugdíj", "techhiba", "üzemzavar", "adósságfék",
    "hitel", "Kína", "műszaki hiba", "fegyelmi eljárás", "eltiltás", "pesszimizmus",
    "munkaerőpiac", "áremelés", "ellátási lánc", "ételmérgezés", "nébih",
    "Matolcsy György", "luxusingatlan", "közpénz", "állami vagyon", "MNB-botrány",
    "veszteség", "terméskiesés", "akció", "szókirakó", "napi fejtörő",
]

# Egyedi minták, ahol a puszta szóhatár nem elég pontos:
#  - "hitel": ne fogja meg a "hiteles"/"hitelesség"-et (az pozitív jelentésű).
_WORDSTART_CUSTOM = {
    "hitel": r"\bhitel(?!es)",
}

def _build_wordstart_patterns():
    pats = []
    for kw in NEGATIVE_KEYWORDS_WORDSTART:
        rx = _WORDSTART_CUSTOM.get(kw.lower(), r"\b" + re.escape(kw.lower()))
        pats.append((kw, re.compile(rx, re.IGNORECASE)))
    return pats

_WORDSTART_PATTERNS = _build_wordstart_patterns()
_LEGACY_LOWER = [(kw, kw.lower()) for kw in NEGATIVE_KEYWORDS]


def find_negative_match(text, include_wordstart=True):
    """Az első negatív kulcsszó a szövegben, vagy None.

    - Legacy lista (NEGATIVE_KEYWORDS): substring-illesztés (visszamenőleges kompatibilitás).
      Ezek a "kemény" kizárások (Trump, halál, háború...) — a pre-szűrésnél is aktívak.
    - Szótő-lista (NEGATIVE_KEYWORDS_WORDSTART): szóhatárhoz kötött illesztés. Ezeket CSAK a
      post-szűrés (filter_news.py) használja (include_wordstart=True), hogy a hír előbb
      ÖSSZEFOGLALÓDJON, majd a data.json-ból a filtered.json-be kerüljön (re-includable).
      A pre-szűrő (summarizer) include_wordstart=False-szal hívja, így NEM dobja el őket előre.
    Sose dob kivételt.
    """
    if not text:
        return None
    hay = str(text).lower()
    for kw, low in _LEGACY_LOWER:
        if low and low in hay:
            return kw
    if include_wordstart:
        for kw, pat in _WORDSTART_PATTERNS:
            if pat.search(hay):
                return kw
    return None


def _write_filtered_json(target_dir, removed_items):
    """A kizárt (már összefoglalt) híreket a weboldal által várt `filtered.json`-be írja
    (data.json-nel azonos formátumú tömb), ugyanabba a dátum-mappába.

    Akkumulál: a meglévő filtered.json-hoz hozzáfűz és dedupe-ol (sourceLink/title szerint),
    így a pipeline több lépésben (data.json, majd i4/i5) is bővítheti ugyanazt a napi fájlt.
    """
    path = os.path.join(target_dir, 'filtered.json')
    existing = []
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    seen = set()
    unique = []
    for it in existing + list(removed_items):
        if not isinstance(it, dict):
            continue
        key = (it.get('sourceLink') or it.get('title') or '').strip().lower()
        if not key:
            key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(unique, f, indent=2, ensure_ascii=False)
        print(f"filtered.json: {len(unique)} item ({len(removed_items)} uj ebben a futasban) -> {path}")
    except Exception as e:
        print(f"Error writing {path}: {e}")


def filter_news_json(json_path, history):
    """Filters news from a data.json file based on negative keywords.

    Args:
        json_path: Path to the data.json file
        history: HistoryManager instance for logging filtered links

    Returns:
        (removed_count, removed_items): a törölt hírek száma és maguk a hír-objektumok
        (utóbbi a filtered.json-be íráshoz).
    """
    # Load JSON file
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            news_list = json.load(f)
    except Exception as e:
        print(f"Error reading {json_path}: {e}")
        return 0, []

    if not isinstance(news_list, list):
        print(f"Invalid JSON format in {json_path}: expected a list")
        return 0, []

    filtered_news = []
    removed_items = []
    removed_count = 0

    for news_item in news_list:
        # Check title and content for negative keywords
        title = news_item.get('title', '')
        content = news_item.get('content', '')
        source_link = news_item.get('sourceLink', '')

        # Combine title, content, and sourceLink for keyword check
        text_to_check = f"{title} {content} {source_link}"

        matched_keyword = find_negative_match(text_to_check)

        if not matched_keyword:
            filtered_news.append(news_item)
        else:
            removed_count += 1
            removed_items.append(news_item)
            print(f"Removing news '{str(title)[:50]}...' due to keyword: {matched_keyword}")
            # Log to history.json with reason
            if source_link:
                history.mark_filtered(source_link, "json_news_filter", f"Content contains keyword: {matched_keyword}")

    # Save filtered news back to file
    if removed_count > 0:
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(filtered_news, f, indent=2, ensure_ascii=False)
            print(f"Updated {json_path}: Removed {removed_count} items.")
        except Exception as e:
            print(f"Error writing {json_path}: {e}")

    return removed_count, removed_items


def filter_all_json_news(output_dir, history):
    """Filters all data.json files in the output directory.
    
    Args:
        output_dir: Root output directory containing date folders
        history: HistoryManager instance
    
    Returns:
        Total number of removed news items
    """
    total_removed = 0

    # Walk through date directories
    for item in os.listdir(output_dir):
        item_path = os.path.join(output_dir, item)
        if os.path.isdir(item_path):
            # Check for data.json in each date folder
            json_path = os.path.join(item_path, 'data.json')
            if os.path.exists(json_path):
                print(f"\nProcessing: {json_path}")
                removed, removed_items = filter_news_json(json_path, history)
                total_removed += removed
                if removed_items:
                    _write_filtered_json(item_path, removed_items)

    return total_removed


def filter_today_json(history=None):
    """Filters only today's JSON files (data.json, data_i4.json, data_i5.json).
    
    Args:
        history: Optional HistoryManager instance (creates new one if not provided)
    
    Returns:
        Number of removed news items
    """
    if history is None:
        history = HistoryManager()
    
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    script_dir = os.path.dirname(os.path.abspath(__file__))
    today_dir = os.path.join(script_dir, 'Output', today)

    total_removed = 0
    all_removed_items = []
    for fname in ['data.json', 'data_i4.json', 'data_i5.json']:
        json_path = os.path.join(today_dir, fname)
        if os.path.exists(json_path):
            print(f"Filtering today's news: {json_path}")
            removed, removed_items = filter_news_json(json_path, history)
            total_removed += removed
            all_removed_items.extend(removed_items)

    # A weboldal a napi filtered.json-ből húzza vissza igény szerint a kizárt híreket.
    # (data.json/i4/i5 removed elemeit egy fájlba gyűjtjük, akkumulálva a napi mappában.)
    if os.path.isdir(today_dir) and all_removed_items:
        _write_filtered_json(today_dir, all_removed_items)

    if total_removed == 0:
        print(f"No negative news found in today's files.")

    return total_removed


if __name__ == "__main__":
    # Run on today's data.json by default
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Output")
    print(f"Scanning directory: {output_dir}")
    
    history = HistoryManager()
    
    # Option 1: Filter only today's JSON
    removed = filter_today_json(history)
    
    # Option 2: Filter all JSON files (uncomment if needed)
    # removed = filter_all_json_news(output_dir, history)
    
    print(f"\nTotal removed: {removed} news items")
    print("Filtered links logged to history.json")
