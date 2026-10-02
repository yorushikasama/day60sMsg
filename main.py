"""day60sMsg 主流程：分时段抓取入池 -> 9 点前补抓、策展、发送。

用法：
  python main.py --crawl      # 只抓取并入候选池（供 cron 分时段调用，如每 3 小时）
  python main.py --send       # 补抓一次 -> 从池中策展 -> 发送（供 cron 每天 8:45 调用）
  python main.py --send-if-needed  # 仅当今天尚未发送时执行 --send（供补偿 cron 调用）
  python main.py              # 与 --send 相同
  python main.py --preview    # 策展并生成 preview/preview.html（补抓会照常入池；不发送、不记录去重）
  python main.py --send-test  # 发送验证 SMTP，不记录去重、不清池、不写已发标记
  python main.py --dry-run    # 只抓取并打印统计，不写任何状态
  python main.py --no-llm     # 本次跳过 AI，使用权重降级挑选
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime

import yaml

import email_builder
import llm
import sources
from pool import CandidatePool
from sender import send_alert, send_email
from store import SeenStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CST = sources.CST

log = logging.getLogger("day60s")
_INTENT = None  # 本次运行意图（send/crawl/preview/dry），失败告警只针对 send


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


def _deep_merge(base, override):
    """递归合并：dict 逐键覆盖，其他类型（含 list）整体替换。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    """读取 config.yaml；若存在 config.local.yaml（已 gitignore）则叠加覆盖。

    这样服务器上的邮箱授权码、API key 等私密配置与代码分离，git pull 不会冲突。
    """
    with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    local_path = os.path.join(BASE_DIR, "config.local.yaml")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local)
        log.debug("已叠加本地配置 %s", local_path)
    return cfg


def _pool(cfg):
    """候选池保留期 = 抓取窗口 + 12h 余量，必须 >= 窗口，否则窗口配置被架空。"""
    retain = int(cfg["fetch"].get("window_hours", 24)) + 12
    return CandidatePool(os.path.join(BASE_DIR, "data", "pool.json"), retain_hours=retain)


def _seen():
    return SeenStore(os.path.join(BASE_DIR, "data", "seen.json"))


def cmd_crawl(cfg):
    """分时段抓取：结果并入候选池，供发送时策展使用。"""
    items = sources.fetch_all(cfg)
    if not items:
        raise RuntimeError("本次抓取所有源均失败，未入池任何内容")
    _pool(cfg).merge(items)


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

    items = _seen().filter_new(items)
    items = sources.filter_fresh(items, cfg)
    if not items:
        raise RuntimeError("24 小时窗口内没有新内容，跳过本次推送")
    log.info("候选条目共 %d 条，开始策展", len(items))

    llm_cfg = cfg.get("llm") or {}
    if not use_llm:
        llm_cfg = {**llm_cfg, "api_key": ""}
    intro, sections = llm.curate(items, {**cfg, "llm": llm_cfg})

    # 按 budget 键序排定版块顺序，不依赖 LLM 的返回顺序
    order = list((cfg.get("budget") or {}).keys())
    sections.sort(key=lambda s: order.index(s["name"]) if s["name"] in order else len(order))
    return intro, sections, tip, pool


def _data_path(name):
    return os.path.join(BASE_DIR, "data", name)


def _mark_sent():
    """记录今天已发送（供补偿 cron --send-if-needed 判断）。"""
    path = _data_path("last_sent.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(CST)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": now.strftime("%Y-%m-%d"), "at": now.isoformat(timespec="seconds")}, f)


def _sent_today():
    try:
        with open(_data_path("last_sent.json"), encoding="utf-8") as f:
            return json.load(f).get("date") == datetime.now(CST).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return False


def _maybe_alert(cfg):
    """发送失败时给自己发一封告警邮件；每天最多一封，配置不完整则静默跳过。"""
    marker = _data_path("alert_sent.json")
    today = datetime.now(CST).strftime("%Y-%m-%d")
    try:
        with open(marker, encoding="utf-8") as f:
            if json.load(f).get("date") == today:
                log.info("今日失败告警已发过，跳过")
                return
    except (OSError, ValueError):
        pass

    tail = []
    log_file = os.path.join(BASE_DIR, "logs", f"{datetime.now(CST):%Y%m}.log")
    try:
        with open(log_file, encoding="utf-8") as f:
            tail = f.readlines()[-30:]
    except OSError:
        pass
    body = (
        "day60sMsg 今日简报发送失败，请检查服务器日志。\n\n"
        f"时间: {datetime.now(CST):%Y-%m-%d %H:%M:%S}\n"
        f"服务器: /opt/day60sMsg\n\n"
        "---- 日志末尾 ----\n" + "".join(tail)
    )
    send_alert(cfg, "【告警】day60sMsg 简报发送失败", body)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        json.dump({"date": today}, f)


def _acquire_lock():
    """简易运行锁：30 分钟 TTL，防止 cron 重叠；崩溃残留的锁会自动过期。"""
    path = _data_path("run.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        age = datetime.now().timestamp() - os.path.getmtime(path)
        if age < 30 * 60:
            return False
        log.warning("发现超过 30 分钟的残留锁，视为已过期并接管")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock():
    try:
        os.remove(_data_path("run.lock"))
    except OSError:
        pass


def main():
    global _INTENT
    parser = argparse.ArgumentParser(description="每日要闻速览：分时段抓取 + 定时策展发送")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--crawl", action="store_true", help="只抓取入池（分时段 cron 用）")
    group.add_argument("--send", action="store_true", help="补抓+策展+发送（默认行为）")
    group.add_argument("--send-if-needed", action="store_true",
                       help="仅当今天尚未发送时执行 --send（补偿 cron 用）")
    parser.add_argument("--preview", action="store_true", help="只生成 preview/preview.html")
    parser.add_argument("--send-test", action="store_true", help="发送验证，不记录去重、不清池")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印统计")
    parser.add_argument("--no-llm", action="store_true", help="跳过 AI，使用权重挑选")
    args = parser.parse_args()
    _INTENT = "crawl" if args.crawl else "dry" if args.dry_run else "preview" if args.preview else "send"

    setup_logging()
    if not _acquire_lock():
        log.warning("已有实例在运行（30 分钟内），本次退出")
        return
    import atexit
    atexit.register(_release_lock)
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

    if args.send_if_needed:
        if _sent_today():
            log.info("今日简报已发送，--send-if-needed 跳过")
            return
        log.info("今日尚未发送，执行补偿发送")

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
        _mark_sent()
    log.info("本次运行完成")


if __name__ == "__main__":
    cfg = None
    try:
        cfg = main() or cfg
    except Exception:
        log.exception("运行失败")
        if _INTENT == "send":  # 只有正式发送路径的失败才值得告警
            try:
                if cfg is None:
                    cfg = load_config()
                _maybe_alert(cfg)
            except Exception:
                log.warning("失败告警邮件未能发送（忽略）")
        sys.exit(1)
