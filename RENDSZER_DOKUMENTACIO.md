# Nemrossz3 Hírfeldolgozó Rendszer - Teljes Technikai Dokumentáció

Ez a dokumentáció részletesen leírja a teljes hírgyűjtő, -elemző és -feldolgozó pipeline működését, a programfájlok feladatait, valamint a köztük lévő kapcsolatokat és adatáramlást.

## 1. Rendszer Architektúra Áttekintés

A rendszer egy többszálú (multi-threaded), többszörös AI háttérrel (multi-backend) rendelkező, automatizált hírügynökségi bot. Feladata, hogy különböző RSS forrásokból összegyűjtse a híreket, kiszűrje a negatív/politikai/kattintásvadász tartalmakat, majd mesterséges intelligencia segítségével koherens, magyar nyelvű összefoglalókat generáljon, kiegészítve címkékkel (tagek), képekkel és generált tartalmakkal (időjárás, tőzsde, TTS felolvasás).

**Fő Adatáramlás (Data Flow):**
`RSS Források -> Letöltés -> Szűrés -> AI Összegzés -> Kép letöltés -> Utófeldolgozás -> Weboldalra kész JSON adatok`

---

## 2. Vezérlés és Orkesztráció (Core Orchestration)

### `run_pipeline.py`
A rendszer fő belépési pontja. Ez az orkesztrátor vezérli a teljes napi folyamatot lépésről lépésre.
- **Működés:** Beolvassa a parancssori argumentumokat (pl. `--skip-weather`, `--multi-model`), létrehozza a napi kimeneti mappákat (`Output/YYYY-MM-DD`), és alfolyamatokként (subprocess) elindítja a pipeline többi szkriptjét.
- **Hívott fájlok:** Szinte minden más script (pl. `news_filter.py`, `summarizer_json.py`, `weather_generator.py`).
- **Fő funkciók:**
  - `run_step(script_name, ...)`: Egy adott Python szkript biztonságos futtatása és a kimenetének naplózása.
  - `main()`: A logikai sorrend (Generátorok -> Szűrés -> Összegzés -> Utófeldolgozás) végrehajtása.

### `backend_orchestrator.py`
Az AI modellek közötti terheléselosztó és hibatűrő (failover) rendszer. 
- **Működés:** Mivel a rendszer többféle AI szolgáltatót használ, ez a modul dönti el, hogy egy adott kérés hova menjen. Ha egy szolgáltató kiesik (timeout, captcha), automatikusan újrapróbálja a kérést egy alternatív szolgáltatónál.
- **Hívott fájlok:** Az összes AI kliens (`gemini_api_client.py`, `perplexity_client.py`, `deeperseek_client.py`, stb.).
- **Fő funkciók:**
  - `choose_and_run_backend(...)`: A legfontosabb metódus. Kiválasztja az elsődleges AI-t (pl. a gyorsaság vagy minőség alapján), meghívja, és ha hibázik, továbbadja a másodlagosnak (failover logika).
  - Kezeli a Json Response javítást (ha az AI hibás JSON-t ad vissza formátumként).

### `pipeline_menu.py`
Egy interaktív parancssori (CLI) menürendszer, amivel a felhasználó kényelmesen konfigurálhatja a rendszert (szálak száma, használandó AI modellek) anélkül, hogy a kódot vagy parancssori argumentumokat írna át.

---

## 3. Adatgyűjtés és Előszűrés (Ingestion & Filtering)

### `news_filter.py`
- **Feladat:** Az `Input/input.txt`-ben megadott RSS feedek beolvasása és letöltése.
- **Működés:** Végigmegy az összes RSS-en (pl. Index, Telex), kinyeri az új cikkek linkjeit, átküldi őket egy alap AI ellenőrzésen (pozitív/negatív tartalom szűrés), és csak a megfelelőeket engedi tovább.
- **Hívott fájlok:** `google_rss_resolver.py` (a Google News linkek dekódolására), `history_manager.py` (hogy ne dolgozzon fel már látott híreket).

### `google_rss_resolver.py`
- **Feladat:** A Google News speciális, base64-al kódolt URL-jeit (pl. `news.google.com/rss/articles/CBMi...`) feloldja a valós forrás URL-re (pl. `telex.hu/cikk...`).
- **Működés:** HTML elemzéssel vagy belső base64 dekódolással kitermeli a valódi cél-linket.

### `link_filter.py` és `link_dedup.py`
- **Feladat:** Szigorú URL-alapú szűrés és több napon átívelő duplikáció-ellenőrzés.
- **Működés:** Szótár alapján kiszűri a politikai és negatív hatású URL-eket (pl. "orban", "haboru", "gyilkossag"). A `link_dedup.py` ellenőrzi az elmúlt pár nap adatait, és ha szemantikailag vagy URL szerint azonos cikket talál, eldobja az újat.

### `sorter.py`
- **Feladat:** A nyers (már szűrt) linkeket kategóriákba (Tech, Sport, Üzlet, Szórakozás stb.) rendezi a tartalmuk szerint.
- **Kimenet:** Kategória specifikus `.txt` fájlok az Output mappába.

---

## 4. Feldolgozás és Összegzés (Processing Core)

### `summarizer_json.py` (A Rendszer Szíve)
- **Feladat:** Ez végzi a cikkek tényleges szövegének letöltését, majd elküldi őket az AI-nak egy részletes, anti-kattintásvadász, strukturált magyar JSON összefoglaló elkészítésére.
- **Működés:** Erősen párhuzamosított architektúrát használ (Producer-Consumer modell).
  - Közös `LinkManager` feladata, hogy olvassa a feldolgozatlan linkeket.
  - A letöltő szálak (`batch_worker`) megszerzik a szöveget.
  - Az elemző szálak (`summarizer_worker`) meghívják az AI-t a `backend_orchestrator`-on keresztül.
  - A képmentő szálak (`image_worker`) aszinkron töltik fel a képeket ImgBB-re.
  - Végül a `ResultManager` írja vissza az eredményt a `data.json` fájlba, és végzi a "git push" folyamatot is.
- **Fő Osztályok és Funkciók:**
  - `LinkManager`, `ArticleBuffer`, `ResultManager`
  - `process_batch()`: Adott linkek batch-elt (csoportosított) elküldése az AI felé.
  - `pair_news_to_batch()`: Összeilleszti az AI által visszaadott eredményt a letöltött forrás adatokkal.

### `article_downloader.py`
- **Feladat:** Bármilyen adott hír URL-jéből kinyeri az olvasható szöveget, címet és a cikk borítóképének linkjét.
- **Működés:** Modern web-scraping technológiákat használ sorrendben (visszaesési hierarchia): először a beépített `trafilatura` könyvtárat, ha ez sikertelen, akkor a `newspaper3k`-t, végső esetben pedig a `BeautifulSoup4`-et használja. Kezeli a Cloudflare-rel védett weboldalakat is `curl_cffi` segítségével.

### `image_downloader.py`
- **Feladat:** A webről lekapart nyers képlinkeket letölti, ellenőrzi, majd feltölti a saját ImgBB (vagy más) tárhelyre, így biztosítva, hogy a hírportálon a kép sosem tűnik el, és gyorsan betöltődik.

---

## 5. Mesterséges Intelligencia Kliensek (AI Clients)

Mivel az LLM szolgáltatások instabilak lehetnek, a rendszer számos klienst ismer, amiket az Orchestrator dinamikusan váltogat.

- **`gemini_api_client.py`:** A hivatalos Google Gemini REST API hívására szolgál API kulccsal. Rendelkezik JSON automatikus javító mechanizmussal (JSON Repair), ha a modell pontatlan szintaxist adna vissza.
- **`gemini_selenium_client.py`:** Böngésző automatizációval (Selenium) vezérli a gemini.google.com weboldalt, ezáltal ingyenesen érheti el a legújabb Gemini modelleket webes fiókon keresztül.
- **`perplexity_client.py`:** A perplexity.ai weboldalt hívja meg API kérések imitálásával (Cookie alapú bejelentkezéssel). Képes a Pro modellek használatára is.
- **`deeperseek_client.py`:** A kínai DeepSeek modellt integrálja. Kezeli a Cloudflare ellenőrzéseket és a munkamenet sütijeit.
- **`g4f_client.py`:** A "GPT4Free" ökoszisztémát használja fallback-ként nyílt AI modellek elérésére.

---

## 6. Generátorok (Kiegészítő Tartalom)

Ezek a szkriptek önálló, új adatokat állítanak elő.

- **`weather_generator.py`:** Beolvassa a megadott európai/magyar városokat, és az AI segítségével (általában Gemini) egy vicces, de pontos 4-5 mondatos időjárás-előrejelzést ír a napra vonatkozóan.
- **`market_generator.py`:** A gazdasági és kriptovaluta piac aktuális helyzetéről kér összefoglalót ("Mit csinál ma a Bitcoin és a dollár?").
- **`tts_generator.py` (Text-to-Speech):** Egy dinamikus szkript, ami beolvassa a legfrissebb híreket, a piacokat és az időjárást, és egy élő rádiós bemondó stílusában ír belőlük egy hangzatos szövegkönyvet. Képes ezt MP3-ba is lekonvertálni a Google TTS rendszerén keresztül.
- **`tag_generator_json.py`:** Gyakoriságelemzést végez a lefordított hírek kategóriáin, és kiemeli a legrelevánsabb hashtag-eket (pl. `#Apple`, `#Magyarorszag`), amit a weboldal UI-ján jelenít meg címkefelhőként.
- **`ajanlott_generator.py`:** A meglévő JSON adatbázis alapján megkeresi azokat a cikkeket, amik a legérdekesebbek lehetnek, és "Ajánlott cikkek" jelzővel látja el a kimenetet.

---

## 7. Adatbázisok, Állapotkezelés és Segédprogramok

### `history_manager.py` (A Memória)
- **Feladat:** Minden URL-t és hír-státuszt nyilvántart a `history.json` nevű gigantikus cache fájlban. 
- **Működés:** Megakadályozza, hogy a rendszer ugyanazt a cikket többször is feldolgozza vagy elküldje a fizetős AI API-knak. Itt kerülnek rögzítésre a "FILTERED" (szemét) és "POSITIVE" (feldolgozandó) státuszok.

### `cookie_manager.py` és `*extract.py` szkriptek
- **Feladat:** A böngésző automatizáció alapú AI kliensek életben tartása.
- **Működés:** Segítenek kinyerni a felhasználó saját Google Chrome profiljából a bejelentkezési sütiket (cookies), így a robot átveszi a hitelesítést.

### `section_validator.py`
- Biztosítja, hogy az AI által visszaadott adatokban a "Hírszekció" mező mindig egy valid, a frontend által megegyező kategóriába (pl. "Tech", "Sport", "Szórakozás") mutasson. Ha az AI véletlen "Technológia"-t ír "Tech" helyett, kijavítja.

### `filter_news.py` és `clear_negative_history.py`
- Utólagos takarítók. A `filter_news.py` a már lefordított, kész magyar szöveget szkenneli át, hátha az AI "hallucinált" valami nem kívánatos vagy politikai tartalmat, és törli azt a listából. A clear szkript pedig a fals-pozitív hibákat javítja a history fájlban.

---

## Összegzés

A teljes logika tehát egy rendkívül autonóm gépezetként épül fel: 
1. `run_pipeline` irányít.
2. A Letöltők összeszedik az anyagot.
3. A Szűrők vigyáznak a tartalom minőségére.
4. A `summarizer_json` kiküldi az adatokat a `backend_orchestrator` tranzakcióival a felhőbe.
5. Végül a generátorok bedobják az extrákat (időjárás, rádió/TTS), és az utófeldolgozók megjavítják az apró hibákat egy végső, weboldalon publikálható `data.json` fájllá.
