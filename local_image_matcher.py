"""
LEÍRÁS / DESCRIPTION:
Local image matcher for the news pipeline.

Given a news item's ordered `tags` list, selects a matching LOCAL image from
IMAGE_DIR by comparing the item's tags against per-language keyword lists stored
in each image's sibling `.json` file (`gemini_keywords_<lang>`).

Fully import-safe and side-effect-free: no file I/O happens at import time; the
image index is built lazily on first use and cached per language.

BEMENET / INPUT:
- A news item's `tags` list (strings, in the scraper's language).
- Local images in IMAGE_DIR: `<name>.png` + `<name>.json` (with keyword lists).

KIMENET / OUTPUT:
- A local .png path (or None) plus a details dict describing the best match.
"""

import os
import re
import json
import glob
import threading

# --- CONFIGURATION (easy to tune) ---
IMAGE_DIR = "/Volumes/Apple/DerűsProjekt/Hírképek"
MIN_KEYWORD_MATCHES = 5
RATIO_THRESHOLD = 0.0  # start at 0 so nothing is filtered by ratio yet; tune later


# --- NORMALIZATION ---
# casefold + trim surrounding whitespace/punctuation + collapse internal whitespace.
# Language accents are PRESERVED (no umlaut / HU accent stripping).
_PUNCT_STRIP = " \t\r\n\"'`.,;:!?()[]{}<>«»„”“…-–—/\\|@#*_~"


def normalize(text):
    """Normalize a tag/keyword for comparison. casefold + trim + collapse whitespace."""
    if text is None:
        return ""
    s = str(text).casefold()
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(_PUNCT_STRIP)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _harmonic(n):
    """H_n = sum_{i=0}^{n-1} 1/(i+1)."""
    return sum(1.0 / (i + 1) for i in range(n))


class LocalImageMatcher:
    """
    Matches news items to local images by tag/keyword overlap for a single language.

    The index is loaded lazily (on first score/best_match call) and cached; construction
    performs no I/O so the class is safe to instantiate at import time.
    """

    def __init__(self, image_dir=IMAGE_DIR, lang="hu",
                 min_matches=MIN_KEYWORD_MATCHES, ratio_threshold=RATIO_THRESHOLD):
        self.image_dir = image_dir
        self.lang = lang
        self.min_matches = min_matches
        self.ratio_threshold = ratio_threshold
        self._index = None  # list of {png_path, keywords, n, H_n}
        self._lock = threading.Lock()

    # --- index loading ---
    def _load_index(self):
        """Scan image_dir for *.json; build (png_path, keywords) entries for this lang."""
        index = []
        key = "gemini_keywords_" + self.lang
        if not os.path.isdir(self.image_dir):
            return index
        for json_path in sorted(glob.glob(os.path.join(self.image_dir, "*.json"))):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            keywords = meta.get(key)
            # If the language list is missing/empty, SKIP (no cross-language fallback).
            if not keywords or not isinstance(keywords, list):
                continue
            png_path = os.path.splitext(json_path)[0] + ".png"
            if not os.path.exists(png_path):
                continue
            n = len(keywords)
            index.append({
                "png_path": png_path,
                "keywords": keywords,
                "n": n,
                "H_n": _harmonic(n),
            })
        return index

    def _get_index(self):
        if self._index is None:
            with self._lock:
                if self._index is None:
                    self._index = self._load_index()
        return self._index

    # --- scoring ---
    def score(self, news_tags, entry):
        """
        Score one (news_tags, image entry) pair.

        Returns dict: {match_count, weighted_score, ratio, matched_keywords, n, png_path}.
        """
        S = {normalize(t) for t in (news_tags or []) if normalize(t)}
        keywords = entry["keywords"]
        n = entry["n"]
        H_n = entry["H_n"]

        match_count = 0
        weighted_score = 0.0
        matched_keywords = []
        for i, kw in enumerate(keywords):
            if normalize(kw) in S:
                match_count += 1
                weighted_score += 1.0 / (i + 1)
                matched_keywords.append(kw)

        ratio = (weighted_score / H_n) if H_n > 0 else 0.0
        return {
            "match_count": match_count,
            "weighted_score": weighted_score,
            "ratio": ratio,
            "matched_keywords": matched_keywords,
            "n": n,
            "png_path": entry["png_path"],
        }

    def best_match(self, news_tags):
        """
        Score all indexed images; keep only those passing the gates
        (match_count >= min_matches AND ratio >= ratio_threshold); return the
        best-scoring (png_path, details).

        Best = highest weighted_score, tie-break higher match_count, then
        alphabetical png name. When nothing passes, returns
        (None, best_details_even_if_gated_out) so callers can log near-misses.
        """
        index = self._get_index()

        scored = [self.score(news_tags, entry) for entry in index]
        if not scored:
            return None, None

        # "Best" = highest weighted_score, then higher match_count, then alphabetically
        # earliest png name. Sort ascending on a composite key: weighted_score desc,
        # match_count desc, then name asc.
        scored.sort(key=lambda d: (-d["weighted_score"], -d["match_count"],
                                   os.path.basename(d["png_path"]).casefold()))

        best_overall = scored[0]  # best regardless of gates (for near-miss logging)

        for details in scored:
            if (details["match_count"] >= self.min_matches
                    and details["ratio"] >= self.ratio_threshold):
                return details["png_path"], details

        return None, best_overall


def find_local_image(news_tags, lang, image_dir=IMAGE_DIR,
                     min_matches=MIN_KEYWORD_MATCHES, ratio_threshold=RATIO_THRESHOLD):
    """
    Convenience wrapper: build/reuse a matcher for `lang` and return its best match.

    Returns (png_path_or_None, details_dict).
    """
    matcher = _get_shared_matcher(image_dir, lang, min_matches, ratio_threshold)
    return matcher.best_match(news_tags)


# Cache shared matchers per (image_dir, lang, min_matches, ratio_threshold) so repeated
# convenience calls reuse the built index.
_SHARED_MATCHERS = {}
_SHARED_LOCK = threading.Lock()


def _get_shared_matcher(image_dir, lang, min_matches, ratio_threshold):
    key = (image_dir, lang, min_matches, ratio_threshold)
    matcher = _SHARED_MATCHERS.get(key)
    if matcher is None:
        with _SHARED_LOCK:
            matcher = _SHARED_MATCHERS.get(key)
            if matcher is None:
                matcher = LocalImageMatcher(
                    image_dir=image_dir, lang=lang,
                    min_matches=min_matches, ratio_threshold=ratio_threshold,
                )
                _SHARED_MATCHERS[key] = matcher
    return matcher


if __name__ == "__main__":
    # Read-only smoke test: report how many images are indexed per language and
    # demo scoring against a sample tag set. Never writes anything.
    print(f"IMAGE_DIR = {IMAGE_DIR}")
    print(f"MIN_KEYWORD_MATCHES = {MIN_KEYWORD_MATCHES}  RATIO_THRESHOLD = {RATIO_THRESHOLD}")
    for lang in ("hu", "en", "de"):
        m = LocalImageMatcher(lang=lang)
        idx = m._get_index()
        print(f"  [{lang}] indexed images: {len(idx)}")

    # Demo scoring (HU) using tags that should overlap the Apple image.
    demo_tags = ["apple", "iphone", "okostelefon", "technológia", "mobil",
                 "design", "kijelző", "iOS"]
    png, details = find_local_image(demo_tags, lang="hu")
    print("\nDemo HU match for tags:", demo_tags)
    if png:
        print(f"  -> {os.path.basename(png)}")
    else:
        print("  -> (no match passed gates)")
    if details:
        print(f"     match_count={details['match_count']} "
              f"weighted_score={details['weighted_score']:.3f} "
              f"ratio={details['ratio']:.3f} matched={details['matched_keywords']}")
