# Nemrossz3 - Hírfeldolgozó Pipeline

## Áttekintés

Ez a rendszer automatikusan:
1. **Letölti** a híreket RSS feedekből
2. **Szűri** a negatív/politikai tartalmakat
3. **Összegzi** magyarul, AI segítségével
4. **Kategorizálja** és **formázza** a kimeneteket
5. **Generálja** a címkéket

---

## Könyvtárszerkezet

```
Nemrossz3/
├── Input/
│   ├── input.txt          # API kulcs + RSS feedek listája
│   ├── summarize.txt      # Összegzési prompt
│   └── fix_category_prompt.txt  # Kategória-javító prompt
├── Output/
│   └── 2026-01-05/        # Napi mappa
│       ├── output.txt     # Szűrt linkek
│       ├── tech.txt       # Kategorizált linkek
│       └── Tartalom/
│           ├── tech.txt   # Összegzett cikkek
│           └── tech_cimke.txt  # Címkék
├── run_pipeline.py        # Fő futtatóscript
├── tts_generator.py       # TTS Hírgenerátor
└── history.json           # Feldolgozott linkek cache
```

---

## Használat

### Teljes pipeline futtatása
```bash
py run_pipeline.py
```

### Lépések kihagyása
```bash
py run_pipeline.py --skip-filter      # RSS letöltés kihagyása
py run_pipeline.py --skip-sort        # Rendezés kihagyása
py run_pipeline.py --skip-linkfilter  # URL szűrés kihagyása
py run_pipeline.py --skip-summarize   # Összegzés kihagyása
py run_pipeline.py --skip-process     # Formázás kihagyása
py run_pipeline.py --skip-newsfilter  # Tartalom szűrés kihagyása
py run_pipeline.py --skip-tags        # Címkék kihagyása
py run_pipeline.py --skip-tts         # TTS hírgenerálás kihagyása
```

### Csak újra-formázás és címkék
```bash
py run_pipeline.py --skip-filter --skip-sort --skip-linkfilter --skip-summarize
```

---

## Pipeline lépései

| # | Script | Leírás | Bemenet | Kimenet |
|---|--------|--------|---------|---------|
| 1 | `news_filter.py` | RSS feedek letöltése, AI szűrés | `Input/input.txt` | `Output/DÁTUM/output.txt` |
| 2 | `sorter.py` | Kategóriákba rendezés | `output.txt` | `tech.txt`, `uzlet.txt`, stb. |
| 3 | `link_filter.py` | URL alapú negatív szűrés | Kategória fájlok | Szűrt fájlok |
| 4 | `summarizer.py` | Cikkek letöltése, AI összegzés | Kategória fájlok | `Tartalom/*.txt` |
| 5 | `post_processor.py` | Formázás, hashtag-ek | `Tartalom/*.txt` | Frissített fájlok |
| 6 | `filter_news.py` | Tartalom alapú szűrés | `Tartalom/*.txt` | Szűrt fájlok |
| 7 | `tag_generator.py` | Címkék generálása | `Tartalom/*.txt` | `*_cimke.txt` fájlok |
| 8 | `tts_generator.py` | Rádiós hírblokk generálása | `idojaras.json`, `piacok.json` | `tts_news.txt` |
| 9 | `article_downloader.py` | Cikk tartalom letöltése (belső modul) | URL lista | Cikk szöveg objektumok |

---

## Konfiguráció

### `Input/input.txt`
```
API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

PROMPT=
Te egy hírelemző vagy...

FEEDS:
https://index.hu/24ora/rss/
https://telex.hu/rss
...
```

### `Input/summarize.txt`
Az összegzési prompt template. A `[gemini_summarize]` marker után kezdődik a tényleges prompt.

### `Input/tts_prompt.txt`
A TTS hírblokk generálásához használt prompt. Itt állíthatod be a rádiós stílust.

---

## Kimeneti formátum

### Összegzett cikk (`Tartalom/*.txt`)
```
[Hírszekció] Tech
[Cím] Az új iPhone bemutatkozik
[Tagek] #Apple, #iPhone, #tech
[Tartalom] Az Apple bemutatta...
[Forráslink] https://example.com/cikk
[Hír szerzője] Example News
{{kép linkje}} https://example.com/kep.jpg
```

### Címkefájl (`*_cimke.txt`)
```
#Apple, #Tesla, #Bitcoin, #AI, #robotika
```

---

## Beállítások a Menüben

A `py pipeline_menu.py` indításával elérhető menüben új opciók jelentek meg:

- **Summarizer Batch Size**: Beállítható, hogy egyszerre hány cikket küldjön a rendszer az AI-nak összegzésre.
  - Alacsonyabb érték (pl. 3-5): Stabilabb, részletesebb válaszok, de lassabb.
  - Magasabb érték (pl. 10-15): Gyorsabb, de a modellek néha kihagyhatnak híreket.
  - A beállítás a `Global Settings` menüpont alatt érhető el.

---

## Szűrés

### URL szűrés (`link_filter.py`)
Kiszűri a linkeket, amelyek URL-jében ezek szerepelnek:
- Politika: `orban`, `trump`, `putin`, `valasztas`
- Háború: `haboru`, `ukrajna`, `venezuela`
- Negatív: `halal`, `gyilkossag`, `katasztrofa`

### Tartalom szűrés (`filter_news.py`)
Kiszűri a cikkeket, amelyek szövegében ezek szerepelnek:
- `Orbán Viktor`, `Magyar Péter`, `Mészáros Lőrinc`
- `háború`, `halál`, `tragédia`, `börtön`

---

## Cache

A `history.json` fájl tárolja a már feldolgozott linkeket. Ha egy link már szerepel benne, nem kerül újra feldolgozásra.

---

## Interaktív chat
```bash
py chat_mimo.py
```
Teszteléshez használható a Mimo API-val való közvetlen kommunikációra.

---

## Hibaelhárítás

| Hiba | Megoldás |
|------|----------|
| `API_KEY not found` | Ellenőrizd az `Input/input.txt` fájlt |
| `421 Misdirected Request` | Átmeneti API hiba, próbáld újra |
| `Timeout` | A timeout 450 másodperc, várj tovább |
| `Directory not found` | Ellenőrizd az `Output/` mappa létezését |

---

## TTS Hírgenerátor

A rendszer egy rádiós stílusú napi hírblokkot is generál az időjárás és piaci adatok alapján.

### Kimenet
- `Output/DÁTUM/tts_news.txt`: Felolvasásra kész szöveg.

### Használat
A pipeline futtatásakor automatikusan lefut, kivéve ha a `--skip-tts` kapcsolót használod.

```bash
# Teljes futtatás (TTS-sel)
python run_pipeline.py

# TTS kihagyása
python run_pipeline.py --skip-tts

# Csak TTS generálás (teszteléshez)
python tts_generator.py --use-geminipro-cli
```

### Prompt testreszabása
A stílust és formátumot az `Input/tts_prompt.txt` fájlban módosíthatod.

---

## Gemini CLI Wrapper

Ha a Gemini API cookie-k lejártak, vagy automatikusan szeretnéd használni a böngésződben bejelentkezett Gemini fiókot:

1. Jelentkezz be a [gemini.google.com](https://gemini.google.com) oldalon Chrome/Firefox böngészőben.
2. Használd a `--geminipro-cli-summarize` kapcsolót a pipeline-ban:

```bash
python run_pipeline.py --geminipro-cli-summarize
```

Ez a `geminipro_cli_tool.py` segítségével kinyeri a sütiket és a Gemini Pro modellt használja.

---

## Git

Push a módosítások:
```bash
git add .
git commit -m "Napi frissítés"
git push
```
