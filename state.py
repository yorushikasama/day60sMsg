"""本地运行状态：数据目录、运行锁、今日已发/告警去重标记、时区自检。"""
import atexit
import json
import logging
import os
import time as _time
from datetime import datetime

from sources import CST

log = logging.getLogger("day60s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def data_path(name):
    """data/ 下的状态文件路径（目录不存在会自动创建）。"""
    path = os.path.join(BASE_DIR, "data", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def check_timezone():
    """自检系统时区与程序常量是否一致；不一致时告警（返回是否一致）。

    程序的调度假定运行在 Asia/Shanghai：cron 按系统时区解释时间，而代码用
    CST 常量算"准点发送"，两者不一致会导致发送时刻整体偏移（服务器迁移后常见）。
    """
    local_offset = _time.localtime().tm_gmtoff              # 系统 UTC 偏移（秒）
    expected_offset = int(datetime.now(CST).utcoffset().total_seconds())
    if local_offset != expected_offset:
        log.error(
            "系统时区偏移 %+d 小时与程序预期 %+d 小时不一致："
            "cron 会按系统时区触发，发送时刻将整体偏移！"
            "请执行 sudo timedatectl set-timezone Asia/Shanghai 并重启 cron",
            local_offset // 3600, expected_offset // 3600,
        )
        return False
    return True


def acquire_lock(ttl_minutes=30):
    """简易运行锁：TTL 防止 cron 重叠；崩溃残留的锁会自动过期。

    进程退出时自动释放（atexit）。
    """
    path = data_path("run.lock")
    if os.path.exists(path):
        age = datetime.now().timestamp() - os.path.getmtime(path)
        if age < ttl_minutes * 60:
            return False
        log.warning("发现超过 %d 分钟的残留锁，视为已过期并接管", ttl_minutes)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)
    return True


def release_lock():
    try:
        os.remove(data_path("run.lock"))
    except OSError:
        pass


def mark_sent():
    now = datetime.now(CST)
    with open(data_path("last_sent.json"), "w", encoding="utf-8") as f:
        json.dump({"date": now.strftime("%Y-%m-%d"), "at": now.isoformat(timespec="seconds")}, f)


def sent_today():
    return _read_marker("last_sent.json").get("date") == datetime.now(CST).strftime("%Y-%m-%d")


def mark_alerted():
    with open(data_path("alert_sent.json"), "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now(CST).strftime("%Y-%m-%d")}, f)


def alerted_today():
    return _read_marker("alert_sent.json").get("date") == datetime.now(CST).strftime("%Y-%m-%d")


def _read_marker(name):
    try:
        with open(data_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
