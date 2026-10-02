"""每日存档与自产 Atom 订阅：data/archive/YYYY-MM-DD.{html,json}、data/feed.xml。"""
import json
import logging
import os
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from sources import CST

log = logging.getLogger("day60s")

SITE_BASE = ""  # 无对外域名，存档仅本机/SSH 查阅


def _archive_dir(cfg=None):
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "data", "archive")
    os.makedirs(path, exist_ok=True)
    return path


def _iso(dt):
    if isinstance(dt, datetime):
        return dt.isoformat(timespec="seconds")
    return str(dt) if dt else ""


def save_archive(now, sections, html_text):
    """保存当日策展结果（HTML + 结构化 JSON），并重建索引页。"""
    day = now.strftime("%Y-%m-%d")
    adir = _archive_dir()

    slim = [{"name": s["name"],
             "items": [{k: (_iso(v) if isinstance(v, datetime) else v)
                        for k, v in it.items() if k != "_tip"}
                       for it in s["items"]]}
            for s in sections]
    with open(os.path.join(adir, f"{day}.json"), "w", encoding="utf-8") as f:
        json.dump({"date": day, "sections": slim}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(adir, f"{day}.html"), "w", encoding="utf-8") as f:
        f.write(html_text)
    _write_index(adir)
    log.info("每日存档已写入 data/archive/%s.html", day)


def _write_index(adir):
    days = sorted(f[:-5] for f in os.listdir(adir)
                  if f.endswith(".html") and not f.startswith("index"))
    rows = "\n".join(
        f'<li><a href="{escape(d)}.html">{d}</a></li>' for d in reversed(days))
    html = ("<!DOCTYPE html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>day60sMsg 往期回顾</title>"
            "<body style='font-family:sans-serif;max-width:640px;margin:2em auto'>"
            f"<h2>day60sMsg 往期回顾（{len(days)} 天）</h2><ul>{rows}</ul></body></html>")
    with open(os.path.join(adir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def write_feed(now, sections):
    """把当日策展结果输出为 Atom 订阅文件（覆盖写）。"""
    day = now.strftime("%Y-%m-%d")
    entries = []
    for sec in sections:
        for it in sec["items"]:
            entries.append(
                "<entry>"
                f"<title>{escape(it['title'])}</title>"
                f"<id>urn:day60smsg:{day}:{escape(str(it['id']))}</id>"
                f"<updated>{now.isoformat(timespec='seconds')}</updated>"
                + (f"<link href=\"{escape(it['url'])}\"/>" if it.get("url") else "")
                + f"<summary>{escape(it.get('summary') or '')}</summary>"
                "</entry>"
            )
    xml = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom'>"
        "<title>day60sMsg 每日要闻速览</title>"
        f"<id>urn:day60smsg:{day}</id>"
        f"<updated>{now.isoformat(timespec='seconds')}</updated>"
        + "".join(entries) + "</feed>"
    )
    base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(base, "data"), exist_ok=True)
    with open(os.path.join(base, "data", "feed.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    log.info("Atom 订阅文件已更新 data/feed.xml（%d 条）", len(entries))


def load_recent_archives(days=7):
    """读取近 N 天的结构化存档（供周报聚合），按日期升序返回。"""
    adir = _archive_dir()
    out = []
    for offset in range(days):
        day = (datetime.now(CST) - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = os.path.join(adir, f"{day}.json")
        try:
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return list(reversed(out))  # 旧 → 新
