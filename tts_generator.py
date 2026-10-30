"""
LEÍRÁS:
TTS (Text-to-Speech) hírolvasó szkript generátor.
Összefűzi az időjárás- és piacelemzési adatokat egy folyó szöveggé, amit később felolvasásra szánnak.
A generált szöveg barátságos, híradós stílusú.

BEMENET:
- Időjárás adatok: Output/[Dátum]/idojaras.json
- Piaci adatok: Output/[Dátum]/piacok.json
- Prompt sablon: Input/tts_prompt.txt

KIMENET:
- Output/[Dátum]/tts_news.txt: A felolvasandó szöveg
"""

import os
import sys
import datetime
import json
import time

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Import Clients
# Import Clients
import geminipro_client
import gemini_api_client as geminipro_cli_client
import perplexity_client
# Lazy import for selenium to avoid overhead if not used
# import gemini_selenium_client 

# Enable wait-for-rate-limit mode if WAITREFRESH env var is set
if os.environ.get('WAITREFRESH') == '1':
    geminipro_client.set_wait_for_rate_limit(True)

# --- CONFIGURATION ---
INPUT_DIR = 'Input'

DAILY_OUTPUT_DIR = os.environ.get('DAILY_OUTPUT_DIR')
if not DAILY_OUTPUT_DIR:
    today = datetime.date.today().strftime('%Y-%m-%d')
    DAILY_OUTPUT_DIR = os.path.join('Output', today)

OUTPUT_DIR = DAILY_OUTPUT_DIR


def load_tts_items():
    """Per-item TTS enable flags from Input/pipeline_settings.json → pipeline.tts_items."""
    default = {"idojaras": True, "piacok": True, "headlines": True, "hircsarda": True}
    try:
        import json as _json
        import os as _os
        base = _os.path.dirname(_os.path.abspath(__file__))
        path = _os.path.join(base, "Input", "pipeline_settings.json")
        with open(path, encoding="utf-8") as f:
            s = _json.load(f)
        items = s.get("pipeline", {}).get("tts_items", {})
        if isinstance(items, dict):
            merged = {k: (items.get(k) is not False) for k in default}
            for k, v in items.items():
                if k not in merged:
                    merged[k] = (v is not False)
            return merged
    except Exception:
        pass
    return default


def load_custom_tts_items():
    """Load custom TTS item definitions from pipeline_settings.json → pipeline.custom_tts_items."""
    import json as _json
    try:
        import os as _os
        base = _os.path.dirname(_os.path.abspath(__file__))
        path = _os.path.join(base, "Input", "pipeline_settings.json")
        with open(path, encoding="utf-8") as f:
            s = _json.load(f)
        items = s.get("pipeline", {}).get("custom_tts_items", [])
        return items if isinstance(items, list) else []
    except Exception:
        return []

def parse_config(template_content):
    """
    Parses optional [CONFIG]...[/CONFIG] block from the beginning of the template.
    Returns (config_dict, remaining_template).
    """
    config = {
        'CITIES': [],
        'MARKETS': []
    }
    
    if '[CONFIG]' not in template_content or '[/CONFIG]' not in template_content:
        return config, template_content
        
    try:
        start = template_content.find('[CONFIG]')
        end = template_content.find('[/CONFIG]') + len('[/CONFIG]')
        config_block = template_content[start:end]
        remaining_template = template_content[:start] + template_content[end:]
        
        # Parse lines inside config
        inner_content = config_block.replace('[CONFIG]', '').replace('[/CONFIG]', '').strip()
        for line in inner_content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().upper()
                value = value.strip()
                
                if key in ['CITIES', 'MARKETS']:
                    if value == '*' or value.upper() == 'ALL':
                        config[key] = ['*']
                    else:
                        # Split by comma and strip
                        items = [item.strip() for item in value.split(',') if item.strip()]
                        config[key] = items
                        
        print(f"🔧 Loaded TTS Config: Cities={config['CITIES']}, Markets={config['MARKETS']}")
        return config, remaining_template.strip()
        
    except Exception as e:
        print(f"⚠️ Error parsing config block: {e}")
        return config, template_content

def load_json_data(filename):
    """Loads JSON data from the output directory."""
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {filename}: {e}")
            return {}
    else:
        print(f"Warning: {filename} not found in {OUTPUT_DIR}")
        return {}

def load_prompt_template(filename='tts_prompt.txt'):
    """Loads prompt template from a file and parses config."""
    try:
        with open(os.path.join(INPUT_DIR, filename), 'r', encoding='utf-8') as f:
            content = f.read()
            return parse_config(content)
    except FileNotFoundError:
        print(f"Error: {filename} not found.")
        return None, None

def filter_data(data, allowed_keys):
    """Filters dictionary to only include allowed keys. If allowed is ['*'] or empty, returns all."""
    if not allowed_keys or '*' in allowed_keys:
        return data
        
    filtered = {}
    for key, value in data.items():
        # Check if key (e.g. "Budapest") matches any allowed item (case insensitive mostly?)
        # Let's do exact match or simple containment for now to be safe
        for allowed in allowed_keys:
            if allowed.lower() == key.lower():
                filtered[key] = value
                break
    return filtered

def _call_backend(prompt, backend='gemini', gemini_api_key=None, use_pro=True):
    """Call the selected backend with a prompt."""
    print(f"  Sending prompt to {backend} ({len(prompt)} chars)...")
    if backend == 'perplexity':
        return perplexity_client.call_with_fallback(prompt, use_pro=True)
    elif backend == 'geminipro-cli':
        return geminipro_cli_client.call_with_fallback(prompt, use_pro=True, api_key=gemini_api_key)
    elif backend == 'gemini-selenium':
        import gemini_selenium_client
        return gemini_selenium_client.call_with_fallback(prompt, use_pro=use_pro)
    else:
        return geminipro_client.call_with_fallback(prompt, use_pro=True)


def generate_weather_tts(weather_data, config, backend='gemini', gemini_api_key=None, use_pro=True):
    """Generate weather-only TTS script."""
    cfg, template = load_prompt_template('tts_prompt_idojaras.txt')
    if not template:
        return None, None
    filtered = filter_data(weather_data, (cfg or config).get('CITIES', ['*']))
    prompt = template.replace("{WEATHER_JSON}", json.dumps(filtered, ensure_ascii=False, indent=2))
    return _call_backend(prompt, backend, gemini_api_key, use_pro)


def generate_market_tts(market_data, config, backend='gemini', gemini_api_key=None, use_pro=True):
    """Generate market/crypto-only TTS script."""
    cfg, template = load_prompt_template('tts_prompt_piacok.txt')
    if not template:
        return None, None
    filtered = filter_data(market_data, (cfg or config).get('MARKETS', ['*']))
    prompt = template.replace("{MARKET_JSON}", json.dumps(filtered, ensure_ascii=False, indent=2))
    return _call_backend(prompt, backend, gemini_api_key, use_pro)


def get_news_by_importance(section_list, count, daily_output_dir):
    """Find articles from yesterday's data.json by section and descending importance (5→4→3)."""
    try:
        date_str = os.path.basename(daily_output_dir)
        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        yesterday = (date_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    except Exception:
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    yesterday_path = os.path.join(os.path.dirname(daily_output_dir), yesterday, 'data.json')
    items = _load_json_file(yesterday_path)
    if not items:
        print(f"    ⚠️ Yesterday's data.json not found: {yesterday_path}")
        return []

    def _get_section(item):
        s = item.get('section', '')
        return [s] if isinstance(s, str) else s

    matching = [it for it in items if any(sec in section_list for sec in _get_section(it))]

    for importance in [5, 4, 3]:
        candidates = [it for it in matching if it.get('importance', 0) == importance]
        if len(candidates) >= count:
            return candidates[:count]
        elif candidates:
            return candidates[:count]

    return []


def generate_intl_business_news():
    """Generate 2 international business news items via free Gemini API."""
    import gemini_api_pool

    prompt = (
        "Te egy tapasztalt magyar gazdasági újságíró vagy. Írj 2 rövid hírt (egyenként 3-4 mondat) "
        "a tegnapi nap legfontosabb nemzetközi üzleti-gazdasági eseményeiről, amelyek befolyásolták "
        "a globális piaci hangulatot.\n\n"
        "SZABÁLYOK:\n"
        "- Magyar nyelven, tárgyilagos, rádiós felolvasásra kész stílusban.\n"
        "- Konkrét számok, cégnevek, országok.\n"
        "- NE találj ki adatokat — csak valós, közismert eseményeket említs!\n"
        "- A 2 hír között hagyj 3 üres sort, hogy a felolvasó szünetet tartson.\n"
        "- Minden hír egy félkövér nélküli címmel kezdődjön, utána új sorban a szöveg.\n"
        "- NE írj 'CÍM:' előtagot, NE használj Markdown formázást, listajelet, csillagot!\n"
        "- Angol szavakat fonetikusan írd magyarul (pl. 'Nvidia' → 'Envídia', 'S&P' → 'eszendpí').\n"
        "- A kimenet KIZÁRÓLAG a 2 felolvasandó hír legyen, semmi más.\n"
    )

    print(f"  Generating 2 international business news via free Gemini API...")
    result_text, success = gemini_api_pool.generate_with_free_api(prompt)
    if not success or not result_text:
        print(f"  ⚠️ International business news generation failed")
        return ""
    return result_text.strip()


def assemble_market_tts(market_script, daily_output_dir):
    """Assemble the full market TTS: AI market analysis + crypto news + business news + generated intl news."""
    parts = []

    if market_script:
        parts.append(market_script.strip())

    # 3 crypto news from yesterday (importance 5→4→3)
    crypto_news = get_news_by_importance(['kripto'], 3, daily_output_dir)
    if crypto_news:
        parts.append("\nA kriptovaluták világából a tegnapi nap legfontosabb hírei.")
        for article in crypto_news:
            title = article.get('title', '')
            content = article.get('content', '')
            parts.append(f"\n\n\n{title}.\n\n{content}")
        print(f"  Added {len(crypto_news)} crypto news (importance {crypto_news[0].get('importance', '?')})")

    # 2 business news from yesterday (importance 5→4→3)
    biz_news = get_news_by_importance(['uzlet'], 2, daily_output_dir)
    if biz_news:
        parts.append("\n\n\nÉs most következzenek az üzleti hírek.")
        for article in biz_news:
            title = article.get('title', '')
            content = article.get('content', '')
            parts.append(f"\n\n\n{title}.\n\n{content}")
        print(f"  Added {len(biz_news)} business news (importance {biz_news[0].get('importance', '?')})")

    # +2 AI-generated international business news
    intl_news = generate_intl_business_news()
    if intl_news:
        # Clean any "CÍM:" prefixes from AI output
        import re
        intl_news = re.sub(r'(?i)^CÍM:\s*', '', intl_news, flags=re.MULTILINE)
        parts.append(f"\n\n\nA nemzetközi üzleti élet legfrissebb fejleményei.\n\n\n{intl_news}")
        print(f"  Added AI-generated international business news")

    return '\n'.join(parts)

def _load_json_file(path):
    """Safely loads a JSON file, returns [] if not found or invalid."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Error loading {path}: {e}")
        return []


def _item_matches_sections(item, target_sections):
    """Check if item's section matches one of target_sections. Handles str or list sections."""
    section = item.get('section', '')
    if isinstance(section, list):
        return any(s in target_sections for s in section)
    return section in target_sections


def _extract_titles(items, target_sections, max_count):
    """
    Extract up to max_count titles from items matching target_sections.
    Prioritises importance >= 4, then importance == 3.
    """
    found = []
    # Priority 1: importance >= 4
    for item in items:
        if _item_matches_sections(item, target_sections):
            try:
                importance = int(item.get('importance', 0))
            except:
                importance = 0
            if importance >= 4:
                title = item.get('title', '').strip()
                if title and title not in found:
                    found.append(title)
        if len(found) >= max_count:
            return found

    # Priority 2: importance == 3
    for item in items:
        if _item_matches_sections(item, target_sections):
            try:
                importance = int(item.get('importance', 0))
            except:
                importance = 0
            if importance == 3:
                title = item.get('title', '').strip()
                if title and title not in found:
                    found.append(title)
        if len(found) >= max_count:
            return found

    return found


def get_news_for_tts(daily_output_dir):
    """
    Collects news headlines for TTS from multiple sources:

    FROM YESTERDAY (data.json):
      - tech/kripto: up to 15
      - szorakozas/gamer: up to 15
      - tudomány: up to 10

    FROM TODAY (data_i4.json + data_i5.json):
      - tech/kripto: ALL
      - szorakozas/gamer: ALL
      - tudomány: ALL
      - üzlet: up to 5
    """
    try:
        date_str = os.path.basename(daily_output_dir)
        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        yesterday_obj = date_obj - datetime.timedelta(days=1)
        yesterday_dir = os.path.join(os.path.dirname(daily_output_dir), yesterday_obj.strftime('%Y-%m-%d'))
    except Exception:
        yesterday_obj = datetime.date.today() - datetime.timedelta(days=1)
        yesterday_dir = os.path.join('Output', yesterday_obj.strftime('%Y-%m-%d'))

    output_parts = []
    all_seen_titles = set()  # Avoid duplicates across sections
    final_titles = []

    def add_titles(titles):
        nonlocal all_seen_titles, final_titles
        unique = [t for t in titles if t not in all_seen_titles]
        if unique:
            all_seen_titles.update(unique)
            final_titles.extend(unique)

    # ─── YESTERDAY ───
    yesterday_items = _load_json_file(os.path.join(yesterday_dir, 'data.json'))

    if yesterday_items:
        tech_y = _extract_titles(yesterday_items, ['tech', 'kripto'], 15)
        szora_y = _extract_titles(yesterday_items, ['szorakozas', 'gamer'], 15)
        tud_y = _extract_titles(yesterday_items, ['tudomany'], 10)

        add_titles(tech_y)
        add_titles(szora_y)
        add_titles(tud_y)
    else:
        print(f"⚠️ Yesterday's data.json not found: {yesterday_dir}")

    # ─── TODAY i4 + i5 ───
    today_i4 = _load_json_file(os.path.join(daily_output_dir, 'data_i4.json'))
    today_i5 = _load_json_file(os.path.join(daily_output_dir, 'data_i5.json'))
    today_items = today_i4 + today_i5

    if today_items:
        # ALL tech/kripto from today's i4+i5
        tech_today = _extract_titles(today_items, ['tech', 'kripto'], 999)
        # ALL szorakozas/gamer from today's i4+i5
        szora_today = _extract_titles(today_items, ['szorakozas', 'gamer'], 999)
        # ALL tudomany from today's i4+i5 
        tud_today = _extract_titles(today_items, ['tudomany'], 999)
        # Max 5 uzlet from today's i4+i5
        uzlet_today = _extract_titles(today_items, ['uzlet'], 5)

        add_titles(tech_today)
        add_titles(szora_today)
        add_titles(tud_today)
        add_titles(uzlet_today)
    else:
        print(f"⚠️ Today's data_i4/i5 not found in: {daily_output_dir}")

    if not final_titles:
        return ""

    # Formatting the final output string exactly as requested
    intro = "A mai és tegnapi nap legfontosabb hírei röviden:\n"
    news_body = "\n".join([f"- {t}" for t in final_titles])
    return intro + news_body


HIRCSARDA_SOURCE = "/Users/viktorposa/Documents/Python/hircsarda_export/articles.json"
HIRCSARDA_PROMPT_FILE = os.path.join('Input', 'hircsarda_prompt.txt')
HIRCSARDA_ARCHIVE_COUNT = 1


def _fix_hircsarda_text(text):
    """Fix scraping artifacts in hircsarda articles: missing spaces, LEAD labels."""
    import re
    text = re.sub(r'(?im)^\s*LEAD\s*:\s*', '', text)
    text = re.sub(r'([a-záéíóöőúüű])([A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű])', r'\1 \2', text)
    text = re.sub(r'([.!?,;:])([A-ZÁÉÍÓÖŐÚÜŰ])', r'\1 \2', text)
    return text


def _generate_new_satirical_article():
    """Generate a fresh satirical article using free Gemini API and hircsarda_prompt.txt."""
    import gemini_api_pool

    if not os.path.exists(HIRCSARDA_PROMPT_FILE):
        print(f"  ⚠️ Hírcsárda prompt not found: {HIRCSARDA_PROMPT_FILE}")
        return None

    with open(HIRCSARDA_PROMPT_FILE, 'r', encoding='utf-8') as f:
        prompt = f.read()

    print(f"  Generating new satirical article via free Gemini API...")
    result_text, success = gemini_api_pool.generate_with_free_api(prompt)

    if not success or not result_text:
        print(f"  ⚠️ Free Gemini API failed for satirical article generation")
        return None

    return result_text.strip()


def _parse_generated_article(raw_text):
    """Parse a generated article into (title, body). Expects CÍM:/LEAD:/SZÖVEG: or similar."""
    import re
    lines = raw_text.strip().split('\n')

    title = ""
    body_lines = []
    in_body = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_body:
                body_lines.append("")
            continue

        # Detect title line
        upper = stripped.upper()
        if not title and (upper.startswith('CÍM:') or upper.startswith('**CÍM')):
            title = re.sub(r'^[\*\s]*CÍM[\*\s]*:[\*\s]*', '', stripped, flags=re.IGNORECASE).strip()
            continue
        if not title and (upper.startswith('LEAD:') or upper.startswith('**LEAD')):
            continue
        if upper.startswith('SZÖVEG:') or upper.startswith('**SZÖVEG'):
            in_body = True
            rest = re.sub(r'^[\*\s]*SZÖVEG[\*\s]*:[\*\s]*', '', stripped, flags=re.IGNORECASE).strip()
            if rest:
                body_lines.append(rest)
            continue

        # If we haven't found a title yet, first non-empty non-label line is the title
        if not title and not in_body:
            clean = re.sub(r'^\*+\s*|\s*\*+$', '', stripped)
            if clean and len(clean) > 5:
                title = clean
                continue

        # Everything after title is body
        body_lines.append(stripped)

    body = '\n'.join(body_lines).strip()

    # Fallback: if parsing failed, use first line as title, rest as body
    if not title and body:
        parts = body.split('\n', 1)
        title = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
    elif not body and title:
        # Everything ended up as title somehow
        body = title
        title = title[:80]

    return title, body


def generate_hircsarda_tts():
    """Build Hírcsárda TTS script: 1 archive article + 2 AI-generated, no AI rewriting on archive."""
    import random

    if not os.path.exists(HIRCSARDA_SOURCE):
        print(f"  ⚠️ Hírcsárda source not found: {HIRCSARDA_SOURCE}")
        return None

    with open(HIRCSARDA_SOURCE, 'r', encoding='utf-8') as f:
        all_articles = json.load(f)

    if len(all_articles) < HIRCSARDA_ARCHIVE_COUNT:
        print(f"  ⚠️ Not enough articles in Hírcsárda source")
        return None

    # 1 random archive article
    selected = random.sample(all_articles, HIRCSARDA_ARCHIVE_COUNT)
    print(f"  Selected {HIRCSARDA_ARCHIVE_COUNT} archive article(s):")
    for a in selected:
        print(f"    - {a.get('title', '?')[:60]}")

    # 2 freshly generated articles (separate API calls)
    generated_articles = []
    for i in range(2):
        print(f"  Generating new satirical article {i+1}/2...")
        raw = _generate_new_satirical_article()
        if raw:
            title, body = _parse_generated_article(raw)
            if title and body:
                print(f"    ✅ {title[:60]}")
                generated_articles.append((title, body))
            else:
                print(f"    ⚠️ Could not parse generated article {i+1}")
        else:
            print(f"    ⚠️ Generation failed for article {i+1}")

    # Build TTS script
    parts = []
    parts.append("Most pedig következzen a nap szatirikus hírblokja!\n")

    for article in selected:
        title = article.get('title', 'Ismeretlen')
        text = article.get('text', '').strip()
        if len(text) > 2000:
            text = text[:2000].rsplit('.', 1)[0] + '.'
        text = _fix_hircsarda_text(text)
        parts.append(f"{title}.\n\n{text}")

    for gen_title, gen_body in generated_articles:
        gen_body = _fix_hircsarda_text(gen_body)
        parts.append(f"{gen_title}.\n\n{gen_body}")

    if not generated_articles:
        print(f"  ⚠️ No generated articles, using archive only")

    parts.append("\nEmlékeztetünk, hogy a Hírcsárda cikkei szatirikusak, a valósághoz való hasonlóság nem a véletlen műve.")

    return '\n\n\n'.join(parts)

def main():
    print(f"TTS Generator running for: {DAILY_OUTPUT_DIR}")

    weather_data = load_json_data('idojaras.json')
    market_data = load_json_data('piacok.json')

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--use-geminipro-cli', action='store_true')
    parser.add_argument('--use-geminipro', action='store_true')
    parser.add_argument('--use-perplexity', action='store_true')
    parser.add_argument('--use-gemini-selenium', action='store_true')
    parser.add_argument('--gemini-selenium', action='store_true', help="Alias for use-gemini-selenium")
    parser.add_argument('--gemini-api-key', type=str)
    args = parser.parse_args()

    backend = 'perplexity'
    if args.use_gemini_selenium or args.gemini_selenium:
        backend = 'gemini-selenium'
    elif args.use_geminipro_cli:
        backend = 'geminipro-cli'
    elif args.use_geminipro:
        backend = 'gemini'
    elif args.use_perplexity:
        backend = 'perplexity'

    config = {}

    # --- Part 1: Weather ---
    part1_path = os.path.join(OUTPUT_DIR, 'tts_idojaras.txt')
    if os.path.exists(part1_path):
        print(f"Skipping weather TTS: {part1_path} already exists.")
    elif weather_data:
        content, model = generate_weather_tts(weather_data, config, backend, args.gemini_api_key, use_pro=True)
        if content:
            with open(part1_path, 'w', encoding='utf-8') as f:
                f.write(content.strip())
            print(f"✅ Part 1 Weather TTS saved to: {part1_path} (by {model})")
        else:
            print("❌ Weather TTS generation failed.")
    else:
        print("⚠️ No weather data, skipping Part 1.")

    # --- Part 2: Markets + crypto/business news ---
    part2_path = os.path.join(OUTPUT_DIR, 'tts_piacok.txt')
    if os.path.exists(part2_path):
        print(f"Skipping market TTS: {part2_path} already exists.")
    elif market_data:
        market_content, model = generate_market_tts(market_data, config, backend, args.gemini_api_key, use_pro=True)
        full_market = assemble_market_tts(market_content, DAILY_OUTPUT_DIR)
        if full_market:
            with open(part2_path, 'w', encoding='utf-8') as f:
                f.write(full_market)
            print(f"✅ Part 2 Market TTS saved to: {part2_path} (analysis by {model})")
        else:
            print("❌ Market TTS generation failed.")
    else:
        print("⚠️ No market data, skipping Part 2.")

    # --- Part 3: Headlines ---
    part3_path = os.path.join(OUTPUT_DIR, 'tts_headlines.txt')
    if os.path.exists(part3_path):
        print(f"Skipping headlines TTS: {part3_path} already exists.")
    else:
        news_headlines = get_news_for_tts(DAILY_OUTPUT_DIR)
        if news_headlines:
            with open(part3_path, 'w', encoding='utf-8') as f:
                f.write(news_headlines)
            print(f"✅ Part 3 Headlines TTS saved to: {part3_path}")
        else:
            print("⚠️ No headlines found.")

    # --- Part 4: Hírcsárda (3 archív + 1 generált, nincs AI átdolgozás) ---
    part4_path = os.path.join(OUTPUT_DIR, 'tts_hircsarda.txt')
    if os.path.exists(part4_path):
        print(f"Skipping Hírcsárda TTS: {part4_path} already exists.")
    else:
        hircsarda_content = generate_hircsarda_tts()
        if hircsarda_content:
            with open(part4_path, 'w', encoding='utf-8') as f:
                f.write(hircsarda_content)
            print(f"✅ Part 4 Hírcsárda TTS saved to: {part4_path}")
        else:
            print("⚠️ Hírcsárda TTS generation failed.")


    # --- Egyéni TTS elemek ---
    tts_items = load_tts_items()
    custom_items = load_custom_tts_items()
    for item in custom_items:
        item_id = item.get("id", "")
        if not item_id:
            continue
        if tts_items.get(item_id) is False:
            print(f"\u23ed\ufe0f Kihagyva: {item_id} (letiltva a pipeline_settings.json-ban)")
            continue
        out_path = os.path.join(OUTPUT_DIR, f"tts_{item_id}.txt")
        if os.path.exists(out_path):
            print(f"\u23e9 Kihagyva (már kész): tts_{item_id}.txt")
            continue
        prompt = item.get("prompt", "").strip()
        if not prompt:
            print(f"\u26a0\ufe0f Kihagyva: {item_id} — üres prompt")
            continue
        data_file = item.get("data_file", "")
        if data_file:
            data = load_json_data(data_file)
            prompt = prompt + "\n\nAdatok (JSON):\n" + json.dumps(data, ensure_ascii=False, indent=2)
        result, model = _call_backend(prompt, backend, args.gemini_api_key, use_pro=True)
        if result:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(result.strip())
            print(f"\u2705 Egyéni TTS \'{item_id}\' kész ({model}): {out_path}")
        else:
            print(f"\u274c Egyéni TTS \'{item_id}\' sikertelen.")

if __name__ == "__main__":
    main()
