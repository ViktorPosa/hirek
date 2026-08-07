import os
import json
import datetime
import threading

"""
LEÍRÁS:
Előzménykezelő modul (HistoryManager).
Nyomon követi a feldolgozott URL-eket, azok státuszát (POSITIVE, NEGATIVE, FILTERED, ERROR)
és egyéb metaadatait, hogy elkerülje a duplikált feldolgozást.

BEMENET:
- history.json fájl
- URL-ek és státuszfrissítések a feldolgozó szkriptektől

KIMENET:
- Frissített history.json
- Státuszlekérdezések eredménye (pl. is_known, is_negative)
"""


HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'history.json')

class HistoryManager:
    def __init__(self, filename=HISTORY_FILE):
        self.filename = filename
        self._lock = threading.Lock()
        self.history = self.load()

    def load(self):
        """Loads history from JSON file."""
        if not os.path.exists(self.filename):
            return {}
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Corrupt history file {self.filename}. Starting fresh.")
            return {}
        except Exception as e:
            print(f"Error loading history: {e}")
            return {}

    def normalize_url(self, url):
        """Normalizes a URL for consistent history tracking.
        Strips scheme, www, trailing slash, query params, and fragments so that
        http://www.example.com/article/ and https://example.com/article both
        map to the same key and avoid duplicate processing.
        """
        try:
            # Strip markdown link format: [text](url) -> url
            if url.startswith('[') and '](' in url and url.endswith(')'):
                url = url.split('](')[1].rstrip(')')
            if url.startswith('[http') and ']' in url:
                url = url.split(']')[0].lstrip('[')

            # Strip scheme (http/https) and www prefix for canonical form
            url = url.strip()
            for prefix in ('https://', 'http://'):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            if url.startswith('www.'):
                url = url[4:]

            # Remove query parameters and fragments
            if '?' in url:
                url = url.split('?')[0]
            if '#' in url:
                url = url.split('#')[0]

            # Remove trailing slash
            url = url.rstrip('/')

            return url.lower()
        except Exception:
            return url

    def save(self):
        """Saves history to JSON file."""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving history: {e}")

    def get_status(self, url):
        """Returns the status dict for a URL or None if not found."""
        with self._lock:
            return self.history.get(url)

    def is_known(self, url):
        """Checks if a URL is already in history (either positive or negative)."""
        with self._lock:
            return self.normalize_url(url) in self.history

    def is_negative(self, url):
        """Checks if a URL was previously marked as negative."""
        with self._lock:
            record = self.history.get(self.normalize_url(url))
            return record and record.get('status') == 'NEGATIVE'

    def is_positive(self, url):
        """Checks if a URL was previously marked as positive/neutral."""
        with self._lock:
            record = self.history.get(self.normalize_url(url))
            return record and record.get('status') in ['POSITIVE', 'NEUTRAL']

    def is_summarized(self, url):
        """Checks if a URL has been marked as summarized."""
        with self._lock:
            record = self.history.get(self.normalize_url(url))
            return record and record.get('summarized', False)

    def is_filtered(self, url):
        """Checks if a URL was previously filtered out."""
        with self._lock:
            record = self.history.get(self.normalize_url(url))
            return record and record.get('status') == 'FILTERED'

    def update(self, url, status=None, summarized=None):
        """Updates the record for a URL."""
        url = self.normalize_url(url)
        with self._lock:
            if url not in self.history:
                self.history[url] = {
                    'first_seen': datetime.datetime.now().isoformat(),
                    'status': 'UNKNOWN',
                    'summarized': False
                }

            record = self.history[url]
            record['last_updated'] = datetime.datetime.now().isoformat()

            if status:
                record['status'] = status

            if summarized is not None:
                record['summarized'] = summarized

            self.save()

    def mark_filtered(self, url, filter_source, reason):
        """Marks a URL as filtered out with the reason."""
        url = self.normalize_url(url)
        with self._lock:
            if url not in self.history:
                self.history[url] = {
                    'first_seen': datetime.datetime.now().isoformat(),
                    'status': 'FILTERED',
                    'summarized': False
                }

            record = self.history[url]
            record['last_updated'] = datetime.datetime.now().isoformat()
            record['status'] = 'FILTERED'
            record['filtered_by'] = filter_source
            record['filter_reason'] = reason

            self.save()

    def mark_processing_error(self, url, reason):
        """Marks a URL as having a processing error after max retries."""
        url = self.normalize_url(url)
        with self._lock:
            if url not in self.history:
                self.history[url] = {
                    'first_seen': datetime.datetime.now().isoformat(),
                    'status': 'PROCESSING_ERROR',
                    'summarized': False,
                    'failure_count': 0
                }

            record = self.history[url]
            record['last_updated'] = datetime.datetime.now().isoformat()
            record['status'] = 'PROCESSING_ERROR'
            record['error_reason'] = reason
            record['failure_count'] = record.get('failure_count', 0) + 1
            record['summarized'] = False

            self.save()

    def get_failure_count(self, url):
        """Returns the number of times a URL has failed processing."""
        with self._lock:
            record = self.history.get(self.normalize_url(url))
            return record.get('failure_count', 0) if record else 0

    def get_stats(self):
        with self._lock:
            total = len(self.history)
            positive = len([r for r in self.history.values() if r.get('status') in ['POSITIVE', 'NEUTRAL']])
            negative = len([r for r in self.history.values() if r.get('status') == 'NEGATIVE'])
            filtered = len([r for r in self.history.values() if r.get('status') == 'FILTERED'])
            summarized = len([r for r in self.history.values() if r.get('summarized')])
        return {
            "total_links": total,
            "positive_neutral": positive,
            "negative": negative,
            "filtered": filtered,
            "summarized": summarized
        }

    def cleanup_old_entries(self, days=60):
        """Remove entries older than `days` days that are not summarized positives.
        Keeps all POSITIVE/summarized entries regardless of age (they prevent re-processing).
        Removes old NEGATIVE, FILTERED, PROCESSING_ERROR, and UNKNOWN entries.
        Returns count of removed entries.
        """
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        to_remove = []
        with self._lock:
            for url, record in self.history.items():
                status = record.get('status', 'UNKNOWN')
                summarized = record.get('summarized', False)
                if summarized or status in ('POSITIVE', 'NEUTRAL'):
                    continue
                last_updated = record.get('last_updated') or record.get('first_seen', '')
                if last_updated < cutoff_iso:
                    to_remove.append(url)
            for url in to_remove:
                del self.history[url]
            if to_remove:
                self.save()
                print(f"  [HistoryManager] Cleaned up {len(to_remove)} stale entries older than {days} days.")
        return len(to_remove)

