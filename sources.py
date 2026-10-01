"""多源抓取：RSS/Atom（含 RSSHub）与 60s 聚合接口，统一归一化为 dict。"""
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import requests

log = logging.getLogger("day60s")

CST = timezone(timedelta(hours=8))

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _clean(text, limit=220):
    """去掉 HTML 标签并压缩空白；超长时在词边界截断并加省略号。"""
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit - 40:  # 英文在最近的空格断词；中文没有空格则直接截断
        cut = cut[:space]
    return cut.rstrip(" ，。,.") + "…"


def _item_id(url, title):
    raw = (url or title).encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


def _to_cst(struct_time):
    if not struct_time:
        return None
    dt = datetime(*struct_time[:6], tzinfo=timezone.utc)
    return dt.astimezone(CST)


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _http_get(url, timeout):
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    resp.raise_for_status()
    return resp.content


def fetch_rss(source, cfg):
    """抓取任意 RSS/Atom 源，返回归一化条目列表。"""
    out = []
    content = _http_get(source["url"], cfg["fetch"]["timeout"])
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS 解析失败: {getattr(feed, 'bozo_exception', '')}")
    for e in feed.entries:
        title = _clean(e.get("title", ""), 160)
        if not title:
            continue
        out.append({
            "id": _item_id(e.get("link", ""), title),
            "title": title,
            "url": e.get("link", ""),
            "summary": _clean(e.get("summary") or e.get("description", "")),
            "source": source["name"],
            "section": source["section"],
            "weight": int(source.get("weight", 1)),
            "published": _to_cst(e.get("published_parsed") or e.get("updated_parsed")),
        })
    return out


def fetch_60s(source, cfg):
    """抓取 vikiboss/60s 的「每天60秒读懂世界」聚合接口。"""
    resp = requests.get(source["url"], timeout=cfg["fetch"]["timeout"])
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"60s 接口返回异常: {payload.get('message')}")
    data = payload.get("data") or {}
    link = data.get("link", "")
    out = []
    for text in data.get("news") or []:
        text = _clean(text, 200)
        if not text:
            continue
        out.append({
            "id": _item_id("", text),
            "title": text,
            "url": "",          # 60s 为浓缩一句话新闻，无单独原文链接
            "summary": "",
            "source": source["name"],
            "section": source["section"],
            "weight": int(source.get("weight", 1)),
            "published": None,
        })
    # 每日寄语作为来源级备注暂存（主流程可在邮件脚注引用）
    if out and data.get("tip"):
        out[0]["_tip"] = _clean(data["tip"], 120)
    return out


FETCHERS = {"rss": fetch_rss, "60s": fetch_60s}


def fetch_all(cfg):
    """并发抓取全部启用源；单源失败仅告警，不影响整体。"""
    enabled = [s for s in cfg["sources"] if s.get("enabled", True)]
    items, failures = [], []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(enabled)))) as pool:
        futures = {pool.submit(_fetch_one, s, cfg): s["name"] for s in enabled}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                got = fut.result()
                log.info("源 [%s] 抓取 %d 条", name, len(got))
                items.extend(got)
            except Exception as exc:
                failures.append(name)
                log.warning("源 [%s] 失败: %s", name, exc)
    if failures:
        log.warning("失败源: %s", ", ".join(failures))
    return items


def _fetch_one(source, cfg):
    fetcher = FETCHERS[source["type"]]
    last_err = None
    for _ in range(1 + int(cfg["fetch"].get("retries", 1))):
        try:
            return fetcher(source, cfg)
        except Exception as exc:
            last_err = exc
    raise last_err


def filter_fresh(items, cfg):
    """按时间窗过滤（无发布时间的条目默认保留），并限制每源候选数。"""
    window = int(cfg["fetch"].get("window_hours", 24))
    max_per = int(cfg["fetch"].get("max_per_source", 15))
    deadline = datetime.now(CST) - timedelta(hours=window)

    by_source, kept = {}, []
    for it in items:
        if it["published"] and it["published"] < deadline:
            continue
        by_source.setdefault(it["source"], []).append(it)

    for source, group in by_source.items():
        dated = [i for i in group if i["published"]]
        undated = [i for i in group if not i["published"]]
        dated.sort(key=lambda i: i["published"], reverse=True)
        kept.extend((dated + undated)[:max_per])
    return kept
