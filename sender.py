"""QQ 邮箱 SMTP 发送（SSL 465）。password 为授权码，非 QQ 登录密码。"""
import logging
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("day60s")


def _smtp_password(smtp):
    """校验并返回授权码；未配置/占位符时抛 RuntimeError（给出可操作的提示）。"""
    password = str(smtp.get("password", ""))
    if not password or not password.isascii() or " " in password:
        raise RuntimeError(
            "SMTP 授权码未配置：请在 config.local.yaml（推荐）或 config.yaml 的 "
            "email.smtp.password 填入 QQ 邮箱 16 位授权码"
            "（QQ 邮箱网页版 → 设置 → 账号 → 开启 SMTP 服务后生成），不是 QQ 登录密码"
        )
    return password


def send_email(cfg, subject, html_text, plain_text, retries=3):
    """发送 HTML 邮件，失败自动重试（瞬时网络抖动不应导致漏发一天）。"""
    last = None
    for attempt in range(1, retries + 1):
        try:
            _send(cfg, subject, html_text, plain_text)
            return
        except (smtplib.SMTPException, OSError) as exc:
            last = exc
            if attempt < retries:
                delay = 3 * attempt
                log.warning("SMTP 发送失败(第 %d/%d 次): %s，%ds 后重试",
                            attempt, retries, exc, delay)
                time.sleep(delay)
    raise last


def _send(cfg, subject, html_text, plain_text):
    email_cfg = cfg["email"]
    smtp = email_cfg["smtp"]
    password = _smtp_password(smtp)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((email_cfg.get("from_name", ""), email_cfg["from_addr"]))
    msg["To"] = ", ".join(email_cfg["to_addrs"])
    msg.set_content(plain_text)
    msg.add_alternative(html_text, subtype="html")

    with smtplib.SMTP_SSL(smtp["host"], int(smtp["port"]), timeout=30) as server:
        server.login(smtp["user"], password)
        server.send_message(msg)
    log.info("邮件已发送至 %s", ", ".join(email_cfg["to_addrs"]))


def send_alert(cfg, subject, body):
    """发送纯文本告警邮件（供运行失败时自通知）。配置不完整时抛异常，由调用方忽略。"""
    email_cfg = cfg["email"]
    smtp = email_cfg["smtp"]
    password = _smtp_password(smtp)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((email_cfg.get("from_name", ""), email_cfg["from_addr"]))
    msg["To"] = ", ".join(email_cfg["to_addrs"])
    msg.set_content(body)

    with smtplib.SMTP_SSL(smtp["host"], int(smtp["port"]), timeout=30) as server:
        server.login(smtp["user"], password)
        server.send_message(msg)
    log.info("告警邮件已发送至 %s", ", ".join(email_cfg["to_addrs"]))
