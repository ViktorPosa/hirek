# Lokális kép-egyeztetés (Local Image Matching)

Ez a mappa **hu** nyelvű scraper. A képek kulcsszó-listái közül a `gemini_keywords_hu` listát használja.

## 1. Mit csinál

Ha be van kapcsolva, a funkció a letöltött (távoli) kép helyett megpróbál egy **lokális** képet találni a `/Users/viktorposa/Desktop/Hírképek/` mappából, és a hír kép-linkjét erre a lokális képre írja felül. Minden lokális kép egy `<név>.png` + `<név>.json` páros, ahol a JSON tartalmazza a sorrendezett `gemini_keywords_hu` / `gemini_keywords_en` / `gemini_keywords_de` kulcsszólistákat. Az egyeztetés a hír `tags` mezőjét (a scraper saját nyelvén) veti össze a képek kulcsszavaival. **Alapból KIKAPCSOLVA.**

## 2. Hogyan kell bekapcsolni

Az `Input/pipeline_settings.json` fájlban a `pipeline` blokk alatt állítsd `true`-ra (most `false`):

```json
"pipeline": {
    ...
    "image_upload": true,
    "local_image_matching": true
}
```

## 3. Az algoritmus röviden

- Minden képnél a kulcsszó-lista `i`. eleme **pozíció-súlyt** kap: `1/(i+1)` (a lista elején lévő kulcsszó többet ér).
- `match_count` = hány kulcsszó egyezik a hír tag-jeivel.
- `weighted_score` = az egyező kulcsszavak súlyainak összege.
- `H_n` = a lista harmonikus összege (`1/1 + 1/2 + ... + 1/n`).
- `ratio = weighted_score / H_n`.
- **Kapu:** legalább `MIN_KEYWORD_MATCHES` egyezés **ÉS** `ratio >= RATIO_THRESHOLD`.
- A kaput átlépő képek közül a legmagasabb `weighted_score`-ú nyer (döntetlennél magasabb `match_count`, majd betűrend).

## 4. Hangolás (sweet spot)

A `local_image_matcher.py` tetején két konstans állítható kézzel:

- `MIN_KEYWORD_MATCHES` (jelenleg `5`)
- `RATIO_THRESHOLD` (jelenleg `0.0`)

Ezeket **kézzel kell belőni**. Ha túl sok gyenge/pontatlan találat van, **emeld a küszöböt** (növeld a `MIN_KEYWORD_MATCHES`-t és/vagy a `RATIO_THRESHOLD`-ot). A `0.0` ratio azt jelenti, hogy egyelőre semmit sem szűr a ratio.

## 5. Mi történik találatkor

Találat esetén a lokális PNG bemásolódik az `ImageRepo`-ba, és a hír kép-URL-je erre a képre íródik (a letöltés kimarad). **Ha nincs találat, marad a letöltött (távoli) kép** — a funkció ilyenkor semmit sem ront el.

## 6. Jelenlegi állapot / feltétel

A képekhez **3-nyelvű** `gemini_keywords_hu/en/de` kulcsszólista kell. A régi, egy-listás JSON-ok **kimaradnak** az indexből (nincs nyelvközi tartalék) — ezeket újra kell tagelni. Amíg kevés a felkészített lokális kép, **hagyd kikapcsolva**, vagy kapcsold be és **figyeld a logot** (a "Local image match" sorokat).

## 7. Kikapcsolás

Állítsd vissza a `local_image_matching` értékét `false`-ra az `Input/pipeline_settings.json`-ban.
