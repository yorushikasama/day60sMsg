"""执行编排：抓取、策展、日发送流水线、周报、状态一览。"""
import logging
import os
import time
from datetime import datetime, timedelta

import archive
import email_builder
import llm
import push
import sources
import state
from pool import CandidatePool
from sender import send_email
from sources import CST
from state import BASE_DIR, data_path
from store import SeenStore

log = logging.getLogger("day60s")


def _pool(cfg):
    """候选池保留期 = 抓取窗口 + 12h 余量，必须 >= 窗口，否则窗口配置被架空。"""
    retain = int((cfg.get("fetch") or {}).get("window_hours", 24)) + 12
    return CandidatePool(data_path("pool.json"), retain_hours=retain)


def _seen():
    return SeenStore(data_path("seen.json"))


def crawl(cfg):
    """分时段抓取：结果并入候选池，供发送时策展使用。"""
    items = sources.fetch_all(cfg)
    if not items:
        raise RuntimeError("本次抓取所有源均失败，未入池任何内容")
    _pool(cfg).merge(items)


def dry_run(cfg):
    items = sources.fetch_all(cfg)
    fresh = sources.filter_fresh(items, cfg)
    log.info("dry-run: 抓取 %d 条，时间窗过滤后 %d 条", len(items), len(fresh))
    for it in fresh[:5]:
        log.info("  示例 [%s/%s] %s", it["section"], it["source"], it["title"])


def curate_from_pool(cfg, use_llm=True, topup=True):
    """补抓并入池 -> 去重 -> 时间窗过滤 -> AI 策展。返回 (intro, sections, tip, pool)。"""
    pool = _pool(cfg)
    if topup:  # 发送前最后补抓一轮，把最近几小时的新闻捞进来
        fresh = sources.fetch_all(cfg)
        if fresh:
            pool.merge(fresh)
        else:
            log.warning("补抓失败，仅使用候选池中已有内容")

    items = pool.items()
    if not items:
        raise RuntimeError("候选池为空且补抓失败，无法策展")
    tip = next((i.get("_tip") for i in items if i.get("_tip")), "")

    seen = _seen()
    items = seen.filter_new(items)
    items = sources.filter_fresh(items, cfg)
    items = seen.filter_by_title(items, days=2)  # 跨天主题去重
    if not items:
        raise RuntimeError("24 小时窗口内没有新内容，跳过本次推送")
    log.info("候选条目共 %d 条，开始策展", len(items))

    recent_titles = seen.recent_titles(days=2)
    llm_cfg = cfg.get("llm") or {}
    if not use_llm:
        llm_cfg = {**llm_cfg, "api_key": ""}
    intro, sections = llm.curate(items, {**cfg, "llm": llm_cfg},
                                 recent_titles=recent_titles)

    # 按 budget 键序排定版块顺序，不依赖 LLM 的返回顺序
    order = list((cfg.get("budget") or {}).keys())
    sections.sort(key=lambda s: order.index(s["name"]) if s["name"] in order else len(order))
    return intro, sections, tip, pool


def _wait_seconds_until(hhmm):
    """距今天内指定时刻的秒数；时刻已过或格式无效返回 0。"""
    try:
        h, m = str(hhmm).split(":")
        target = datetime.now(CST).replace(hour=int(h), minute=int(m),
                                           second=0, microsecond=0)
    except (ValueError, TypeError):
        return 0
    return max(0, int((target - datetime.now(CST)).total_seconds()))


def run_daily(cfg, send_test=False, no_llm=False, preview=False):
    """日发送流水线：策展 -> 渲染 -> （等待准点）-> 发送 -> 存档/推送/状态更新。

    preview 模式只生成 preview/preview.html（补抓会照常入池）；
    send_test 模式真实发送但不记录去重、不清池、不写已发标记、不触发后置动作。
    """
    now = datetime.now(CST)
    intro, sections, tip, pool = curate_from_pool(cfg, use_llm=not no_llm)
    total = sum(len(s["items"]) for s in sections)
    log.info("策展完成：%d 个版块 %d 条", len(sections), total)

    subject = email_builder.build_subject(now)
    html_text = email_builder.build_html(now, intro, sections, tip)
    plain_text = email_builder.build_plain(intro, sections)

    if preview:
        preview_dir = os.path.join(BASE_DIR, "preview")
        os.makedirs(preview_dir, exist_ok=True)
        out = os.path.join(preview_dir, "preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html_text)
        log.info("预览已生成: %s", out)
        return

    # 准点发送：策展提前完成时，等到 send_at 再发出（重试预算因此可以前置）
    send_at = str((cfg.get("email") or {}).get("send_at") or "").strip()
    if send_at and not send_test:
        wait = _wait_seconds_until(send_at)
        if wait > 0:
            log.info("策展完成，等待 %d 秒至 %s 准点发送", wait, send_at)
            time.sleep(wait)

    send_email(cfg, subject, html_text, plain_text)
    if not send_test:
        _seen().mark_seen([it for sec in sections for it in sec["items"]])
        pool.clear()
        state.mark_sent()
        archive.save_archive(now, sections, html_text)
        archive.write_feed(now, sections)
        push.push_digest(cfg, sections)  # 尽力而为，失败不影响邮件
    log.info("本次运行完成")


def weekly(cfg, use_llm=True):
    """汇总近 7 天存档，生成一周综述邮件。"""
    now = datetime.now(CST)
    archives = archive.load_recent_archives(7)
    if not archives:
        raise RuntimeError("近 7 天没有可用的每日存档，无法生成周报")
    lines = []
    for day in archives:
        for sec in day.get("sections", []):
            for it in sec.get("items", []):
                lines.append(f"[{day.get('date')}] {it.get('title')}")
    log.info("周报素材：%d 天共 %d 条头条", len(archives), len(lines))

    llm_cfg = cfg.get("llm") or {}
    if not use_llm:
        llm_cfg = {**llm_cfg, "api_key": ""}
    system = ("你是资深新闻编辑。基于一周内每天推送过的头条，写一份本周综述："
              "提炼 3~5 条主线（而非逐条罗列），说明趋势与关联。")
    user = ("一周头条（按天）：\n- " + "\n- ".join(lines) +
            "\n\n输出 JSON：{\"intro\":\"本周一句话总览，60字以内\","
            "\"highlights\":[{\"title\":\"主线标题\",\"detail\":\"120字以内的综述\"}]}")
    data = llm.chat_json(llm_cfg, system, user)
    highlights = data.get("highlights") or []
    if not highlights:
        raise RuntimeError("周报未生成有效内容")

    sections = [{"name": "一周综述",
                 "items": [{"id": f"w{i}", "title": str(h.get("title", ""))[:80],
                            "summary": str(h.get("detail", "")),
                            "source": "day60sMsg 周报", "url": "", "published": None}
                           for i, h in enumerate(highlights, 1)]}]
    intro = str(data.get("intro", "")).strip()
    start = (now - timedelta(days=6)).strftime("%m-%d")
    subject = f"每周要闻综述 | {start} ~ {now:%m-%d}"
    html_text = email_builder.build_html(now, intro, sections)
    send_email(cfg, subject, html_text, email_builder.build_plain(intro, sections))
    archive.save_archive(now, sections, html_text)
    log.info("周报已发送并存档")


def status(cfg):
    """只读状态一览：发送记录、池规模、去重库、存档、配置摘要。"""
    llm_cfg = cfg.get("llm") or {}
    print("== day60sMsg 运行状态 ==")
    print("时间      :", datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("今日已发送:", "是" if state.sent_today() else "否")
    marker = state._read_marker("last_sent.json")
    print("上次发送  :", marker.get("at", "无记录"))
    pool = _pool(cfg)
    print(f"候选池    : {len(pool.entries)} 条（保留 {pool.retain_hours:.0f}h）")
    seen = _seen()
    print(f"去重库    : {len(seen.records)} 条 id / {len(seen.titles)} 个主题")
    adir = os.path.join(BASE_DIR, "data", "archive")
    days = [f for f in sorted(os.listdir(adir)) if f.endswith(".json")] if os.path.isdir(adir) else []
    print(f"存档      : {len(days)} 天" + (f"（{days[0][:10]} ~ {days[-1][:10]}）" if days else ""))
    enabled = [s["name"] for s in cfg["sources"] if s.get("enabled", True)]
    print(f"数据源    : {len(enabled)} 个启用 / {len(cfg['sources'])} 个配置")
    print("LLM       :", llm_cfg.get("model"), "| 备用:", llm_cfg.get("fallback_models"))
    print("兴趣主题  :", cfg.get("interests") or "未配置")
    pch = (cfg.get("push") or {}).get("channels") or {}
    print("推送渠道  :", ("、".join(pch) if pch else "未配置") +
          ("（已启用）" if (cfg.get("push") or {}).get("enabled") else "（未启用）"))
    err_log = os.path.join(BASE_DIR, "logs", "cron.err")
    if os.path.exists(err_log) and os.path.getsize(err_log):
        print("\n== cron.err 末尾 ==")
        with open(err_log, encoding="utf-8", errors="replace") as f:
            print("".join(f.readlines()[-8:]))
