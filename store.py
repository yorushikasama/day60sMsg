"""已推送条目去重：data/seen.json 滚动保留 7 天。"""
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger("day60s")

RETAIN_DAYS = 7


class SeenStore:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.records = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = {}

    def filter_new(self, items):
        fresh = [it for it in items if it["id"] not in self.records]
        dropped = len(items) - len(fresh)
        if dropped:
            log.info("去重跳过 %d 条近期已推送内容", dropped)
        return fresh

    def mark_seen(self, items):
        now = datetime.now().isoformat(timespec="seconds")
        for it in items:
            self.records[it["id"]] = now
        self._purge()
        self._save()

    def _purge(self):
        cutoff = (datetime.now() - timedelta(days=RETAIN_DAYS)).isoformat(timespec="seconds")
        self.records = {k: v for k, v in self.records.items() if v >= cutoff}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=0)
