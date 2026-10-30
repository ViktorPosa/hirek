import os
import re
import json
import datetime
from urllib.parse import urlparse, unquote
from collections import defaultdict
from history_manager import HistoryManager

"""
LEÍRÁS:
Téma szerinti link deduplikáció a links.txt-ben.
Az RSS feedekből begyűjtött linkek között gyakran több forrás is ugyanazt a témát dolgozza fel.
Ez a szkript a hasonló témájú linkeket felismeri (domain+path kulcsszavak alapján),
és csak egyet tart meg közülük — csökkentve az AI összefoglaló terhelését.

BEMENET:
- Output/[Dátum]/links.txt: Nyers linklista

KIMENET:
- Output/[Dátum]/links.txt: Deduplikált linklista (felülírva)
- Output/[Dátum]/links_dedup_debug.json: Eldobott linkek részletei
"""

# --- CONFIGURATION ---
DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

LINKS_FILE = os.path.join(DAILY_OUTPUT_DIR, 'links.txt')
DEBUG_FILE = os.path.join(DAILY_OUTPUT_DIR, 'links_dedup_debug.json')

# Hasonlósági küszöb (azonos domain-en belül)
SIMILARITY_THRESHOLD = 0.65

# Stop szavak - ezeket nem vesszük figyelembe a hasonlóság számításnál
STOP_WORDS = {
    'news', 'article', 'articles', 'cikk', 'hirek', 'hir', 'index',
    'cid', 'php', 'html', 'htm', 'www', 'com', 'hu', 'org', 'net',
    'https', 'http', 'utm_source', 'utm_medium', 'utm_campaign',
    'rss', 'feed', 'rss_feed', 'rss_syndication', 'rss_partner_inbound',
    '2026', '2025', '2024', '01', '02', '03', '04', '05', '06',
    '07', '08', '09', '10', '11', '12', '20260210', '20260209',
    '20260211', 'sites', 'posts', 'blog',
}


def extract_keywords(url):
    """URL-ből kulcsszavakat nyer ki a path és query alapján."""
    try:
        parsed = urlparse(unquote(url))
        domain = parsed.netloc.replace('www.', '').split(':')[0]
        
        # Path szegmensek
        path = parsed.path.strip('/')
        # Szétbontjuk a path-et szegmensekre és alszegmensekre
        segments = re.split(r'[/\-_.]', path)
        
        # Szűrés: csak értelmes szavak
        keywords = []
        for seg in segments:
            seg = seg.lower().strip()
            if not seg:
                continue
            if seg in STOP_WORDS:
                continue
            if len(seg) < 3:
                continue
            if seg.isdigit():
                continue
            keywords.append(seg)
        
        return domain, keywords
    except Exception:
        return '', []


def get_domain_group(url):
    """Domain kinyerése az URL-ből csoportosításhoz."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').split(':')[0]
        return domain
    except Exception:
        return ''


def jaccard_similarity(set_a, set_b):
    """Jaccard hasonlóság két halmaz között."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def keyword_overlap_similarity(kw_a, kw_b):
    """
    Kulcsszó-átfedés alapú hasonlóság.
    Jaccard + egy bónusz ha legalább 3 szó egyezik.
    Minimum 3 kulcsszó szükséges mindkét oldalon.
    """
    set_a = set(kw_a)
    set_b = set(kw_b)
    
    # Minimum keyword requirement — too few keywords = unreliable comparison
    if len(set_a) < 3 or len(set_b) < 3:
        return 0.0
    
    if not set_a or not set_b:
        return 0.0
    
    intersection = set_a & set_b
    union = set_a | set_b
    
    jaccard = len(intersection) / len(union)
    
    # Bónusz: ha 3+ szó egyezik, erősebb jelzés
    if len(intersection) >= 3:
        jaccard = min(1.0, jaccard + 0.15)
    
    return jaccard


def cross_domain_similarity(kw_a, kw_b):
    """
    Különböző domain-ek közötti hasonlóság.
    Szigorúbb: legalább 4 közös kulcsszó ÉS minimum 0.40 Jaccard kell.
    """
    set_a = set(kw_a)
    set_b = set(kw_b)
    
    # Minimum keyword requirement
    if len(set_a) < 3 or len(set_b) < 3:
        return 0.0
    
    if not set_a or not set_b:
        return 0.0
    
    intersection = set_a & set_b
    union = set_a | set_b
    
    jaccard = len(intersection) / len(union)
    
    # Strict: need BOTH 4+ common keywords AND decent Jaccard
    if len(intersection) >= 4 and jaccard >= 0.40:
        return jaccard
    
    # Very high Jaccard alone is also fine
    if jaccard >= 0.70:
        return jaccard
    
    return 0.0


def _get_past_days_links(days_back=5):
    """Elmúlt N nap links.txt fájljaiban szereplő linkek betöltése."""
    past_links = []
    today = datetime.date.today()
    
    for delta in range(1, days_back + 1):
        past_date = today - datetime.timedelta(days=delta)
        past_dir = os.path.join('Output', past_date.strftime('%Y-%m-%d'))
        past_links_file = os.path.join(past_dir, 'links.txt')
        
        if os.path.exists(past_links_file):
            with open(past_links_file, 'r', encoding='utf-8') as f:
                for line in f:
                    url = line.strip()
                    if url:
                        past_links.append(url)
    
    # Also check past days' data.json for sourceLinks (already processed)
    for delta in range(1, days_back + 1):
        past_date = today - datetime.timedelta(days=delta)
        past_dir = os.path.join('Output', past_date.strftime('%Y-%m-%d'))
        
        for fname in ['data.json', 'data_i4.json', 'data_i5.json']:
            fpath = os.path.join(past_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    if isinstance(items, list):
                        for item in items:
                            sl = item.get('sourceLink', '')
                            if sl:
                                past_links.append(sl)
                except (json.JSONDecodeError, Exception):
                    pass
    
    return past_links


def run_link_dedup():
    """Fő deduplikációs logika."""
    
    # 1. Linkek beolvasása
    if not os.path.exists(LINKS_FILE):
        print(f"⚠️ {LINKS_FILE} nem található, kilépés.")
        return
    
    with open(LINKS_FILE, 'r', encoding='utf-8') as f:
        links = [line.strip() for line in f if line.strip()]
    
    if not links:
        print("Nincsenek linkek a feldolgozáshoz.")
        return
    
    print(f"📋 Link deduplikáció: {len(links)} link beolvasva")
    
    # 1b. Cross-day deduplikáció — elmúlt 2 nap linkjeinek ellenőrzése
    past_links = _get_past_days_links(days_back=5)
    cross_day_removed = []
    
    if past_links:
        past_link_data = []
        for url in past_links:
            domain, keywords = extract_keywords(url)
            past_link_data.append({'url': url, 'domain': domain, 'keywords': keywords})
        
        # Exact URL match (gyors) + keyword similarity (lassabb)
        past_urls_set = set(past_links)
        new_links = []
        
        for url in links:
            # Exact match
            if url in past_urls_set:
                cross_day_removed.append({'url': url, 'reason': 'Pontos URL egyezés korábbi nappal'})
                continue
            
            # Keyword similarity check against past links
            domain_today, kw_today = extract_keywords(url)
            matched = False
            for past in past_link_data:
                if past['domain'] != domain_today:
                    continue
                sim = keyword_overlap_similarity(kw_today, past['keywords'])
                if sim >= SIMILARITY_THRESHOLD:
                    cross_day_removed.append({
                        'url': url,
                        'reason': f"Hasonló korábbi link ({sim:.0%}): {past['url'][:80]}"
                    })
                    matched = True
                    break
            if not matched:
                new_links.append(url)
        
        if cross_day_removed:
            print(f"  🕐 Cross-day dedup: {len(cross_day_removed)} link eltávolítva (már szerepelt az elmúlt 5 napban)")
            links = new_links
        else:
            print(f"  🕐 Cross-day dedup: nincs egyezés az elmúlt 5 nappal")
    
    # 2. Kulcsszavak kinyerése minden linkhez
    link_data = []
    for url in links:
        domain, keywords = extract_keywords(url)
        link_data.append({
            'url': url,
            'domain': domain,
            'keywords': keywords,
        })
    
    # 3. Domain csoportok összeállítása
    domain_groups = defaultdict(list)
    for i, data in enumerate(link_data):
        domain_groups[data['domain']].append(i)
    
    # 4. Deduplikáció - domainen belüli
    duplicates = {}  # idx -> winner_idx
    processed = set()
    
    for domain, indices in domain_groups.items():
        if len(indices) < 2:
            continue
        
        for i_pos in range(len(indices)):
            idx_a = indices[i_pos]
            if idx_a in duplicates:
                continue
                
            for j_pos in range(i_pos + 1, len(indices)):
                idx_b = indices[j_pos]
                if idx_b in duplicates:
                    continue
                
                sim = keyword_overlap_similarity(
                    link_data[idx_a]['keywords'],
                    link_data[idx_b]['keywords']
                )
                
                if sim >= SIMILARITY_THRESHOLD:
                    # idx_a nyer (első előfordulás), idx_b eldobva
                    duplicates[idx_b] = {
                        'winner_idx': idx_a,
                        'similarity': round(sim, 3),
                        'type': 'same_domain'
                    }
    
    # 5. Deduplikáció - cross-domain (különböző forrásból ugyanaz a hír)
    all_indices = list(range(len(link_data)))
    non_dup_indices = [i for i in all_indices if i not in duplicates]
    
    for i_pos in range(len(non_dup_indices)):
        idx_a = non_dup_indices[i_pos]
        if idx_a in duplicates:
            continue
            
        for j_pos in range(i_pos + 1, len(non_dup_indices)):
            idx_b = non_dup_indices[j_pos]
            if idx_b in duplicates:
                continue
            
            # Csak különböző domain-ek között
            if link_data[idx_a]['domain'] == link_data[idx_b]['domain']:
                continue
            
            sim = cross_domain_similarity(
                link_data[idx_a]['keywords'],
                link_data[idx_b]['keywords']
            )
            
            if sim > 0:
                duplicates[idx_b] = {
                    'winner_idx': idx_a,
                    'similarity': round(sim, 3),
                    'type': 'cross_domain'
                }
    
    # 6. Eredmények összeállítása
    kept_links = []
    debug_groups = []
    
    # Csoportosítás winner szerint
    winner_to_removed = defaultdict(list)
    for dup_idx, info in duplicates.items():
        winner_to_removed[info['winner_idx']].append(dup_idx)
    
    for i, data in enumerate(link_data):
        if i in duplicates:
            continue
        kept_links.append(data['url'])
        
        # Debug info ha volt duplikátum
        if i in winner_to_removed:
            removed = []
            for dup_idx in winner_to_removed[i]:
                dup_info = duplicates[dup_idx]
                removed.append({
                    'url': link_data[dup_idx]['url'],
                    'domain': link_data[dup_idx]['domain'],
                    'similarity': dup_info['similarity'],
                    'type': dup_info['type'],
                    'keywords': link_data[dup_idx]['keywords'][:8],  # max 8 keyword debug
                })
            
            debug_groups.append({
                'MEGTARTVA': {
                    'url': data['url'],
                    'domain': data['domain'],
                    'keywords': data['keywords'][:8],
                },
                'ELTÁVOLÍTVA': removed,
            })
    
    removed_count = len(duplicates)
    
    # 7. Fájlok írása
    print(f"\n--- LINK DEDUP EREDMÉNYEK ---")
    print(f"  Eredeti linkek: {len(links)}")
    print(f"  Eltávolított duplikátumok: {removed_count}")
    print(f"  Megmaradt linkek: {len(kept_links)}")
    
    if removed_count > 0:
        # Links.txt felülírása
        with open(LINKS_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(kept_links))
        print(f"  ✅ {LINKS_FILE} frissítve")
        
        # Debug fájl
        with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
            json.dump(debug_groups, f, ensure_ascii=False, indent=2)
        print(f"  📝 Debug log: {DEBUG_FILE} ({len(debug_groups)} csoport)")
        
        # Néhány példa kiírása
        print(f"\n  Példák az eltávolított duplikátumokra:")
        for group in debug_groups[:5]:
            winner = group['MEGTARTVA']
            print(f"    ✅ {winner['url'][:80]}...")
            for removed in group['ELTÁVOLÍTVA']:
                print(f"    ❌ ({removed['similarity']:.0%} {removed['type']}) {removed['url'][:80]}...")
            print()
    else:
        print("  ℹ️ Nem találtam duplikátumokat.")
    
    # 8. Eltávolított linkek hozzáadása a history-hoz (same-day + cross-day)
    all_removed_for_history = []
    
    # Same-day duplicates
    for dup_idx, info in duplicates.items():
        winner_url = link_data[info['winner_idx']]['url']
        removed_url = link_data[dup_idx]['url']
        reason = f"Duplikátum ({info['type']}, {info['similarity']:.0%} hasonlóság) — megtartva: {winner_url[:80]}"
        all_removed_for_history.append((removed_url, reason))
    
    # Cross-day duplicates
    for item in cross_day_removed:
        all_removed_for_history.append((item['url'], f"Cross-day dedup: {item['reason']}"))
    
    if all_removed_for_history:
        history = HistoryManager()
        for removed_url, reason in all_removed_for_history:
            norm_url = history.normalize_url(removed_url)
            history.history[norm_url] = {
                'first_seen': datetime.datetime.now().isoformat(),
                'last_updated': datetime.datetime.now().isoformat(),
                'status': 'FILTERED',
                'summarized': False,
                'filtered_by': 'link_dedup',
                'filter_reason': reason
            }
        history.save()
        print(f"  📚 {len(all_removed_for_history)} link hozzáadva a history.json-hoz (FILTERED)")


if __name__ == "__main__":
    run_link_dedup()
