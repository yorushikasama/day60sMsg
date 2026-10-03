"""失败告警：正式发送失败时给自己发一封邮件，每天最多一封。"""
import logging
import os
from datetime import datetime

import state
from sender import send_alert
from sources import CST

log = logging.getLogger("day60s")


def maybe_alert(cfg):
    if state.alerted_today():
        log.info("今日失败告警已发过，跳过")
        return

    body = (
        "day60sMsg 今日简报发送失败，请检查服务器日志。\n\n"
        f"时间: {datetime.now(CST):%Y-%m-%d %H:%M:%S}\n"
        "服务器: /opt/day60sMsg\n\n"
        "---- 日志末尾 ----\n" + _log_tail(30)
    )
    send_alert(cfg, "【告警】day60sMsg 简报发送失败", body)
    state.mark_alerted()
    log.info("失败告警邮件已发出")


def _log_tail(lines=30):
    path = os.path.join(state.BASE_DIR, "logs", f"{datetime.now(CST):%Y%m}.log")
    try:
        with open(path, encoding="utf-8") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""
