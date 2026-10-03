"""配置加载：config.yaml 为基底，config.local.yaml（私密覆盖，已 gitignore）叠加。"""
import logging
import os

import yaml

log = logging.getLogger("day60s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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
    with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    local_path = os.path.join(BASE_DIR, "config.local.yaml")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local)
        log.debug("已叠加本地配置 %s", local_path)
    return cfg
