"""day60sMsg 主流程：分时段抓取入池 -> 9 点前补抓、策展、发送。

用法：
  python main.py --crawl      # 只抓取并入候选池（供 cron 分时段调用，如每 3 小时）
  python main.py --send       # 补抓一次 -> 从池中策展 -> 发送（供 cron 每天 8:45 调用）
  python main.py              # 与 --send 相同
  python main.py --preview    # 策展后只生成 preview/preview.html，不发送、不改状态
  python main.py --send-test  # 发送验证 SMTP，不记录去重、不清池
  python main.py --dry-run    # 只抓取并打印统计，不写任何状态
  python main.py --no-llm     # 本次跳过 AI，使用权重降级挑选
"""
import argparse
import logging
import os
import sys
from datetime import datetime

import yaml

import email_builder
import llm
import sources
from pool import CandidatePool
from sender import send_email
from store import SeenStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CST = sources.CST

log = logging.getLogger("day60s")


def setup_logging():
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(log_dir, f"{datetime.now(CST):%Y%m}.log"), encoding="utf-8"
            ),
        ],
    )


def load_config():
    with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _pool():
    return CandidatePool(os.path.join(BASE_DIR, "data", "pool.json"))


def _seen():
    return SeenStore(os.path.join(BASE_DIR, "data", "seen.json"))


def cmd_crawl(cfg):
    """分时段抓取：结果并入候选池，供发送时策展使用。"""
    items = sources.fetch_all(cfg)
    if not items:
        raise RuntimeError("本次抓取所有源均失败，未入池任何内容")
    _pool().merge(items)


def curate_from_pool(cfg, use_llm=True, topup=True):
    """补抓并入池 -> 去重 -> 时间窗过滤 -> AI 策展。返回 (intro, sections, tip, pool)。"""
    pool = _pool()
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

    items = _seen().filter_new(items)
    items = sources.filter_fresh(items, cfg)
    if not items:
        raise RuntimeError("24 小时窗口内没有新内容，跳过本次推送")
    log.info("候选条目共 %d 条，开始策展", len(items))

    llm_cfg = cfg.get("llm") or {}
    if not use_llm:
        llm_cfg = {**llm_cfg, "api_key": ""}
    intro, sections = llm.curate(items, {**cfg, "llm": llm_cfg})
    return intro, sections, tip, pool


def main():
    parser = argparse.ArgumentParser(description="每日要闻速览：分时段抓取 + 定时策展发送")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--crawl", action="store_true", help="只抓取入池（分时段 cron 用）")
    group.add_argument("--send", action="store_true", help="补抓+策展+发送（默认行为）")
    parser.add_argument("--preview", action="store_true", help="只生成 preview/preview.html")
    parser.add_argument("--send-test", action="store_true", help="发送验证，不记录去重、不清池")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印统计")
    parser.add_argument("--no-llm", action="store_true", help="跳过 AI，使用权重挑选")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config()
    now = datetime.now(CST)

    if args.dry_run:
        items = sources.fetch_all(cfg)
        fresh = sources.filter_fresh(items, cfg)
        log.info("dry-run: 抓取 %d 条，时间窗过滤后 %d 条", len(items), len(fresh))
        for it in fresh[:5]:
            log.info("  示例 [%s/%s] %s", it["section"], it["source"], it["title"])
        return

    if args.crawl:
        cmd_crawl(cfg)
        log.info("抓取入池完成")
        return

    intro, sections, tip, pool = curate_from_pool(cfg, use_llm=not args.no_llm)
    total = sum(len(s["items"]) for s in sections)
    log.info("策展完成：%d 个版块 %d 条", len(sections), total)

    subject = email_builder.build_subject(now)
    html_text = email_builder.build_html(now, intro, sections, tip)
    plain_text = email_builder.build_plain(intro, sections)

    if args.preview:
        preview_dir = os.path.join(BASE_DIR, "preview")
        os.makedirs(preview_dir, exist_ok=True)
        out = os.path.join(preview_dir, "preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html_text)
        log.info("预览已生成: %s", out)
        return

    send_email(cfg, subject, html_text, plain_text)
    if not args.send_test:
        _seen().mark_seen([it for sec in sections for it in sec["items"]])
        pool.clear()
    log.info("本次运行完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("运行失败")
        sys.exit(1)
