"""article_date.py — best-effort article publication-date extraction + max-age parsing.

Cél: egy forráshoz (input.txt / rss_creator.txt sorban) megadott `--Nd/w/m` max-kor
alapján el tudjuk dönteni, hogy egy cikk túl régi-e. Két belépési pont:

  * parse_max_age("--1w")        -> datetime.timedelta | None   (input-flag → küszöb)
  * parse_rss_datetime(struct)   -> aware UTC datetime | None    (Tier A: RSS published_parsed)
  * extract_publish_date(html,url) -> aware UTC datetime | None  (Tier B: HTML meta/JSON-LD/…)
  * is_older_than(dt, max_age)   -> bool                          (kényelmi összevetés)

Csak stdlib. Minden visszaadott datetime timezone-aware, UTC-re normalizálva.
A kor-küszöb nap/hét/hónap granularitású, ezért az időzóna pár órás bizonytalansága
nem befolyásolja a döntést; TZ nélküli időbélyeget UTC-nek tekintünk.
"""

import re
import json
import calendar
from datetime import datetime, timedelta, timezone

# Minden 2005 előtti dátum gyanús (tipikusan 1970 epoch-0 szemét vagy hibás parse).
_MIN_VALID_YEAR = 2005

_HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12,
}


# --------------------------------------------------------------------------- #
# max-age flag → timedelta
# --------------------------------------------------------------------------- #
def parse_max_age(token):
    """'--1d' / '--2w' / '--1m' → timedelta. Hónap = 30 nap. Nincs egyezés → None."""
    if not token or not isinstance(token, str):
        return None
    m = re.fullmatch(r"--(\d+)\s*([dwmDWM])", token.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if n <= 0:
        return None
    if unit == "d":
        return timedelta(days=n)
    if unit == "w":
        return timedelta(weeks=n)
    if unit == "m":
        return timedelta(days=30 * n)
    return None


def split_source_line(line):
    """Egy input-sorból leválasztja az URL-t és az opcionális max-age flaget.
    Visszaad: (url, max_age|None). A flag bárhol lehet az URL után (szóközzel)."""
    parts = line.split()
    if not parts:
        return line.strip(), None
    url = parts[0]
    max_age = None
    for tok in parts[1:]:
        ma = parse_max_age(tok)
        if ma is not None:
            max_age = ma
            break
    return url, max_age


# --------------------------------------------------------------------------- #
# datetime helpers
# --------------------------------------------------------------------------- #
def _to_utc(dt):
    """Aware→UTC; naive→UTC-nek tekintjük. Érvénytelen év → None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt.year < _MIN_VALID_YEAR:
        return None
    return dt


def _parse_iso(s):
    """ISO 8601 (Z, offset, fractional, date-only) → aware UTC datetime | None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # 'Z' → '+00:00' (a 3.11 előtti fromisoformat nem eszi a Z-t; biztonságból cseréljük)
    s2 = re.sub(r"[zZ]$", "+00:00", s)
    try:
        return _to_utc(datetime.fromisoformat(s2))
    except ValueError:
        pass
    # date-only 'YYYY-MM-DD'
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return _to_utc(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc))
        except ValueError:
            return None
    return None


def parse_rss_datetime(struct_time):
    """feedparser published_parsed/updated_parsed (time.struct_time, UTC) → aware UTC dt."""
    if not struct_time:
        return None
    try:
        return _to_utc(datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc))
    except (ValueError, OverflowError, TypeError):
        return None


def is_older_than(dt, max_age, now=None):
    """True, ha dt régebbi, mint (now - max_age). dt vagy max_age None → False (nem skippel)."""
    if dt is None or max_age is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - dt) > max_age


# --------------------------------------------------------------------------- #
# HTML publish-date extraction (Tier B fallback-lánc)
# --------------------------------------------------------------------------- #
def _attr(tag, name):
    m = re.search(name + r'\s*=\s*["\']([^"\']*)["\']', tag, re.I)
    return m.group(1) if m else None


def _meta_dates(html):
    """Visszaad egy dict-et: {kulcs(lower) -> content} a <meta> tagekből."""
    out = {}
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        key = _attr(tag, "property") or _attr(tag, "name") or _attr(tag, "itemprop")
        if not key:
            continue
        content = _attr(tag, "content")
        if content:
            out.setdefault(key.lower(), content)
    return out


def _walk_jsonld_datepublished(obj):
    """Rekurzívan keres 'datePublished'-et JSON-LD struktúrában."""
    if isinstance(obj, dict):
        if "datePublished" in obj and isinstance(obj["datePublished"], str):
            return obj["datePublished"]
        for v in obj.values():
            found = _walk_jsonld_datepublished(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_jsonld_datepublished(v)
            if found:
                return found
    return None


def _hungarian_visible_date(html):
    """'2026. augusztus 19.' → dt. Csak a dokumentum elején keres (fejléc), a törzsbeli
    véletlen dátumok (pl. '2017. március 29-én') elkerülése végett."""
    head = html[:4000]
    pat = r"(20\d{2})\.\s*(" + "|".join(_HU_MONTHS.keys()) + r")\s*(\d{1,2})"
    m = re.search(pat, head, re.I)
    if not m:
        return None
    month = _HU_MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return _to_utc(datetime(int(m.group(1)), month, int(m.group(3)), tzinfo=timezone.utc))
    except ValueError:
        return None


def extract_publish_date(html, url=""):
    """Cikk-HTML-ből kinyeri a publikálás dátumát a prioritizált fallback-lánccal.
    Aware UTC datetime | None. Nem dob kivételt."""
    try:
        if not html or not isinstance(html, str):
            html = ""

        metas = _meta_dates(html) if html else {}

        # 1) article:published_time (property VAGY name) / og:published_time
        for k in ("article:published_time", "og:published_time"):
            dt = _parse_iso(metas.get(k))
            if dt:
                return dt

        # 2) JSON-LD datePublished
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.I | re.S,
        ):
            raw = m.group(1).strip()
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            dp = _walk_jsonld_datepublished(data)
            dt = _parse_iso(dp) if dp else None
            if dt:
                return dt

        # 3) egyéb meta kulcsok
        for k in ("datepublished", "date", "pubdate", "parsely-pub-date",
                  "publish-date", "sailthru.date", "dc.date.issued"):
            dt = _parse_iso(metas.get(k))
            if dt:
                return dt

        # 4) <time datetime="...">
        m = re.search(r'<time\b[^>]*\bdatetime=["\']([^"\']+)["\']', html, re.I)
        if m:
            dt = _parse_iso(m.group(1))
            if dt:
                return dt

        # 5) URL-beli /YYYY/MM/DD/
        m = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", url or "")
        if m:
            try:
                dt = _to_utc(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc))
                if dt:
                    return dt
            except ValueError:
                pass

        # 6) magyar látható dátum a fejlécben
        dt = _hungarian_visible_date(html)
        if dt:
            return dt

        # 7) koncert.hu-szerű: og:image fájlnévbe ágyazott Unix-epoch (low-confidence)
        og_image = metas.get("og:image", "")
        m = re.search(r"/uploads/[^\"']*?/(\d{10})-", og_image) or re.search(r"/(\d{10})-\w+\.(?:jpg|jpeg|png|webp)", og_image)
        if m:
            try:
                return _to_utc(datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc))
            except (ValueError, OverflowError):
                pass

        return None
    except Exception:
        return None
