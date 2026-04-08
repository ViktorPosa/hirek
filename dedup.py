import json
import os
import re
import shutil
import unicodedata
import numpy as np
from difflib import SequenceMatcher
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --- KONFIGURÁCIÓ ---
# Fájlok listája és prioritási sorrendje (Elöl a legerősebb!)
# Ha egy hír több helyen is megvan, abban a fájlban marad meg, amelyik előrébb van itt.
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

# --- KÜSZÖBÉRTÉKEK (A TE BEÁLLÍTÁSAID) ---
# Ha a cím nagyon hasonlít (>0.60), akkor a tartalomnak elég csak kicsit (>0.20)
THRESH_TITLE_HIGH = 0.60
THRESH_CONTENT_LOW = 0.20

# Ha a cím közepesen hasonlít (>0.40), akkor a tartalomnak jobban kell (>0.50)
THRESH_TITLE_MID = 0.40
THRESH_CONTENT_MID = 0.50

# Ha a cím nem hasonlít, akkor a tartalomnak erősen egyeznie kell
# (0.40-ra csökkentve 0.50-ről, mert az AI-összefoglalók átfogalmazzák a szövegeket)
THRESH_CONTENT_ONLY = 0.40

def normalize_text(text):
    """Szöveg tisztítása a jobb összehasonlításhoz."""
    if not text: return ""
    text = str(text).lower()
    # Ékezetek eltávolítása (Unicode normalization)
    text = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    # Csak alfanumerikus karakterek maradnak
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text

def extract_keywords(text):
    """Kinyeri a 4 betűsnél hosszabb kulcsszavakat és a fontos számokat a szövegből."""
    if not text: return set()
    text = normalize_text(text)
    # 4 karakternél hosszabb szavak + számjegyek (pl. 2, 10)
    words = set(re.findall(r'\b[a-z]{4,}\b|\b\d+\b', text))
    return words

def load_all_files(file_list):
    """Betölti az összes fájlt és megjegyzi, melyik hír honnan jött."""
    combined_data = []
    file_origin = [] # Párhuzamos lista: [file_name, file_name, ...]
    
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
                     # Ha véletlenül nem lista, hanem egy objektum
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
    
    # Derive "today" from DAILY_OUTPUT_DIR to handle midnight-crossing pipelines
    # (run_pipeline.py sets DAILY_OUTPUT_DIR at start, but dedup may run hours later)
    today_dir_name = os.path.basename(DAILY_OUTPUT_DIR)
    try:
        today = dt.datetime.strptime(today_dir_name, '%Y-%m-%d').date()
    except ValueError:
        today = dt.date.today()  # fallback
    past_data = []
    past_origins = []
    PAST_MARKER = "__PAST_DAY__"
    
    for delta in range(1, 6):  # 1-5 nappal ezelőtt
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

    # Összefűzés: past_data (legmagasabb prioritás) + mai data
    # A past_data elemek nem lesznek eltávolítva, csak a mai duplikátumaik
    combined_data = past_data + data
    combined_origins = past_origins + origins
    past_count = len(past_data)

    print(f"Összesen {len(data)} mai hír + {past_count} korábbi hír = {len(combined_data)} összehasonlításra.")

    # 2. Előkészítés (Normalizálás + Vektorizálás + Kulcsszavak kinyerése)
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

    # 4. Klaszterezés (Csoportosítás)
    # Gráfot építünk: ha két hír duplikátum, összekötjük őket.
    adj_list = {i: set() for i in range(len(combined_data))}
    
    for i in range(len(combined_data)):
        for j in range(i + 1, len(combined_data)):
            # Gyorsítás: Ha a tartalom nagyon nem hasonlít, ne is nézzük a címet
            c_score = cosine_sim[i, j]
            if c_score < 0.1: 
                continue

            t_score = SequenceMatcher(None, norm_titles[i], norm_titles[j]).ratio()
            is_dupe = False
            shared_kw_count = len(content_keywords[i] & content_keywords[j])
            
            # --- DÖNTÉSI LOGIKA ---
            if c_score > THRESH_CONTENT_ONLY:
                is_dupe = True
            elif t_score > THRESH_TITLE_HIGH and c_score > THRESH_CONTENT_LOW:
                is_dupe = True
            elif t_score > THRESH_TITLE_MID and c_score > THRESH_CONTENT_MID:
                is_dupe = True
            elif shared_kw_count >= 15:
                # Extrém egyezés (15+ azonos 4 betűs szó/szám), esélytelen hogy ne ugyanaz a sztori legyen
                is_dupe = True
            elif shared_kw_count >= 12 and c_score > 0.30:
                # Erős egyezés és a karaktern-gramok is legalább egy közepes alatti szintet megütnek
                is_dupe = True
            
            # URL egyezés (bónusz)
            u1 = combined_data[i].get('sourceLink')
            u2 = combined_data[j].get('sourceLink')
            if u1 and u2 and u1 == u2:
                is_dupe = True

            if is_dupe:
                adj_list[i].add(j)
                adj_list[j].add(i)

    # 5. Csoportok feloldása és Győztes kiválasztása
    processed = set()
    final_indices = []
    debug_groups = []
    cross_day_removed_count = 0

    def _get_sort_priority(idx):
        """Priority: past-day > higher importance > file priority.
        Negative importance so higher importance sorts first."""
        imp = combined_data[idx].get('importance', 3)
        try:
            imp = int(imp)
        except (ValueError, TypeError):
            imp = 3
        if combined_origins[idx] == PAST_MARKER:
            return (-1, -imp, idx)  # Past-day = highest priority
        return (get_priority(combined_origins[idx]), -imp, idx)

    for i in range(len(combined_data)):
        if i in processed:
            continue
        
        # Klaszter összegyűjtése (BFS keresés)
        cluster = {i}
        queue = [i]
        while queue:
            curr = queue.pop(0)
            for neighbor in adj_list[curr]:
                if neighbor not in cluster:
                    cluster.add(neighbor)
                    queue.append(neighbor)
        
        processed.update(cluster)

        # GYŐZTES VÁLASZTÁS
        # Past-day > higher importance > i5 file > i4 file > data file
        cluster_members = list(cluster)
        cluster_members.sort(key=_get_sort_priority)
        
        winner_idx = cluster_members[0]
        final_indices.append(winner_idx)

        # Debug infó mentése, ha volt mit törölni
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

    # 6. Eredmények szétválogatása fájlokba (CSAK mai napiak)
    output_buffers = {fname: [] for fname in INPUT_FILES}
    
    for idx in final_indices:
        origin_file = combined_origins[idx]
        # Past-day elemek nem kerülnek bele a mai fájlokba
        if origin_file == PAST_MARKER:
            continue
        if origin_file in output_buffers:
            output_buffers[origin_file].append(combined_data[idx])

    # 7. Fájlok írása (Backup + Felülírás)
    print("\n--- EREDMÉNYEK MENTÉSE ---")
    for fname in INPUT_FILES:
        content = output_buffers.get(fname, [])
        
        # Biztonsági mentés
        if os.path.exists(fname):
            shutil.copy(fname, fname.replace('.json', '_backup.json'))
        
        # Felülírás
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        
        print(f"Frissítve: {fname} -> {len(content)} hír maradt.")

    # Debug fájl
    with open(OUTPUT_DEBUG, 'w', encoding='utf-8') as f:
        json.dump(debug_groups, f, ensure_ascii=False, indent=2)
    print(f"\nRészletes log mentve: {OUTPUT_DEBUG}")

if __name__ == "__main__":
    run_deduplication()
