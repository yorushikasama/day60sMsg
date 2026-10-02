"""多渠道头条推送：ntfy / Bark / Telegram。

全部尽力而为：单渠道失败只记日志，绝不影响邮件发送。
配置（config.yaml push.channels）里启用了哪个渠道就走哪个。
"""
import logging

import requests

log = logging.getLogger("day60s")


def _top_items(sections, n):
    """按版块顺序取出前 n 条（版块已按配置顺序排好）。"""
    out = []
    for sec in sections:
        out.extend(sec.get("items", []))
    return out[:n]


def _ntfy(channel, title, body):
    headers = {"Title": title, "Tags": "newspaper"}
    if channel.get("token"):
        headers["Authorization"] = f"Bearer {channel['token']}"
    requests.post(channel["url"], data=body.encode("utf-8"),
                  headers=headers, timeout=15)


def _bark(channel, title, body):
    # url 形如 https://api.day.app/<你的BarkKey>
    requests.post(channel["url"].rstrip("/"),
                  json={"title": title, "body": body, "group": "day60sMsg"},
                  timeout=15)


def _telegram(channel, title, body):
    requests.post(
        f"https://api.telegram.org/bot{channel['bot_token']}/sendMessage",
        json={"chat_id": channel["chat_id"], "text": f"{title}\n\n{body}"},
        timeout=15,
    )


CHANNELS = {"ntfy": _ntfy, "bark": _bark, "telegram": _telegram}


def push_digest(cfg, sections):
    """把头条速览推送到所有已配置渠道。返回成功渠道数。"""
    pcfg = cfg.get("push") or {}
    if not pcfg.get("enabled"):
        log.info("头条推送未启用（push.enabled=false）")
        return 0

    n = int(pcfg.get("top_n", 5))
    items = _top_items(sections, n)
    if not items:
        log.info("无条目可推送")
        return 0

    title = "day60sMsg 头条速览"
    body = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(items, 1))

    channels = pcfg.get("channels") or {}
    sent = 0
    for name, channel in channels.items():
        fn = CHANNELS.get(name)
        if not fn:
            log.warning("未知推送渠道 %s，跳过", name)
            continue
        try:
            fn(channel, title, body)
            sent += 1
        except Exception as exc:
            log.warning("推送渠道 %s 失败（忽略）: %s", name, exc)
    log.info("头条推送完成：%d 条 × %d/%d 渠道成功", len(items), sent, len(channels))
    return sent
