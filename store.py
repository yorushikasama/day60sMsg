"""已推送条目去重：data/seen.json 滚动保留 7 天。"""
import json
import logging
import os
from datetime import datetime, timedelta

log = logging.getLogger("day60s")

RETAIN_DAYS = 7


class SeenStore:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.records = json.load(f)
        except FileNotFoundError:
            self.records = {}
        except json.JSONDecodeError:
            # 损坏时留档重建，避免静默清空导致整池旧文重发
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                os.rename(self.path, f"{self.path}.corrupt-{stamp}")
                log.warning("seen.json 损坏，已留档为 %s.corrupt-%s 并重新开始", path, stamp)
            except OSError:
                log.warning("seen.json 损坏且无法留档，已重新开始")
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
