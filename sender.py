"""QQ 邮箱 SMTP 发送（SSL 465）。password 为授权码，非 QQ 登录密码。"""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("day60s")


def send_email(cfg, subject, html_text, plain_text):
    email_cfg = cfg["email"]
    smtp = email_cfg["smtp"]

    password = str(smtp.get("password", ""))
    if not password or not password.isascii() or " " in password:
        raise RuntimeError(
            "SMTP 授权码未配置：请在 config.yaml 的 email.smtp.password 填入 QQ 邮箱 16 位授权码"
            "（QQ 邮箱网页版 → 设置 → 账号 → 开启 SMTP 服务后生成），不是 QQ 登录密码"
        )

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
