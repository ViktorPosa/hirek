"""
shared_image_cache.py — cross-worker shared IMAGE CACHE (stdlib-only).

PURPOSE
    Three independent language workers (hu/en/de) may each process the SAME
    article (same sourceLink) from a feed shared across their Input/input.txt.
    Each summarizes it in its own language and writes to its own data.json — but
    the PICTURE should be identical across languages. This module resolves an
    article's image ONCE and lets the other workers reuse the identical file.

    "Same image" = the identical PICTURE file. The 3 sites publish to different
    image repos, so callers copy the cached file into their own ImageRepo; this
    cache just stores the resolved source file + a normalized-link index.

STORAGE (shared, absolute — common to all 3 workers)
    index : /Volumes/Apple/DerűsProjekt/ScraperOmni/shared_image_cache/index.json
    files : /Volumes/Apple/DerűsProjekt/ScraperOmni/shared_image_cache/files/
    Directories are created lazily on first write.

CONCURRENCY
    A single shared lock file is flock()'d (fcntl) around every read/write so
    concurrent workers never corrupt the index. Index writes are atomic
    (temp file + os.replace under the lock).

SAFETY
    Every public function is fully try/except-wrapped: any failure is a no-op
    that returns None. It NEVER raises into the caller and NEVER breaks the
    pipeline. If the flag that gates the caller is off, this module is unused.

API
    normalize_link(url) -> str
    get(source_link)    -> local cached file path (str) | None
    put(source_link, image_path) -> cached file path (str) | None
"""

import os
import json
import hashlib
import shutil
import time

try:
    import fcntl  # POSIX only; all 3 workers run on macOS
    _HAVE_FCNTL = True
except Exception:
    _HAVE_FCNTL = False

# --- shared, absolute storage locations (common to all 3 workers) ---
CACHE_ROOT = "/Volumes/Apple/DerűsProjekt/ScraperOmni/shared_image_cache"
INDEX_PATH = os.path.join(CACHE_ROOT, "index.json")
FILES_DIR = os.path.join(CACHE_ROOT, "files")
_LOCK_PATH = os.path.join(CACHE_ROOT, ".lock")

# Treat images living under this shared curated dir as already-stable: put() can
# record their original path directly (no copy needed). We still copy into the
# cache for stability/portability — documented choice below.
_LOCAL_STABLE_DIR = "/Volumes/Apple/DerűsProjekt/Hírképek"


def normalize_link(url):
    """Normalize a sourceLink for use as a cache key.

    Mirrors history_manager.normalize_url: strip scheme + leading www + query +
    fragment + trailing slash, lowercase. So http vs https, a trailing slash, or
    a ?query all collapse to the same key. Returns '' on falsy/invalid input.
    """
    try:
        url = (url or "").strip()
        if not url:
            return ""
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                url = url[len(prefix):]
                break
        if url.startswith("www."):
            url = url[4:]
        if "?" in url:
            url = url.split("?")[0]
        if "#" in url:
            url = url.split("#")[0]
        url = url.rstrip("/")
        return url.lower()
    except Exception:
        try:
            return (url or "").strip().lower()
        except Exception:
            return ""


def _ensure_dirs():
    """Create the cache root + files dir lazily. Best-effort."""
    os.makedirs(FILES_DIR, exist_ok=True)


class _SharedLock:
    """Context manager: flock() an exclusive lock over the shared lock file.

    Degrades to a no-op if fcntl is unavailable or the lock file can't be
    opened, so a locking failure never breaks the caller.
    """

    def __init__(self):
        self._fh = None

    def __enter__(self):
        try:
            _ensure_dirs()
            self._fh = open(_LOCK_PATH, "a+")
            if _HAVE_FCNTL:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._fh = None
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh is not None:
                if _HAVE_FCNTL:
                    try:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self._fh.close()
        except Exception:
            pass
        self._fh = None
        return False  # never suppress exceptions from the with-body


def _load_index():
    """Read index.json (must be called under the shared lock). {} on any error."""
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index_atomic(index):
    """Atomically write index.json (called under the shared lock)."""
    try:
        _ensure_dirs()
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        os.replace(tmp, INDEX_PATH)
        return True
    except Exception:
        return False


def get(source_link):
    """Return the cached local image file path for this article, or None.

    Returns None if the link is unknown or the recorded file no longer exists.
    Fully guarded — never raises.
    """
    try:
        key = normalize_link(source_link)
        if not key:
            return None
        with _SharedLock():
            index = _load_index()
            entry = index.get(key)
        if not entry:
            return None
        path = entry.get("path") if isinstance(entry, dict) else None
        if path and os.path.exists(path):
            return path
        return None
    except Exception:
        return None


def put(source_link, image_path, origin=None):
    """Record the resolved image for this article so other languages reuse it.

    Copies image_path into the shared files dir named sha1(normalized_link)+ext
    and records {path, ts, origin} under the normalized key in index.json
    (atomic write, under the shared lock). Returns the cached path, or None on
    any failure / invalid input.

    NOTE on curated local images (under /Volumes/Apple/DerűsProjekt/Hírképek):
    those are already stable shared files, so we COULD record their original
    path directly with no copy. We still copy into the cache here for stability
    and portability (a single canonical location, independent of the curated
    dir), which keeps get()'s existence check simple and robust. The 'origin'
    field records where the picture came from ('local' vs 'download'/other).
    """
    try:
        key = normalize_link(source_link)
        if not key or not image_path or not os.path.exists(image_path):
            return None

        if origin is None:
            try:
                origin = "local" if os.path.abspath(image_path).startswith(
                    os.path.abspath(_LOCAL_STABLE_DIR)) else "download"
            except Exception:
                origin = "unknown"

        ext = os.path.splitext(image_path)[1] or ".jpg"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        cached_name = digest + ext

        with _SharedLock():
            _ensure_dirs()
            cached_path = os.path.join(FILES_DIR, cached_name)
            # Copy the picture into the shared cache (idempotent; overwrite ok).
            try:
                # Skip copy only if it's literally already the cached file.
                if os.path.abspath(image_path) != os.path.abspath(cached_path):
                    shutil.copy2(image_path, cached_path)
            except Exception:
                # If the copy fails, fall back to recording the original path so
                # other workers can still find the identical file.
                cached_path = image_path

            index = _load_index()
            index[key] = {
                "path": cached_path,
                "ts": int(time.time()),
                "origin": origin,
            }
            _save_index_atomic(index)
        return cached_path
    except Exception:
        return None
