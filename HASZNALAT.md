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
```

### Csak újra-formázás és címkék
```bash
py run_pipeline.py --skip-filter --skip-sort --skip-linkfilter --skip-summarize
```

---

## Pipeline lépései

| # | Script | Leírás | Bemenet | Kimenet |
|---|--------|--------|---------|---------|
| 1 | `mimofilter.py` | RSS feedek letöltése, AI szűrés | `Input/input.txt` | `Output/DÁTUM/output.txt` |
| 2 | `sorter.py` | Kategóriákba rendezés | `output.txt` | `tech.txt`, `uzlet.txt`, stb. |
| 3 | `link_filter.py` | URL alapú negatív szűrés | Kategória fájlok | Szűrt fájlok |
| 4 | `summarizer.py` | AI összegzés magyarul | Kategória fájlok | `Tartalom/*.txt` |
| 5 | `post_processor.py` | Formázás, hashtag-ek | `Tartalom/*.txt` | Frissített fájlok |
| 6 | `filter_news.py` | Tartalom alapú szűrés | `Tartalom/*.txt` | Szűrt fájlok |
| 7 | `tag_generator.py` | Címkék generálása | `Tartalom/*.txt` | `*_cimke.txt` fájlok |

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

## Git

Push a módosítások:
```bash
git add .
git commit -m "Napi frissítés"
git push
```
