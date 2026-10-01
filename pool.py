"""候选池：分时段抓取的内容先落盘累积（data/pool.json），发送前统一策展。

- merge 按 id 去重，新条目记录首次入池时间
- 无发布时间的条目（如 60s 一句话新闻）以首次入池时间视为发布时间
- 超过 RETAIN_HOURS 的条目自动清理
"""
import json
import logging
import os
from datetime import datetime, timedelta

from sources import CST

log = logging.getLogger("day60s")

RETAIN_HOURS = 36  # 选择窗口 24h + 余量


class CandidatePool:
    def __init__(self, path):
        self.path = path
        self.entries = {}  # id -> {"first_seen": iso, "item": {...}}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for e in data:
                item = self._load(e["item"])
                self.entries[item["id"]] = {"first_seen": e["first_seen"], "item": item}
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def merge(self, items):
        """并入新条目，返回新增数量。重复条目保留最早的首次入池时间。"""
        now = datetime.now(CST).isoformat(timespec="seconds")
        cutoff = datetime.now(CST) - timedelta(hours=RETAIN_HOURS)
        added = 0
        for it in items:
            if it["id"] in self.entries:
                continue
            if it.get("published") and it["published"] < cutoff:
                continue  # 早于保留期的条目不入池，避免每次抓取反复新增/清理
            self.entries[it["id"]] = {"first_seen": now, "item": dict(it)}
            added += 1
        if added:
            self._prune()
            self._save()
        log.info("候选池新增 %d 条，现有 %d 条", added, len(self.entries))
        return added

    def items(self):
        out = []
        for e in self.entries.values():
            it = dict(e["item"])
            if not it.get("published"):
                it["published"] = datetime.fromisoformat(e["first_seen"])
            out.append(it)
        return out

    def clear(self):
        self.entries = {}
        self._save()

    def _prune(self):
        cutoff = datetime.now(CST) - timedelta(hours=RETAIN_HOURS)
        drop = [
            key for key, e in self.entries.items()
            if (e["item"].get("published") or datetime.fromisoformat(e["first_seen"])) < cutoff
        ]
        for key in drop:
            del self.entries[key]
        if drop:
            log.info("候选池清理过期条目 %d 条", len(drop))

    @staticmethod
    def _dump(item):
        it = dict(item)
        if it.get("published"):
            it["published"] = it["published"].isoformat()
        return it

    @staticmethod
    def _load(data):
        it = dict(data)
        if it.get("published"):
            it["published"] = datetime.fromisoformat(it["published"])
        return it

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = [
            {"first_seen": e["first_seen"], "item": self._dump(e["item"])}
            for e in self.entries.values()
        ]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
