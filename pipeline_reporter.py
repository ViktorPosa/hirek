#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_reporter.py — Derűshírek Pipeline Monitor
Parses pipeline logs for a given date and generates:
  - Output/YYYY-MM-DD/report_data.json
  - Output/report_latest.html
  - macOS notification/dialog for CRITICAL issues
"""

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULER_LOG = os.path.join(BASE_DIR, "logs", "scheduler.log")
PIPELINE_LOG = os.path.join(BASE_DIR, "logs", "pipeline.log")
OUTPUT_DIR = os.path.join(BASE_DIR, "Output")

# ---------------------------------------------------------------------------
# Human-readable error descriptions (Hungarian)
# ---------------------------------------------------------------------------
HUMAN_ERRORS = {
    "rss_http_404": "Az RSS feed nem található (404). A forrás valószínűleg megszűnt vagy megváltozott az URL.",
    "rss_http_403": "Az RSS feed megtagadta a hozzáférést (403). IP-tiltás vagy kötelező belépés.",
    "rss_http_503": "Az RSS szerver átmenetileg nem elérhető (503). Általában magától helyreáll a következő futásra.",
    "rss_http_500": "Az RSS szerver belső hibát jelzett (500). Átmeneti szerverhiba, nem igényel beavatkozást.",
    "rss_timeout": "Az RSS lekérés időtúllépéssel zárult. A szerver lassú vagy elérhetetlenné vált.",
    "rss_dns": "DNS-feloldási hiba – a domain nem létezik vagy nem elérhető. A forrás valószínűleg megszűnt.",
    "rss_ssl": "SSL tanúsítvány-hiba. A szerver tanúsítványa érvénytelen – biztonságos kapcsolat nem létesíthető.",
    "api_timeout": "A Gemini API lassan válaszolt, de a retry sikerrel járt. Fokozott API-terhelés esetén normális.",
    "api_no_response": "A Gemini API nem válaszolt (3 kísérlet után sem). A batch átkerült újrafeldolgozásra.",
    "pairing_failure": "Az AI a cikk forrás-linkjét nem találta meg a feldolgozási batch-ben. A cikk megmaradt tartalommal, de a kép hiányozhat.",
    "image_download": "Képletöltés sikertelen. A forrásszerver nem válaszolt az időlimiten belül.",
    "process_killed": "A scheduler leállította a futó pipeline-folyamatot (SIGTERM), mert az előző futás még aktív volt. Előfordulhat, hogy néhány lépés (toplist, importance split) elmaradt.",
    "watchdog_timeout": "A pipeline meghaladta a maximális futási időt, a watchdog leállította. Ellenőrizd, megvannak-e az i4/i5/toplist fájlok!",
    "exit_code_1": "A pipeline hibával zárt (exit code 1). Egy alscript összeomlott – nézd meg a részletes logot.",
    "no_articles": "KRITIKUS: Aznap egyetlen cikk sem készült el! Az app üres hírfolyamot mutat a felhasználóknak.",
    "missing_data_files": "Hiányzó output fájlok: {files}. Az importance-szűrt és toplist szekciók elavult tartalmat mutatnak.",
}

# Pipeline steps expected in a full run
EXPECTED_STEPS = [
    "weather_generator.py",
    "market_generator.py",
    "rss_creator.py",
    "news_filter.py",
    "link_dedup.py",
    "summarizer_json.py",
    "image_downloader.py",
    "section_validator.py",
    "filter_importance.py",
    "randomize_sections.py",
]

# ---------------------------------------------------------------------------
# macOS notifications
# ---------------------------------------------------------------------------

def notify_mac(title, short_message, detail_message, level):
    """Send macOS notification (short) + modal dialog for CRITICAL/WARNING (detailed)."""
    emoji = {"CRITICAL": "🔴", "WARNING": "🟡", "OK": "🟢"}.get(level, "🔵")
    full_title = f"{emoji} {title}"
    # Escape quotes for AppleScript
    safe_short = short_message.replace('"', "'")
    safe_detail = detail_message.replace('"', "'").replace("\\", "\\\\")
    notif = f'display notification "{safe_short}" with title "{full_title}" subtitle "Derűshírek Pipeline"'
    try:
        subprocess.run(["osascript", "-e", notif], timeout=5, capture_output=True)
    except Exception:
        pass
    if level in ("CRITICAL", "WARNING"):
        icon = "stop" if level == "CRITICAL" else "caution"
        dlg = f'display dialog "{safe_detail}" with title "{full_title}" buttons {{"OK"}} default button "OK" with icon {icon}'
        try:
            subprocess.run(["osascript", "-e", dlg], timeout=120, capture_output=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Log parsing: scheduler.log
# ---------------------------------------------------------------------------

def parse_scheduler_log(target_date: str):
    """
    Parse scheduler.log for lines that start with [target_date
    Returns a list of run dicts and error counts.
    """
    date_prefix = f"[{target_date}"
    runs = []
    process_killed_count = 0
    watchdog_kills = 0

    # We also track STEP lines and completed steps within target-date runs.
    # These do NOT have date prefixes in scheduler.log — they appear inline between
    # bracketed lines. We track "in_run" state by finding the start/end brackets.
    current_run = None
    completed_steps = set()
    # We need to scan the whole file but only act on date-relevant lines.
    # Strategy: line-by-line, if bracketed line has target date → process it.
    # Non-bracketed lines between two target-date bracketed lines → still part of that run.

    in_target_section = False

    try:
        with open(SCHEDULER_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")

                # Is this a bracketed timestamp line?
                if line.startswith("[20"):
                    # Check if it matches our date
                    if line.startswith(date_prefix):
                        in_target_section = True
                    else:
                        # Different date — we might be leaving the target section
                        # (but we stay True if we already entered, to catch inline output)
                        # Actually set False only if we've moved to a *later* date
                        # to avoid false-positives from earlier dates bleeding in.
                        if line > f"[{target_date} 23:59:99]":
                            in_target_section = False

                # Process line if we're in the target section
                if not in_target_section:
                    # Still check non-bracketed lines if a current_run is open
                    if current_run is None:
                        continue
                    # We're between bracket lines within an open run — process for steps
                    _process_run_line(line, current_run, completed_steps)
                    continue

                # -- Bracketed target-date lines --
                if "🚀 Starting script: run_pipeline.py" in line:
                    ts = _extract_timestamp(line)
                    current_run = {
                        "scheduled_at": _guess_schedule_slot(ts),
                        "started_at": ts,
                        "ended_at": None,
                        "duration_seconds": 0,
                        "exit_code": None,
                        "status": "PENDING",
                        "process_killed": False,
                        "watchdog_triggered": False,
                    }
                    runs.append(current_run)

                elif "⚠️ Previous process" in line and "still running" in line:
                    process_killed_count += 1
                    if current_run is not None:
                        current_run["process_killed"] = True

                elif "run_pipeline.py finished with code" in line:
                    code_match = re.search(r"finished with code\s+(-?\d+)", line)
                    if code_match and current_run is not None:
                        code = int(code_match.group(1))
                        current_run["exit_code"] = code
                        current_run["ended_at"] = _extract_timestamp(line)
                        _calc_duration(current_run)
                        current_run["status"] = "OK" if code == 0 else ("CRITICAL" if code == 1 else "WARNING")
                        current_run = None

                elif "🚨 WATCHDOG:" in line:
                    watchdog_kills += 1
                    if current_run is not None:
                        current_run["watchdog_triggered"] = True

                else:
                    # Non-bracketed inline content (step completions, etc.)
                    if current_run is not None:
                        _process_run_line(line, current_run, completed_steps)

    except FileNotFoundError:
        pass

    return runs, process_killed_count, watchdog_kills, list(completed_steps)


def _process_run_line(line, current_run, completed_steps):
    """Parse a non-bracketed scheduler.log line for step completion info."""
    if line.startswith(">>> ") and "completed successfully" in line:
        m = re.match(r">>> (\S+\.py) completed successfully", line)
        if m:
            completed_steps.add(m.group(1))
    elif line.startswith("STEP:"):
        pass  # step started, not needed for tracking


def _extract_timestamp(line):
    """Extract ISO8601 timestamp from a bracketed log line like [2026-08-06 06:09:36]"""
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
    if m:
        return m.group(1).replace(" ", "T")
    return None


def _guess_schedule_slot(iso_ts):
    """Map a start timestamp to its nearest scheduled slot (06:00, 12:00, 18:00, 00:00)."""
    if iso_ts is None:
        return "unknown"
    try:
        t = datetime.fromisoformat(iso_ts)
        hour = t.hour
        if hour < 3:
            return "00:00"
        elif hour < 9:
            return "06:00"
        elif hour < 15:
            return "12:00"
        else:
            return "18:00"
    except Exception:
        return "unknown"


def _calc_duration(run):
    """Fill duration_seconds from started_at / ended_at."""
    try:
        if run.get("started_at") and run.get("ended_at"):
            s = datetime.fromisoformat(run["started_at"])
            e = datetime.fromisoformat(run["ended_at"])
            run["duration_seconds"] = int((e - s).total_seconds())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Log parsing: pipeline_errors.log
# ---------------------------------------------------------------------------

def parse_pipeline_errors(target_date: str):
    """Parse Output/YYYY-MM-DD/pipeline_errors.log"""
    errors_path = os.path.join(OUTPUT_DIR, target_date, "pipeline_errors.log")
    counts = {
        "rss_404": 0, "rss_403": 0, "rss_503": 0, "rss_500": 0,
        "rss_timeout": 0, "rss_dns": 0, "rss_ssl": 0,
        "api_timeout": 0, "api_no_response": 0,
        "items_missing_from_batch": 0,
        "pairing_failures": 0, "pairing_fixed": 0,
        "image_failures": 0,
        "process_killed": 0,
        "watchdog_kills": 0,
    }
    failed_feeds = []  # list of {url, error_type, error_short}

    try:
        with open(errors_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()

                # RSS fetch errors
                if line.startswith("Error fetching "):
                    url_match = re.match(r"Error fetching (https?://\S+): (.+)", line)
                    if url_match:
                        url = url_match.group(1)
                        err = url_match.group(2)
                        etype, eshort = _classify_rss_error(err)
                        counts[etype] += 1
                        failed_feeds.append({"url": url, "error_type": etype, "error_short": eshort})

                # API timeout
                elif "Summarizer: TIMEOUT" in line:
                    counts["api_timeout"] += 1

                # API no response
                elif "Summarizer: [Batch] No response from API" in line:
                    counts["api_no_response"] += 1

                # Missing items from batch
                elif "Summarizer: [Batch]" in line and "item(s) missing" in line:
                    m = re.search(r"(\d+) item\(s\) missing", line)
                    if m:
                        counts["items_missing_from_batch"] += int(m.group(1))

                # Image download failures
                elif "Image_Downloader: Download failed" in line:
                    counts["image_failures"] += 1

                # Pairing failures
                elif "⚠️ PAIRING FAIL:" in line:
                    counts["pairing_failures"] += 1

                # Pairing fixed
                elif "✅ Fixed" in line and "pairing failure" in line:
                    m = re.search(r"Fixed (\d+)/\d+", line)
                    if m:
                        counts["pairing_fixed"] = max(counts["pairing_fixed"], int(m.group(1)))

    except FileNotFoundError:
        pass

    return counts, failed_feeds


def _classify_rss_error(err_str):
    """Classify an RSS error string into (error_type_key, short_description)."""
    err_lower = err_str.lower()
    if "http error 404" in err_lower:
        return "rss_404", "HTTP 404"
    elif "http error 403" in err_lower:
        return "rss_403", "HTTP 403"
    elif "http error 503" in err_lower:
        return "rss_503", "HTTP 503"
    elif "http error 500" in err_lower:
        return "rss_500", "HTTP 500"
    elif "curl: (28)" in err_lower or "timed out" in err_lower or "operation timed out" in err_lower:
        return "rss_timeout", "Timeout"
    elif "curl: (6)" in err_lower or "could not resolve" in err_lower:
        return "rss_dns", "DNS Error"
    elif "curl: (35)" in err_lower or "tls" in err_lower or "ssl" in err_lower:
        return "rss_ssl", "SSL/TLS Error"
    else:
        return "rss_timeout", err_str[:60]


# ---------------------------------------------------------------------------
# RSS scraper log
# ---------------------------------------------------------------------------

def parse_rss_scraper_log(target_date: str):
    """Parse Output/YYYY-MM-DD/rss_scraper_log.json"""
    path = os.path.join(OUTPUT_DIR, target_date, "rss_scraper_log.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        total = len(entries)
        ok = sum(1 for e in entries if e.get("success"))
        failed = total - ok
        rate = round(ok / total * 100, 1) if total else 0.0
        return {"total_feeds": total, "ok_feeds": ok, "failed_feeds": failed, "success_rate_pct": rate}
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {"total_feeds": 0, "ok_feeds": 0, "failed_feeds": 0, "success_rate_pct": 0.0}


# ---------------------------------------------------------------------------
# Output file inspection
# ---------------------------------------------------------------------------

def inspect_output_files(target_date: str):
    """Check existence and item counts of output JSON files."""
    day_dir = os.path.join(OUTPUT_DIR, target_date)
    files = {}

    def _load(name):
        path = os.path.join(day_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data) if isinstance(data, list) else None
            return {"exists": True, "item_count": count}
        except FileNotFoundError:
            return {"exists": False, "item_count": None}
        except (json.JSONDecodeError, Exception):
            return {"exists": True, "item_count": None}

    files["data_json"] = _load("data.json")
    files["data_i4_json"] = _load("data_i4.json")
    files["data_i5_json"] = _load("data_i5.json")
    files["data_toplist_json"] = _load("data_toplist.json")

    # idojaras/piacok — just existence
    for key, fname in [("idojaras_json", "idojaras.json"), ("piacok_json", "piacok.json")]:
        path = os.path.join(day_dir, fname)
        files[key] = {"exists": os.path.exists(path)}

    return files


# ---------------------------------------------------------------------------
# Article analysis
# ---------------------------------------------------------------------------

def analyze_articles(target_date: str):
    """Analyze data.json (+ data_i4.json + data_i5.json) for article stats.
    filter_importance.py splits i4/i5 out of data.json, so we must combine all three."""
    day_dir = os.path.join(OUTPUT_DIR, target_date)
    result = {
        "total": 0,
        "with_image": 0,
        "without_image": 0,
        "by_importance": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        "by_section": {},
    }
    # Combine articles from all three files to get a full picture
    all_articles = []
    for fname in ["data.json", "data_i4.json", "data_i5.json"]:
        fpath = os.path.join(day_dir, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                chunk = json.load(f)
            if isinstance(chunk, list):
                all_articles.extend(chunk)
        except Exception:
            pass
    result["total"] = len(all_articles)
    for a in all_articles:
        img = a.get("image")
        if img:
            result["with_image"] += 1
        else:
            result["without_image"] += 1

        imp = str(a.get("importance", ""))
        if imp in result["by_importance"]:
            result["by_importance"][imp] += 1

        section = a.get("section", "ismeretlen")
        if isinstance(section, list):
            section = section[0] if section else "ismeretlen"
        if not isinstance(section, str):
            section = str(section)
        result["by_section"][section] = result["by_section"].get(section, 0) + 1

    result["by_section"] = dict(
        sorted(result["by_section"].items(), key=lambda x: x[1], reverse=True)
    )
    return result


# ---------------------------------------------------------------------------
# 7-day history
# ---------------------------------------------------------------------------

def build_history_7d(target_date: str):
    """Build a 7-day trend list ending at target_date."""
    target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    history = []
    for i in range(6, -1, -1):
        d = target_dt - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        day_dir = os.path.join(OUTPUT_DIR, ds)
        data_path = os.path.join(day_dir, "data.json")
        articles = 0
        status = "OK"
        exit_code = 0
        if os.path.exists(data_path):
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    articles = len(data)
            except Exception:
                pass
        else:
            status = "MISSING"
            exit_code = 1
        if articles == 0 and os.path.exists(day_dir):
            status = "CRITICAL"
            exit_code = 1
        elif articles < 100 and articles > 0:
            status = "WARNING"
        history.append({"date": ds, "articles": articles, "status": status, "exit_code": exit_code})
    return history


# ---------------------------------------------------------------------------
# Criticality assessment
# ---------------------------------------------------------------------------

def assess_criticality(files, articles, errors, runs, process_killed, watchdog_kills):
    level = "OK"
    reasons = []

    article_count = articles["total"]
    data_missing = not files["data_json"]["exists"]
    i4_missing = not files["data_i4_json"]["exists"]
    i5_missing = not files["data_i5_json"]["exists"]
    toplist_missing = not files["data_toplist_json"]["exists"]

    overall_exit = max((r.get("exit_code") or 0) for r in runs) if runs else None
    had_watchdog = watchdog_kills > 0 or any(r.get("watchdog_triggered") for r in runs)
    had_process_kill = process_killed > 0 or any(r.get("process_killed") for r in runs)

    # CRITICAL checks
    if data_missing or article_count == 0:
        level = "CRITICAL"
        reasons.append("no_articles" if article_count == 0 else "data_json_missing")

    if overall_exit == 1 and not had_watchdog:
        level = "CRITICAL"
        reasons.append("exit_code_1")

    if had_process_kill and (i4_missing or i5_missing or toplist_missing):
        level = "CRITICAL"
        missing_names = [n for n, m in [("data_i4.json", i4_missing), ("data_i5.json", i5_missing), ("data_toplist.json", toplist_missing)] if m]
        reasons.append(f"process_killed+missing_files:{','.join(missing_names)}")

    if level == "CRITICAL":
        return level, reasons

    # WARNING checks
    if i4_missing or i5_missing or toplist_missing:
        level = "WARNING"
        missing_names = [n for n, m in [("data_i4.json", i4_missing), ("data_i5.json", i5_missing), ("data_toplist.json", toplist_missing)] if m]
        reasons.append(f"missing_files:{','.join(missing_names)}")

    if article_count < 100 and article_count > 0:
        if level != "CRITICAL":
            level = "WARNING"
        reasons.append(f"low_article_count:{article_count}")

    if errors["rss_404"] >= 3:
        if level not in ("CRITICAL",):
            level = "WARNING"
        reasons.append(f"rss_404_count:{errors['rss_404']}")

    image_fail = errors["image_failures"]
    if article_count > 0 and image_fail > article_count * 0.1:
        if level not in ("CRITICAL",):
            level = "WARNING"
        reasons.append(f"image_fail_rate:{round(image_fail/article_count*100,1)}%")

    if level == "WARNING":
        return level, reasons

    # INFO checks
    if errors["api_timeout"] > 0:
        level = "INFO"
        reasons.append(f"api_timeout:{errors['api_timeout']}")

    if errors["pairing_failures"] > 0:
        level = "INFO"
        reasons.append(f"pairing_failures:{errors['pairing_failures']}")

    if image_fail > 0:
        level = "INFO"
        reasons.append(f"image_failures:{image_fail}")

    if had_process_kill and not (i4_missing or i5_missing or toplist_missing):
        level = "INFO"
        reasons.append("process_killed_but_files_ok")

    return level, reasons


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def build_report(target_date: str):
    # Parse scheduler.log
    runs, process_killed, watchdog_kills, completed_steps = parse_scheduler_log(target_date)

    # Parse pipeline_errors.log
    errors, failed_feeds = parse_pipeline_errors(target_date)
    errors["process_killed"] = process_killed
    errors["watchdog_kills"] = watchdog_kills

    # Output files
    files = inspect_output_files(target_date)

    # Article analysis
    articles = analyze_articles(target_date)

    # RSS scraper stats
    rss_stats = parse_rss_scraper_log(target_date)
    # Augment with failed_feed_list (deduplicated by url)
    seen_urls = set()
    deduped_feeds = []
    for f in failed_feeds:
        if f["url"] not in seen_urls:
            seen_urls.add(f["url"])
            deduped_feeds.append(f)
    rss_stats["failed_feed_list"] = deduped_feeds

    # Image stats
    image_attempted = articles["total"]
    image_failed = errors["image_failures"]
    image_rate = round(image_failed / image_attempted * 100, 1) if image_attempted else 0.0
    images = {
        "attempted": image_attempted,
        "failed": image_failed,
        "failure_rate_pct": image_rate,
    }

    # Overall exit code
    overall_exit = None
    for r in runs:
        ec = r.get("exit_code")
        if ec is not None:
            overall_exit = ec if overall_exit is None else max(overall_exit, abs(ec))

    # Missing steps
    missing_steps = [s for s in EXPECTED_STEPS if s not in completed_steps]

    # Pipeline section
    pipeline = {
        "runs": runs,
        "overall_exit_code": overall_exit,
        "completed_steps": sorted(completed_steps),
        "missing_steps": missing_steps,
    }

    # Criticality
    criticality, crit_reasons = assess_criticality(
        files, articles, errors, runs, process_killed, watchdog_kills
    )

    # 7-day history
    history_7d = build_history_7d(target_date)

    report = {
        "schema_version": "1.1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": target_date,
        "criticality": criticality,
        "criticality_reasons": crit_reasons,
        "pipeline": pipeline,
        "articles": articles,
        "images": images,
        "rss": rss_stats,
        "errors": errors,
        "files": files,
        "history_7d": history_7d,
    }
    return report


# ---------------------------------------------------------------------------
# HTML report generator
# ---------------------------------------------------------------------------

CRITICALITY_COLORS = {
    "CRITICAL": {"bg": "#fee2e2", "text": "#991b1b", "border": "#f87171", "badge": "#dc2626"},
    "WARNING":  {"bg": "#fef9c3", "text": "#854d0e", "border": "#facc15", "badge": "#ca8a04"},
    "INFO":     {"bg": "#eff6ff", "text": "#1e40af", "border": "#93c5fd", "badge": "#3b82f6"},
    "OK":       {"bg": "#f0fdf4", "text": "#166534", "border": "#86efac", "badge": "#22c55e"},
}

STATUS_EMOJI = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵", "OK": "🟢"}
STATUS_HU = {"CRITICAL": "KRITIKUS", "WARNING": "FIGYELMEZTETÉS", "INFO": "INFO", "OK": "RENDBEN"}


def _format_duration(seconds):
    if seconds is None or seconds == 0:
        return "–"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}ó {m}p"
    elif m:
        return f"{m}p {s}s"
    return f"{s}s"


def _ts_display(iso_ts):
    if not iso_ts:
        return "–"
    try:
        return datetime.fromisoformat(iso_ts).strftime("%H:%M:%S")
    except Exception:
        return iso_ts


def generate_html(report):
    r = report
    crit = r["criticality"]
    colors = CRITICALITY_COLORS.get(crit, CRITICALITY_COLORS["OK"])
    art = r["articles"]
    err = r["errors"]
    rss = r["rss"]
    files = r["files"]
    pipe = r["pipeline"]
    hist = r["history_7d"]
    imgs = r["images"]

    # ----- Alert banners -----
    alert_html = ""
    if crit in ("CRITICAL", "WARNING", "INFO"):
        alert_items = ""
        for reason in r["criticality_reasons"]:
            alert_items += f'<li>{_reason_to_human(reason)}</li>\n'
        alert_html = f"""
<div class="alert alert-{crit.lower()}" style="background:{colors['bg']};border-left:5px solid {colors['border']};color:{colors['text']};padding:1rem 1.25rem;border-radius:8px;margin-bottom:1rem;">
  <strong>{STATUS_EMOJI[crit]} {STATUS_HU[crit]}</strong>
  <ul style="margin:0.5rem 0 0 1.2rem;padding:0;">{alert_items}</ul>
</div>"""

    # ----- Runs table rows -----
    runs_rows = ""
    for run in pipe["runs"]:
        st = run.get("status", "PENDING")
        rc = CRITICALITY_COLORS.get(st, CRITICALITY_COLORS["INFO"])
        killed = "⚠️ Megölve" if run.get("process_killed") else ""
        watchdog = "🚨 Watchdog" if run.get("watchdog_triggered") else ""
        exit_code = run.get("exit_code")
        exit_display = str(exit_code) if exit_code is not None else "–"
        runs_rows += f"""<tr>
          <td>{run.get('scheduled_at','–')}</td>
          <td>{_ts_display(run.get('started_at'))}</td>
          <td>{_ts_display(run.get('ended_at'))}</td>
          <td>{_format_duration(run.get('duration_seconds',0))}</td>
          <td>{exit_display}</td>
          <td>{killed} {watchdog}</td>
          <td><span class="badge" style="background:{rc['badge']};color:#fff;">{st}</span></td>
        </tr>"""

    if not runs_rows:
        runs_rows = '<tr><td colspan="7" style="text-align:center;color:#6b7280;">Nem találtam futást a naplóban.</td></tr>'

    # ----- Files checklist -----
    file_checks = [
        ("data.json", files["data_json"]),
        ("data_i4.json", files["data_i4_json"]),
        ("data_i5.json", files["data_i5_json"]),
        ("data_toplist.json", files["data_toplist_json"]),
        ("idojaras.json", files["idojaras_json"]),
        ("piacok.json", files["piacok_json"]),
    ]
    files_html = ""
    for name, info in file_checks:
        exists = info.get("exists", False)
        icon = "✅" if exists else "❌"
        count_str = ""
        if "item_count" in info and info["item_count"] is not None:
            count_str = f' <span style="color:#6b7280;font-size:0.85em;">({info["item_count"]} db)</span>'
        files_html += f'<li>{icon} <code>{name}</code>{count_str}</li>\n'

    # ----- Failed feeds table -----
    feed_rows = ""
    for f in rss.get("failed_feed_list", [])[:50]:
        etype = f.get("error_type", "")
        desc = HUMAN_ERRORS.get(etype, f.get("error_short", ""))
        feed_rows += f"""<tr>
          <td style="font-size:0.8em;word-break:break-all;">{f.get('url','')}</td>
          <td><span class="badge" style="background:#ef4444;color:#fff;">{f.get('error_short','')}</span></td>
          <td style="font-size:0.85em;">{desc}</td>
        </tr>"""

    if not feed_rows:
        feed_rows = '<tr><td colspan="3" style="text-align:center;color:#6b7280;">Nincsenek sikertelen RSS feedek.</td></tr>'

    # ----- Chart data -----
    imp_labels = ["1", "2", "3", "4", "5"]
    imp_data = [art["by_importance"].get(k, 0) for k in imp_labels]
    imp_colors = ["#22c55e", "#84cc16", "#eab308", "#f97316", "#ef4444"]

    section_items = list(art["by_section"].items())[:15]
    sec_labels = [s[0] for s in section_items]
    sec_data = [s[1] for s in section_items]
    sec_colors = [
        "#3b82f6","#8b5cf6","#ec4899","#14b8a6","#f97316",
        "#22c55e","#eab308","#ef4444","#06b6d4","#a78bfa",
        "#f43f5e","#10b981","#6366f1","#0ea5e9","#84cc16",
    ]

    hist_labels = [h["date"] for h in hist]
    hist_data = [h["articles"] for h in hist]
    hist_colors_map = {"OK": "#22c55e", "WARNING": "#eab308", "CRITICAL": "#ef4444", "MISSING": "#9ca3af"}
    hist_point_colors = [hist_colors_map.get(h["status"], "#3b82f6") for h in hist]

    # Error bar chart
    error_keys = [
        ("rss_404", "RSS 404", "#f87171", "rss_http_404"),
        ("rss_403", "RSS 403", "#fb923c", "rss_http_403"),
        ("rss_503", "RSS 503", "#fbbf24", "rss_http_503"),
        ("rss_timeout", "RSS Timeout", "#a78bfa", "rss_timeout"),
        ("rss_dns", "RSS DNS", "#818cf8", "rss_dns"),
        ("rss_ssl", "RSS SSL", "#22d3ee", "rss_ssl"),
        ("api_timeout", "API Timeout", "#f472b6", "api_timeout"),
        ("api_no_response", "API No Resp.", "#c084fc", "api_no_response"),
        ("items_missing_from_batch", "Batch Missing", "#94a3b8", "pairing_failure"),
        ("pairing_failures", "Pairing Fail", "#fb7185", "pairing_failure"),
        ("image_failures", "Image Fail", "#38bdf8", "image_download"),
    ]
    err_labels = [e[1] for e in error_keys]
    err_data = [err.get(e[0], 0) for e in error_keys]
    err_colors_list = [e[2] for e in error_keys]

    # Build error description table rows (only for errors that actually occurred)
    error_desc_rows = ""
    for key, label, color, human_key in error_keys:
        count = err.get(key, 0)
        if count == 0:
            continue
        desc = HUMAN_ERRORS.get(human_key, "")
        error_desc_rows += f"""
      <tr>
        <td style="white-space:nowrap;">
          <span style="display:inline-block;width:12px;height:12px;background:{color};border-radius:3px;margin-right:6px;vertical-align:middle;"></span>
          <strong>{label}</strong>
        </td>
        <td style="color:#374151;font-weight:700;text-align:center;padding-right:1.2rem;">{count}</td>
        <td style="color:#4b5563;">{desc}</td>
      </tr>"""

    # RSS doughnut
    rss_labels = ["Sikeres", "Sikertelen"]
    rss_data_chart = [rss["ok_feeds"], rss["failed_feeds"]]

    # Completed steps display
    steps_html = ""
    for step in EXPECTED_STEPS:
        done = step in pipe["completed_steps"]
        icon = "✅" if done else "⬜"
        steps_html += f'<span style="margin-right:0.5rem;">{icon} {step}</span>'

    # Missing steps warning
    missing_steps_html = ""
    if pipe["missing_steps"]:
        missing_steps_html = '<p style="color:#dc2626;margin-top:0.5rem;">Hiányzó lépések: ' + ", ".join(pipe["missing_steps"]) + "</p>"

    # Key metrics
    overall_ec = pipe.get("overall_exit_code")
    pipeline_status_text = STATUS_HU.get(crit, crit)
    pipeline_badge_color = colors["badge"]

    rss_ok_pct = rss["success_rate_pct"]
    image_ok_pct = round(100 - imgs["failure_rate_pct"], 1) if imgs["attempted"] else 100.0

    json_embed = json.dumps(report, ensure_ascii=False, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Derűshírek Pipeline Monitor — {r['date']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #f9fafb;
    --card: #ffffff;
    --header: #1e293b;
    --text: #1e293b;
    --muted: #6b7280;
    --radius: 10px;
    --shadow: 0 1px 4px rgba(0,0,0,0.08);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); font-size: 15px; }}
  a {{ color: inherit; }}
  .header {{ background: var(--header); color: #fff; padding: 1.5rem 2rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.75rem; }}
  .header h1 {{ font-size: 1.4rem; font-weight: 700; letter-spacing: -0.02em; }}
  .header .meta {{ font-size: 0.85rem; color: #94a3b8; }}
  .status-badge {{ padding: 0.35rem 0.9rem; border-radius: 999px; font-weight: 700; font-size: 0.9rem; letter-spacing: 0.03em; color: #fff; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; }}
  .section-title {{ font-size: 1.05rem; font-weight: 700; color: var(--header); margin-bottom: 0.75rem; margin-top: 2rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.4rem; }}
  .card {{ background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.25rem 1.5rem; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
  .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 1rem; }}
  .metric-card {{ background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.25rem 1.5rem; text-align: center; border-top: 4px solid #e5e7eb; }}
  .metric-card .metric-value {{ font-size: 2.2rem; font-weight: 800; line-height: 1; margin-bottom: 0.3rem; }}
  .metric-card .metric-label {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ background: #f1f5f9; text-align: left; padding: 0.6rem 0.75rem; font-weight: 600; color: var(--muted); text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.04em; }}
  td {{ padding: 0.6rem 0.75rem; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  .chart-wrap {{ position: relative; height: 280px; }}
  .chart-wrap-lg {{ position: relative; height: 320px; }}
  .steps-wrap {{ font-size: 0.85rem; line-height: 2; flex-wrap: wrap; display: flex; gap: 0.4rem; }}
  ul.files-list {{ list-style: none; padding: 0; line-height: 2.2; font-size: 0.95rem; }}
  .footer {{ text-align: center; color: var(--muted); font-size: 0.8rem; margin-top: 3rem; padding: 1rem; }}
  @media (max-width: 640px) {{
    .header {{ padding: 1rem; }}
    .container {{ padding: 1rem; }}
    .grid-2 {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<script>const REPORT = {json_embed};</script>

<!-- HEADER -->
<div class="header">
  <div>
    <h1>📰 Derűshírek Pipeline Monitor</h1>
    <div class="meta">Dátum: <strong>{r['date']}</strong> &nbsp;·&nbsp; Generálva: {r['generated_at']}</div>
  </div>
  <div>
    <span class="status-badge" style="background:{pipeline_badge_color};">
      {STATUS_EMOJI[crit]} {pipeline_status_text}
    </span>
  </div>
</div>

<div class="container">

  <!-- ALERTS -->
  {alert_html}

  <!-- KEY METRICS -->
  <div class="section-title">Fő mutatók</div>
  <div class="grid-4">
    <div class="metric-card" style="border-top-color:{pipeline_badge_color};">
      <div class="metric-value" style="color:{pipeline_badge_color};">{art['total']}</div>
      <div class="metric-label">Cikk összesen</div>
    </div>
    <div class="metric-card" style="border-top-color:{'#22c55e' if rss_ok_pct>=90 else '#eab308' if rss_ok_pct>=70 else '#ef4444'};">
      <div class="metric-value" style="color:{'#22c55e' if rss_ok_pct>=90 else '#eab308' if rss_ok_pct>=70 else '#ef4444'};">{rss_ok_pct}%</div>
      <div class="metric-label">RSS sikerráta</div>
    </div>
    <div class="metric-card" style="border-top-color:{'#22c55e' if image_ok_pct>=95 else '#eab308' if image_ok_pct>=80 else '#ef4444'};">
      <div class="metric-value" style="color:{'#22c55e' if image_ok_pct>=95 else '#eab308' if image_ok_pct>=80 else '#ef4444'};">{image_ok_pct}%</div>
      <div class="metric-label">Képek rendben</div>
    </div>
    <div class="metric-card" style="border-top-color:{pipeline_badge_color};">
      <div class="metric-value" style="color:{pipeline_badge_color};">{STATUS_EMOJI[crit]}</div>
      <div class="metric-label">Pipeline státusz</div>
    </div>
  </div>

  <!-- PIPELINE RUNS -->
  <div class="section-title">Pipeline futások</div>
  <div class="card">
    <div style="overflow-x:auto;">
      <table>
        <thead><tr>
          <th>Ütemezés</th><th>Indítás</th><th>Befejezés</th><th>Időtartam</th><th>Exit code</th><th>Megjegyzés</th><th>Státusz</th>
        </tr></thead>
        <tbody>{runs_rows}</tbody>
      </table>
    </div>
    <div style="margin-top:1rem;">
      <div style="font-size:0.85rem;font-weight:600;color:var(--muted);margin-bottom:0.4rem;">Befejezett lépések:</div>
      <div class="steps-wrap">{steps_html}</div>
      {missing_steps_html}
    </div>
  </div>

  <!-- ARTICLE ANALYSIS -->
  <div class="section-title">Cikkelemzés</div>
  <div class="grid-2">
    <div class="card">
      <div style="font-weight:600;margin-bottom:0.75rem;">Fontossági szint eloszlása (1–5)</div>
      <div class="chart-wrap"><canvas id="chartImportance"></canvas></div>
    </div>
    <div class="card">
      <div style="font-weight:600;margin-bottom:0.75rem;">Szekciónkénti cikkszám</div>
      <div class="chart-wrap"><canvas id="chartSection"></canvas></div>
    </div>
  </div>

  <!-- 7-DAY TREND -->
  <div class="section-title">Heti cikkszám trend (7 nap)</div>
  <div class="card">
    <div class="chart-wrap-lg"><canvas id="chartTrend"></canvas></div>
  </div>

  <!-- RSS -->
  <div class="section-title">RSS Feedek</div>
  <div class="grid-2">
    <div class="card">
      <div style="font-weight:600;margin-bottom:0.75rem;">Feed sikeresség</div>
      <div style="max-width:260px;margin:0 auto;"><canvas id="chartRss"></canvas></div>
      <div style="text-align:center;margin-top:0.75rem;font-size:0.9rem;">
        Összes: <strong>{rss['total_feeds']}</strong> &nbsp;·&nbsp;
        Sikeres: <strong style="color:#22c55e;">{rss['ok_feeds']}</strong> &nbsp;·&nbsp;
        Sikertelen: <strong style="color:#ef4444;">{rss['failed_feeds']}</strong>
      </div>
    </div>
    <div class="card" style="overflow-x:auto;">
      <div style="font-weight:600;margin-bottom:0.75rem;">Sikertelen feedek (top 50)</div>
      <table>
        <thead><tr><th>URL</th><th>Hiba</th><th>Leírás</th></tr></thead>
        <tbody>{feed_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ERROR SUMMARY -->
  <div class="section-title">Hibák összefoglalója</div>
  <div class="card">
    <div class="chart-wrap"><canvas id="chartErrors"></canvas></div>
    {f'''<table style="margin-top:1.5rem;border-top:1px solid #e5e7eb;padding-top:1rem;width:100%;">
      <thead>
        <tr>
          <th style="width:150px;">Hibatípus</th>
          <th style="width:60px;text-align:center;">Db</th>
          <th>Mit jelent / mi okozza</th>
        </tr>
      </thead>
      <tbody>{error_desc_rows}</tbody>
    </table>''' if error_desc_rows else ''}
  </div>

  <!-- OUTPUT FILES -->
  <div class="section-title">Output fájlok</div>
  <div class="card">
    <ul class="files-list">{files_html}</ul>
  </div>

  <!-- FOOTER -->
  <div class="footer">
    Derűshírek Pipeline Monitor &nbsp;·&nbsp; schema_version {r['schema_version']} &nbsp;·&nbsp; {r['generated_at']}
  </div>

</div>

<script>
// --- Chart.js defaults ---
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
Chart.defaults.color = '#374151';

// Importance bar chart
new Chart(document.getElementById('chartImportance'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(imp_labels)},
    datasets: [{{
      label: 'Cikkszám',
      data: {json.dumps(imp_data)},
      backgroundColor: {json.dumps(imp_colors)},
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}
    }}
  }}
}});

// Section horizontal bar chart
new Chart(document.getElementById('chartSection'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(sec_labels)},
    datasets: [{{
      label: 'Cikkszám',
      data: {json.dumps(sec_data)},
      backgroundColor: {json.dumps(sec_colors[:len(sec_labels)])},
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}
    }}
  }}
}});

// 7-day trend line chart
new Chart(document.getElementById('chartTrend'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(hist_labels)},
    datasets: [{{
      label: 'Cikkszám',
      data: {json.dumps(hist_data)},
      borderColor: '#3b82f6',
      backgroundColor: 'rgba(59,130,246,0.08)',
      pointBackgroundColor: {json.dumps(hist_point_colors)},
      pointRadius: 6,
      tension: 0.3,
      fill: true,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}
    }}
  }}
}});

// RSS doughnut
new Chart(document.getElementById('chartRss'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(rss_labels)},
    datasets: [{{
      data: {json.dumps(rss_data_chart)},
      backgroundColor: ['#22c55e', '#ef4444'],
      borderWidth: 2,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'bottom' }}
    }}
  }}
}});

// Errors horizontal bar chart
new Chart(document.getElementById('chartErrors'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(err_labels)},
    datasets: [{{
      label: 'Darabszám',
      data: {json.dumps(err_data)},
      backgroundColor: {json.dumps(err_colors_list)},
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


def _reason_to_human(reason):
    """Convert a criticality reason code to a human-readable string."""
    if reason == "no_articles":
        return HUMAN_ERRORS["no_articles"]
    if reason == "data_json_missing":
        return "A data.json fájl hiányzik! Nem keletkeztek cikkek."
    if reason == "exit_code_1":
        return HUMAN_ERRORS["exit_code_1"]
    if reason.startswith("process_killed+missing_files:"):
        files = reason.split(":", 1)[1]
        return HUMAN_ERRORS["missing_data_files"].format(files=files) + " (pipeline megölés miatt)"
    if reason.startswith("missing_files:"):
        files = reason.split(":", 1)[1]
        return HUMAN_ERRORS["missing_data_files"].format(files=files)
    if reason.startswith("low_article_count:"):
        n = reason.split(":", 1)[1]
        return f"Kevés cikk: csak {n} db készült el (minimum elvárás: 100)."
    if reason.startswith("rss_404_count:"):
        n = reason.split(":", 1)[1]
        return f"{HUMAN_ERRORS['rss_http_404']} ({n} feed érintett)"
    if reason.startswith("image_fail_rate:"):
        pct = reason.split(":", 1)[1]
        return f"Magas képletöltési hibaarány: {pct}. {HUMAN_ERRORS['image_download']}"
    if reason.startswith("api_timeout:"):
        n = reason.split(":", 1)[1]
        return f"API timeout: {n} alkalommal. {HUMAN_ERRORS['api_timeout']}"
    if reason.startswith("pairing_failures:"):
        n = reason.split(":", 1)[1]
        return f"Párosítási hiba: {n} esetben. {HUMAN_ERRORS['pairing_failure']}"
    if reason.startswith("image_failures:"):
        n = reason.split(":", 1)[1]
        return f"Képletöltési hiba: {n} esetben. {HUMAN_ERRORS['image_download']}"
    if reason == "process_killed_but_files_ok":
        return "A pipeline folyamatot leállították, de az összes output fájl megvan. " + HUMAN_ERRORS["process_killed"]
    return reason


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Derűshírek Pipeline Reporter")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-popup", action="store_true", help="Suppress macOS popup dialogs")
    args = parser.parse_args()
    if args.no_popup:
        global notify_mac
        notify_mac = lambda title, msg, level: None

    if args.date:
        target_date = args.date
    else:
        target_date = date.today().strftime("%Y-%m-%d")

    print(f"[pipeline_reporter] Analysing date: {target_date}")

    # Build report
    report = build_report(target_date)

    # Write report_data.json
    day_dir = os.path.join(OUTPUT_DIR, target_date)
    os.makedirs(day_dir, exist_ok=True)
    json_path = os.path.join(day_dir, "report_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[pipeline_reporter] JSON report → {json_path}")

    # Write report_latest.html
    html_path = os.path.join(OUTPUT_DIR, "report_latest.html")
    html = generate_html(report)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[pipeline_reporter] HTML report → {html_path}")

    # Notification
    crit = report["criticality"]
    article_count = report["articles"]["total"]
    pipe = report["pipeline"]

    # Build detailed popup message
    lines = []

    # 1. What happened (human reasons)
    if report["criticality_reasons"]:
        for reason in report["criticality_reasons"][:3]:
            lines.append(_reason_to_human(reason))
    else:
        lines.append("Minden rendben.")

    # 2. Exit code + process killed / watchdog info from the most recent run
    if pipe["runs"]:
        last_run = pipe["runs"][-1]
        exit_code = last_run.get("exit_code")
        duration = last_run.get("duration_seconds", 0)
        mins = duration // 60
        secs = duration % 60
        run_info = f"Futás: {mins}p {secs}s"
        if exit_code is not None:
            run_info += f", exit code {exit_code}"
        if last_run.get("process_killed"):
            run_info += ", SIGTERM (megölve)"
        if last_run.get("watchdog_triggered"):
            run_info += ", watchdog leállította"
        lines.append(run_info)

    # 3. Missing steps (where it stopped)
    if pipe["missing_steps"]:
        lines.append("Hiányzó lépések: " + ", ".join(pipe["missing_steps"]))

    # 4. Completed steps summary
    done = pipe["completed_steps"]
    if done:
        lines.append("Kész: " + ", ".join(done))

    # 5. Article count
    lines.append(f"Cikkek: {article_count} | {target_date}")

    # Short notification message (first reason + article count)
    short_msg = f"{article_count} cikk | " + (_reason_to_human(report["criticality_reasons"][0]) if report["criticality_reasons"] else "OK")
    short_msg = short_msg[:200]  # notification subtitle limit

    # Full dialog message (multi-line, capped at ~500 chars for osascript)
    full_msg = "\n".join(lines)
    if len(full_msg) > 480:
        full_msg = full_msg[:477] + "..."

    notify_mac("Pipeline Report", short_msg, full_msg, crit)

    print(f"[pipeline_reporter] Status: {crit} | Articles: {article_count}")


if __name__ == "__main__":
    main()
