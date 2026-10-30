import json
import os
import re
import shutil
import unicodedata
import numpy as np
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --- KONFIGURÁCIÓ ---
# Fájlok listája és prioritási sorrendje (Elöl a legerősebb!)
# Ha egy hír több helyen is megvan, abban a fájlban marad meg, amelyik előrébb van itt.
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR', '.')
INPUT_FILES = [
    os.path.join(DAILY_OUTPUT_DIR, 'data_i5.json'),
    os.path.join(DAILY_OUTPUT_DIR, 'data_i4.json'),
    os.path.join(DAILY_OUTPUT_DIR, 'data.json')
]
OUTPUT_DEBUG = os.path.join(DAILY_OUTPUT_DIR, 'data_dedup_debug.json')

# --- KÜSZÖBÉRTÉKEK ---
# Megjegyzés: rapidfuzz.fuzz.ratio kicsit lágyabb mint difflib.SequenceMatcher,
# ezért a cím-alapú küszöböket picit feljebb vettük a kompenzációhoz.
THRESH_TITLE_HIGH = 0.63
THRESH_CONTENT_LOW = 0.20

THRESH_TITLE_MID = 0.42
THRESH_CONTENT_MID = 0.50

THRESH_CONTENT_ONLY = 0.40

# Cím-only fallback küszöb (tematikus duplikátum). Korábban 0.65 volt,
# emelve, mert így ritkábban szól rosszul be eltérő, de hasonló című cikkekre
# (pl. "Supergirl film" vs "Supergirl sorozat").
THRESH_TITLE_ONLY = 0.70

# Csak ezen küszöb felett vizsgáljuk a párokat — kisebb értéknél a tartalom
# olyan távoli, hogy a többi feltétel sem aktiválódhatna.
CANDIDATE_CSCORE_THRESH = 0.05


def normalize_text(text):
    """Szöveg tisztítása a jobb összehasonlításhoz."""
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text


def extract_keywords(text):
    """Kinyeri a 4 betűsnél hosszabb kulcsszavakat és a fontos számokat a szövegből."""
    if not text:
        return set()
    text = normalize_text(text)
    words = set(re.findall(r'\b[a-z]{4,}\b|\b\d+\b', text))
    return words


def load_all_files(file_list):
    """Betölti az összes fájlt és megjegyzi, melyik hír honnan jött."""
    combined_data = []
    file_origin = []

    for fname in file_list:
        if not os.path.exists(fname):
            print(f"Figyelem: '{fname}' nem található, kihagyva.")
            continue

        try:
            with open(fname, 'r', encoding='utf-8') as f:
                content = json.load(f)
                if isinstance(content, list):
                    for item in content:
                        combined_data.append(item)
                        file_origin.append(fname)
                elif isinstance(content, dict):
                    combined_data.append(content)
                    file_origin.append(fname)
        except json.JSONDecodeError:
            print(f"HIBA: Érvénytelen JSON formátum: {fname}")

    return combined_data, file_origin


def get_priority(filename):
    """Visszaadja a fájl prioritását (kisebb szám = magasabb prioritás)."""
    try:
        return INPUT_FILES.index(filename)
    except ValueError:
        return 999


def run_deduplication():
    print(f"Fájlok feldolgozása prioritási sorrendben: {INPUT_FILES}")

    # 0. Elmúlt 5 nap adatainak betöltése (cross-day dedup)
    import datetime as dt

    today_dir_name = os.path.basename(DAILY_OUTPUT_DIR)
    try:
        today = dt.datetime.strptime(today_dir_name, '%Y-%m-%d').date()
    except ValueError:
        today = dt.date.today()

    past_data = []
    past_origins = []
    PAST_MARKER = "__PAST_DAY__"

    for delta in range(1, 6):
        past_date = today - dt.timedelta(days=delta)
        past_dir = os.path.join('Output', past_date.strftime('%Y-%m-%d'))

        for fname in ['data.json', 'data_i4.json', 'data_i5.json']:
            fpath = os.path.join(past_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = json.load(f)
                    if isinstance(content, list):
                        for item in content:
                            past_data.append(item)
                            past_origins.append(PAST_MARKER)
                except (json.JSONDecodeError, Exception):
                    pass

    if past_data:
        print(f"  🕐 {len(past_data)} hír betöltve az elmúlt 5 napból (cross-day dedup)")

    # 1. Adatok betöltése (mai nap)
    data, origins = load_all_files(INPUT_FILES)
    if not data:
        print("Nincs feldolgozható adat.")
        return

    combined_data = past_data + data
    combined_origins = past_origins + origins
    past_count = len(past_data)

    n = len(combined_data)
    print(f"Összesen {len(data)} mai hír + {past_count} korábbi hír = {n} összehasonlításra.")

    # 2. Előkészítés
    print("Szövegelemzés...")
    norm_contents = [normalize_text(d.get('content', '')) for d in combined_data]
    norm_titles = [normalize_text(d.get('title', '')) for d in combined_data]
    content_keywords = [extract_keywords(d.get('content', '')) for d in combined_data]

    vectorizer = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 4), min_df=1)
    try:
        tfidf_matrix = vectorizer.fit_transform(norm_contents)
    except ValueError:
        print("Hiba a vektorizálásnál (túl kevés adat?).")
        return

    # 3. Hasonlóság számítása
    print("Összehasonlítás...")
    cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)

    # 4. Jelölt párok kiszűrése vektorizáltan: csak felső háromszög + threshold
    # Ez kihagyja a Python-szintű iterációt minden i,j párra (n^2 / 2).
    upper_triu_mask = np.triu(cosine_sim >= CANDIDATE_CSCORE_THRESH, k=1)
    candidate_pairs = np.argwhere(upper_triu_mask)
    print(f"  📊 Jelölt párok (c_score ≥ {CANDIDATE_CSCORE_THRESH}): {len(candidate_pairs):,} / {n*(n-1)//2:,}")

    # 5. Klaszterezés (Csoportosítás) — csak a jelölt párokat vizsgáljuk
    adj_list = {i: set() for i in range(n)}

    # URL → indices map a same-URL bónusz O(1)-es ellenőrzéséhez
    url_groups = {}
    for idx, item in enumerate(combined_data):
        u = item.get('sourceLink')
        if u:
            url_groups.setdefault(u, []).append(idx)

    # Azonos URL-ű elemek mindenképp duplikátumok
    for url, idxs in url_groups.items():
        if len(idxs) > 1:
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    adj_list[idxs[a]].add(idxs[b])
                    adj_list[idxs[b]].add(idxs[a])

    # Jelölt párok döntési logikája — rapidfuzz a SequenceMatcher helyett
    for i, j in candidate_pairs:
        i = int(i)
        j = int(j)
        c_score = float(cosine_sim[i, j])

        # rapidfuzz.fuzz.ratio: 0-100, normalizáljuk 0-1-re
        t_score = fuzz.ratio(norm_titles[i], norm_titles[j]) / 100.0

        is_dupe = False
        shared_kw_count = len(content_keywords[i] & content_keywords[j])

        if c_score > THRESH_CONTENT_ONLY:
            is_dupe = True
        elif t_score > THRESH_TITLE_HIGH and c_score > THRESH_CONTENT_LOW:
            is_dupe = True
        elif t_score > THRESH_TITLE_MID and c_score > THRESH_CONTENT_MID:
            is_dupe = True
        elif t_score > THRESH_TITLE_ONLY:
            is_dupe = True
        elif shared_kw_count >= 15:
            is_dupe = True
        elif shared_kw_count >= 12 and c_score > 0.30:
            is_dupe = True

        if is_dupe:
            adj_list[i].add(j)
            adj_list[j].add(i)

    # 6. Csoportok feloldása és Győztes kiválasztása
    processed = set()
    final_indices = []
    debug_groups = []
    cross_day_removed_count = 0

    def _get_sort_priority(idx):
        imp = combined_data[idx].get('importance', 3)
        try:
            imp = int(imp)
        except (ValueError, TypeError):
            imp = 3
        if combined_origins[idx] == PAST_MARKER:
            return (-1, -imp, idx)
        return (get_priority(combined_origins[idx]), -imp, idx)

    for i in range(n):
        if i in processed:
            continue

        # BFS klaszter összegyűjtés
        cluster = {i}
        queue = [i]
        while queue:
            curr = queue.pop(0)
            for neighbor in adj_list[curr]:
                if neighbor not in cluster:
                    cluster.add(neighbor)
                    queue.append(neighbor)

        processed.update(cluster)

        cluster_members = list(cluster)
        cluster_members.sort(key=_get_sort_priority)

        winner_idx = cluster_members[0]
        final_indices.append(winner_idx)

        if len(cluster) > 1:
            removed_items = []
            for idx in cluster_members[1:]:
                is_cross_day = combined_origins[winner_idx] == PAST_MARKER and combined_origins[idx] != PAST_MARKER
                if is_cross_day:
                    cross_day_removed_count += 1
                removed_items.append({
                    "title": combined_data[idx].get('title'),
                    "source_file": combined_origins[idx],
                    "status": "TÖRÖLVE (Cross-day duplikátum)" if is_cross_day else "TÖRÖLVE (Duplikátum)"
                })

            debug_groups.append({
                "MEGTARTVA": {
                    "title": combined_data[winner_idx].get('title'),
                    "source_file": combined_origins[winner_idx]
                },
                "ELTÁVOLÍTVA": removed_items
            })

    if cross_day_removed_count > 0:
        print(f"  🕐 Cross-day dedup: {cross_day_removed_count} mai hír törölve (korábbi napban már szerepelt)")

    # 7. Eredmények szétválogatása fájlokba (CSAK mai napiak)
    output_buffers = {fname: [] for fname in INPUT_FILES}

    for idx in final_indices:
        origin_file = combined_origins[idx]
        if origin_file == PAST_MARKER:
            continue
        if origin_file in output_buffers:
            output_buffers[origin_file].append(combined_data[idx])

    # 8. Fájlok írása (Backup + Felülírás)
    print("\n--- EREDMÉNYEK MENTÉSE ---")
    for fname in INPUT_FILES:
        content = output_buffers.get(fname, [])

        if os.path.exists(fname):
            shutil.copy(fname, fname.replace('.json', '_backup.json'))

        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

        print(f"Frissítve: {fname} -> {len(content)} hír maradt.")

    with open(OUTPUT_DEBUG, 'w', encoding='utf-8') as f:
        json.dump(debug_groups, f, ensure_ascii=False, indent=2)
    print(f"\nRészletes log mentve: {OUTPUT_DEBUG}")


if __name__ == "__main__":
    run_deduplication()
