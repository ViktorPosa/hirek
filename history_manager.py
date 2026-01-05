import os
import json
import datetime

HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'history.json')

class HistoryManager:
    def __init__(self, filename=HISTORY_FILE):
        self.filename = filename
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

    def save(self):
        """Saves history to JSON file."""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving history: {e}")

    def get_status(self, url):
        """Returns the status dict for a URL or None if not found."""
        return self.history.get(url)

    def is_known(self, url):
        """Checks if a URL is already in history (either positive or negative)."""
        return url in self.history

    def is_negative(self, url):
        """Checks if a URL was previously marked as negative."""
        record = self.get_status(url)
        return record and record.get('status') == 'NEGATIVE'

    def is_positive(self, url):
        """Checks if a URL was previously marked as positive/neutral."""
        record = self.get_status(url)
        return record and record.get('status') in ['POSITIVE', 'NEUTRAL']

    def is_summarized(self, url):
        """Checks if a URL has been marked as summarized."""
        record = self.get_status(url)
        return record and record.get('summarized', False)

    def update(self, url, status=None, summarized=None):
        """Updates the record for a URL.
        
        Args:
            url (str): The link URL.
            status (str, optional): 'POSITIVE', 'NEUTRAL', or 'NEGATIVE'.
            summarized (bool, optional): True if summarized.
        """
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
        """Marks a URL as filtered out with the reason.
        
        Args:
            url (str): The link URL.
            filter_source (str): Which filter removed it ('link_filter' or 'news_filter').
            reason (str): Explanation why it was filtered.
        """
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

    def get_stats(self):
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

