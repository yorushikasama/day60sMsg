"""已推送条目去重：data/seen.json 滚动保留 7 天。

v2 结构：{"records": {id: 日期}, "titles": {归一化标题: {"date": 日期, "raw": 标题}}}
- records 用于按条目 id 去重（同 URL/同标题）
- titles 用于跨天主题去重（同一事件隔天换源换标题的情况）与续报提示
兼容 v1（扁平的 {id: 日期}）。
"""
import json
import logging
import os
from datetime import datetime, timedelta

from sources import CST

log = logging.getLogger("day60s")

RETAIN_DAYS = 7


def _norm_title(title):
    import re
    return re.sub(r"[^\w]", "", str(title).lower())


class SeenStore:
    def __init__(self, path):
        self.path = path
        self.records = {}  # id -> "YYYY-MM-DD HH:MM:SS"
        self.titles = {}   # norm_title -> {"date": iso, "raw": 标题}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except json.JSONDecodeError:
            # 损坏时留档重建，避免静默清空导致整池旧文重发
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                os.rename(self.path, f"{self.path}.corrupt-{stamp}")
                log.warning("seen.json 损坏，已留档为 %s.corrupt-%s 并重新开始", self.path, stamp)
            except OSError:
                log.warning("seen.json 损坏且无法留档，已重新开始")
            return
        if "records" in data:  # v2
            self.records = data.get("records") or {}
            self.titles = data.get("titles") or {}
        else:  # v1 扁平结构
            self.records = data or {}

    def filter_new(self, items):
        fresh = [it for it in items if it["id"] not in self.records]
        dropped = len(items) - len(fresh)
        if dropped:
            log.info("去重跳过 %d 条近期已推送内容", dropped)
        return fresh

    def filter_by_title(self, items, days=2):
        """跨天主题去重：近 N 天推送过相同主题（归一化标题一致）的条目剔除。"""
        cutoff = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
        fresh = []
        dropped = 0
        for it in items:
            rec = self.titles.get(_norm_title(it["title"]))
            if rec and rec.get("date", "") >= cutoff:
                dropped += 1
                continue
            fresh.append(it)
        if dropped:
            log.info("跨天主题去重跳过 %d 条（近 %d 天已推送同主题）", dropped, days)
        return fresh

    def recent_titles(self, days=2, limit=15):
        """近 N 天推送过的主题标题（供 LLM 做续报判断）。"""
        cutoff = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
        out = [(v.get("date", ""), v.get("raw", ""))
               for v in self.titles.values() if v.get("date", "") >= cutoff]
        return [raw for _, raw in sorted(out, reverse=True)[:limit]]

    def mark_seen(self, items):
        now = datetime.now(CST)
        stamp = now.isoformat(timespec="seconds")
        day = now.strftime("%Y-%m-%d")
        for it in items:
            self.records[it["id"]] = stamp
            for raw in {str(it.get("orig_title") or it["title"]), str(it["title"])}:
                self.titles[_norm_title(raw)] = {"date": day, "raw": raw}
        self._purge()
        self._save()

    def _purge(self):
        cutoff = (datetime.now() - timedelta(days=RETAIN_DAYS)).isoformat(timespec="seconds")
        self.records = {k: v for k, v in self.records.items() if v >= cutoff}
        day_cutoff = (datetime.now(CST) - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%d")
        self.titles = {k: v for k, v in self.titles.items() if v.get("date", "") >= day_cutoff}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"records": self.records, "titles": self.titles}, f,
                      ensure_ascii=False, indent=0)
