"""day60sMsg 命令行入口：解析参数并调度 pipeline。

  python main.py --crawl      # 只抓取并入候选池（分时段 cron 用）
  python main.py --send       # 补抓 -> 策展 -> 等到 send_at(默认09:00)准点发送（cron 8:45 触发）
  python main.py --send-if-needed  # 仅当今天尚未发送时执行 --send（补偿 cron 用）
  python main.py --preview    # 策展并生成 preview/preview.html（不发送、不记录去重）
  python main.py --send-test  # 发送验证 SMTP，不记录去重、不清池、不触发后置动作
  python main.py --weekly     # 汇总近 7 天存档，生成一周综述邮件
  python main.py --status     # 查看运行状态（不抓取不发送）
  python main.py --dry-run    # 只抓取并打印统计，不写任何状态
  python main.py --no-llm     # 本次跳过 AI，使用权重降级挑选
"""
import argparse
import logging
import os
import sys
from datetime import datetime

import alerting
import config
import pipeline
import state
from sources import CST

log = logging.getLogger("day60s")


def setup_logging():
    # cron.err 只装报错堆栈，超过 2MB 轮转一次
    err_log = os.path.join(state.BASE_DIR, "logs", "cron.err")
    try:
        if os.path.exists(err_log) and os.path.getsize(err_log) > 2 * 1024 * 1024:
            os.replace(err_log, err_log + ".1")
    except OSError:
        pass
    log_dir = os.path.join(state.BASE_DIR, "logs")
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


def build_parser():
    parser = argparse.ArgumentParser(description="每日要闻速览：分时段抓取 + 定时策展发送")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--crawl", action="store_true", help="只抓取入池（分时段 cron 用）")
    group.add_argument("--send", action="store_true", help="策展并准点发送（默认行为）")
    group.add_argument("--send-if-needed", action="store_true",
                       help="仅当今天尚未发送时执行发送（补偿 cron 用）")
    parser.add_argument("--preview", action="store_true", help="只生成 preview/preview.html")
    parser.add_argument("--send-test", action="store_true", help="发送验证，不记录去重、不清池")
    parser.add_argument("--weekly", action="store_true", help="汇总近 7 天存档，生成一周综述邮件")
    parser.add_argument("--status", action="store_true", help="查看运行状态（不抓取不发送）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印统计")
    parser.add_argument("--no-llm", action="store_true", help="跳过 AI，使用权重降级挑选")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    setup_logging()
    # 只有正式发送路径的失败才值得告警
    alertable = args.send or args.send_if_needed

    if not state.acquire_lock():
        log.warning("已有实例在运行（30 分钟内），本次退出")
        return 0

    try:
        cfg = config.load_config()

        if args.dry_run:
            pipeline.dry_run(cfg)
        elif args.crawl:
            pipeline.crawl(cfg)
            log.info("抓取入池完成")
        elif args.status:
            pipeline.status(cfg)
        elif args.weekly:
            pipeline.weekly(cfg, use_llm=not args.no_llm)
        else:
            if args.send_if_needed:
                if state.sent_today():
                    log.info("今日简报已发送，--send-if-needed 跳过")
                    return 0
                log.info("今日尚未发送，执行补偿发送")
            pipeline.run_daily(cfg, send_test=args.send_test,
                               no_llm=args.no_llm, preview=args.preview)
        return 0
    except Exception:
        log.exception("运行失败")
        if alertable:
            try:
                alerting.maybe_alert(config.load_config())
            except Exception:
                log.warning("失败告警邮件未能发送（忽略）")
        return 1
    finally:
        state.release_lock()


if __name__ == "__main__":
    sys.exit(main())
