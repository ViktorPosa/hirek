"""
hirkep_gen_worker.py — párhuzamos illusztráció-generáló driver a pipeline-hoz.

A persistent loop LEVÁLASZTOTT subprocessként indítja (start_new_session=True, nincs
.wait()), a summarizer lépés UTÁN — ekkor a közös gemini_profile szabad. A worker a
HirkepGenerator engine.run()-ját hívja, ami ÁLTALÁNOS, újrafelhasználható képeket készít
a ~/Desktop/Hírképek/ könyvtárba (a data.json-t NEM írja). Ezeket egy külön, gyors merge
lépés (image_generation_merge.py) illeszti tag-alapon a kép nélküli cikkekhez.

Koordináció:
- Közös Gemini-zár: flock EX a .gemini.lock-on, hogy a summarizer és a generátor SOSE
  hajtsa egyszerre a közös Chrome-profilt. A pipeline-folyam nem blokkol a workerre
  (leválasztott), csak a summarizer LÉPÉS várhat a zárra, ha a worker túlfut.
- VPN-policy: csak a TTS kapcsolhat VPN-t. Az engine.run finally-je VPN-t KAPCSOLNA vissza
  (engine.py:414) — ezt semlegesítjük (vpn_control.connect -> no-op). A start-i disconnect
  és az _ensure_vpn_off marad, mert a Geminihez VPN-OFF kell.
- Watchdog: max-runtime után SIGTERM magának -> az engine SIGTERM->KeyboardInterrupt
  kezelője lefuttatja a finally-t (Chrome bezárása). A flock csak ezután szabadul fel.
"""

import argparse
import os
import sys
import time
import signal
import json
import glob
import threading
import datetime
from pathlib import Path

HIRKEP_DIR = str(Path("/Volumes/Apple/DerűsProjekt/HirkepGenerator"))
SCRAPERHU_DIR = str(Path("/Volumes/Apple/DerűsProjekt/ScraperHU"))
GEMINI_LOCK_PATH = os.path.join(SCRAPERHU_DIR, ".gemini.lock")

for _p in (HIRKEP_DIR, SCRAPERHU_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[hirkep-worker {ts}] {msg}", flush=True)


def _acquire_gemini_lock(wait_seconds):
    """Blocking-with-deadline EX flock on the shared Gemini lock. Returns the open
    file object (keep it alive to hold the lock) or None if not acquired in time."""
    import fcntl
    fh = open(GEMINI_LOCK_PATH, "w")
    deadline = time.time() + wait_seconds
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _log(f"Gemini-zár megszerezve ({GEMINI_LOCK_PATH}).")
            return fh
        except (BlockingIOError, OSError):
            if time.time() >= deadline:
                _log(f"Gemini-zár foglalt {wait_seconds}s után is — kihagyom ezt a kört.")
                fh.close()
                return None
            time.sleep(2)


def _release_gemini_lock(fh):
    import fcntl
    if not fh:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass
    _log("Gemini-zár felszabadítva.")


def _count_pngs(image_dir):
    try:
        return len(glob.glob(os.path.join(image_dir, "*.png")))
    except Exception:
        return 0


def _write_last_run(image_dir, generated, note=""):
    try:
        Path(image_dir).mkdir(parents=True, exist_ok=True)
        with open(os.path.join(image_dir, ".last_run.json"), "w", encoding="utf-8") as f:
            json.dump({
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "generated": generated,
                "note": note,
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Párhuzamos HirkepGenerator driver (Gemini-zárral, VPN-semlegesítéssel)."
    )
    parser.add_argument("--data-root", default=None,
                        help="A hírek gyökere (config.OUTPUT_DIR). Alap: a HirkepGenerator configé.")
    parser.add_argument("--count", type=int, default=3, help="Hány képet generáljon (alap: 3).")
    parser.add_argument("--max-runtime", type=int, default=240, dest="max_runtime",
                        help="Kemény időkorlát mp-ben (alap: 240). Utána SIGTERM->tiszta leállás.")
    parser.add_argument("--min-overlap", type=int, default=None, dest="min_overlap",
                        help="Tag-dedup küszöb (alap: a config DEFAULT_MIN_OVERLAP).")
    parser.add_argument("--min-minutes", type=float, default=1.0, dest="min_minutes",
                        help="Leggyorsabb tempó perc/kép (1..10). Alap: 1.")
    parser.add_argument("--lock-wait", type=int, default=120, dest="lock_wait",
                        help="Ennyi mp-ig várunk a Gemini-zárra, utána kilépünk (alap: 120).")
    args = parser.parse_args()

    # --- függőségek importálása ---
    try:
        import vpn_control  # ScraperHU
        import config as hk_config  # HirkepGenerator/config.py
        import engine as hk_engine  # HirkepGenerator/engine.py
    except Exception as e:
        _log(f"HIBA: import sikertelen ({e}). Kilépés.")
        return 0  # ne indíts recovery-t; ez opcionális lépés

    min_overlap = args.min_overlap if args.min_overlap is not None else hk_config.DEFAULT_MIN_OVERLAP
    min_interval = int(max(60, min(600, round(args.min_minutes * 60))))

    # Kimeneti mappa (a data.json gyökere) felülírása.
    if args.data_root:
        hk_config.OUTPUT_DIR = Path(args.data_root)
    _log(f"OUTPUT_DIR={hk_config.OUTPUT_DIR}  HIRKEPEK_DIR={hk_config.HIRKEPEK_DIR}  "
         f"count={args.count} min_overlap={min_overlap} min_interval={min_interval}s "
         f"max_runtime={args.max_runtime}s")

    # VPN-reconnect SEMLEGESÍTÉSE (policy: csak a TTS kapcsolhat VPN-t).
    # A vpn_control MODUL attribútumát írjuk át, így minden importáló (engine +
    # gemini_image_client) számára no-op lesz a connect; a disconnect érintetlen.
    try:
        vpn_control.connect = lambda *a, **k: None
        _log("vpn_control.connect semlegesítve (no-op).")
    except Exception as e:
        _log(f"FIGYELEM: nem sikerült semlegesíteni a vpn_control.connect-et: {e}")

    # --- Gemini-zár ---
    lock_fh = _acquire_gemini_lock(args.lock_wait)
    if lock_fh is None:
        return 0

    before = _count_pngs(str(hk_config.HIRKEPEK_DIR))

    # --- Watchdog: max-runtime után tiszta leállás ---
    stop_event = threading.Event()

    def _watchdog():
        if stop_event.wait(args.max_runtime):
            return  # engine.run már végzett, ne lőjünk
        _log(f"WATCHDOG: {args.max_runtime}s letelt — SIGTERM a tiszta leállításhoz.")
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            pass

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    # engine.run a FŐ szálban fut, mert saját SIGTERM-kezelőt telepít (csak fő szálban
    # lehet). A watchdog SIGTERM-jét az engine KeyboardInterrupt-tá alakítja, és a finally
    # bezárja a Chrome-ot (a connect már no-op).
    try:
        hk_engine.run(count=args.count, min_overlap=min_overlap,
                      retry=False, min_interval=min_interval)
    except KeyboardInterrupt:
        _log("engine.run megszakítva (watchdog/kézi).")
    except SystemExit:
        raise
    except Exception as e:
        _log(f"engine.run hiba: {e}")
    finally:
        stop_event.set()  # watchdog leállítása

    after = _count_pngs(str(hk_config.HIRKEPEK_DIR))
    generated = max(0, after - before)
    _log(f"Generálás kész: ~{generated} új kép (index: {before}->{after}).")
    _write_last_run(str(hk_config.HIRKEPEK_DIR), generated)

    # A zárat csak a session lezárása UTÁN engedjük el (engine finally már bezárta a Chrome-ot).
    _release_gemini_lock(lock_fh)

    # VPN-backstop: biztosan maradjon OFF (a connect no-op, de a disconnect valódi).
    try:
        vpn_control.disconnect(wait=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
