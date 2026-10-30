# ScraperHU – Ágens Architektúra & Munkaszabályzat

## Token-Takarékos Kódintelligencia Protokoll (XERJ + Serena)
- **XERJ keresés először:** Ismeretlen kód, definíció vagy mechanizmus keresésekor használd: `xerj def "<symbol>"` vagy `xerj search "<passzus>"`.
- **Serena AST navigáció:** Fájlstruktúra áttekintésére `get_symbols_overview`, célzott kódolvasásra `find_symbol` (include_body: true).
- **Szigorú tiltás:** TILOS teljes forrásfájlokat vagy nyers adatdumpokat kontextusba olvasni! Mindig célzott sorszámtartományt (`offset`/`limit`) vagy szimbólumszintű szerkesztést (`replace_symbol_body`, `replace_content`) használj.
- **Éles környezeti biztonsági kapu:** Netlify-ra vagy külső tárhelyre deployolni (`netlify deploy`, `git push`) KIZÁRÓLAG kifejezett felhasználói jóváhagyással szabad!
