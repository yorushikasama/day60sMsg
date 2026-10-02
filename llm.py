"""AI 策展：把候选新闻交给 LLM 挑选、翻译、浓缩；无 key 时降级为按权重挑选。"""
import json
import logging
import re
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests

from sources import CST

log = logging.getLogger("day60s")

DUP_RATIO = 0.72  # 标题相似度折叠阈值

SYSTEM_PROMPT = (
    "你是一位资深新闻编辑，负责为中文读者制作一份 60 秒可读完的每日要闻简报。"
    "你的判断标准：影响重大（政策/军事/选举/重大科技进展/重大事故）、涉及全球主要人物与机构、"
    "对普通人生活有实际影响；剔除八卦、软文和同质化重复话题。"
)

OUTPUT_SCHEMA = {
    "intro": "一句话导读，60字以内，概括今天最重要的主线",
    "sections": [
        {
            "name": "版块名，必须与给定版块名完全一致",
            "items": [
                {"id": "候选条目id", "title": "中文标题", "summary": "45字以内的中文摘要"}
            ],
        }
    ],
}


def _api_key(cfg):
    """返回可用的 api_key；空值或仍是中文占位符时返回空串。"""
    key = str((cfg.get("api_key") or "")).strip()
    if not key:
        return ""
    if not key.isascii():
        log.warning("LLM api_key 仍是占位符（含非 ASCII 字符），本次使用权重降级挑选")
        return ""
    return key


def curate(items, cfg, recent_titles=()):
    """返回 (intro, sections)。sections: [{name, items:[完整条目dict]}]。"""
    budget = cfg.get("budget") or {}
    llm_cfg = cfg.get("llm") or {}
    interests = cfg.get("interests") or ()
    api_key = _api_key(llm_cfg)
    if not api_key:
        log.info("未配置有效的 LLM api_key，使用权重降级挑选")
        return _heuristic(items, budget)
    try:
        intro, sections = _llm_curate(
            items, {**llm_cfg, "api_key": api_key}, budget,
            interests=interests, recent_titles=recent_titles,
        )
        return intro, sections
    except Exception as exc:
        log.warning("AI 策展失败，降级为权重挑选: %s", exc)
        return _heuristic(items, budget)


# ---------------------------------------------------------------- LLM
def _norm_title(title):
    """标题归一化（去标点、小写），用于跨源/跨天去重。"""
    return re.sub(r"[^\w]", "", str(title).lower())


def _similar(a, b):
    """标题近似判断：共同长前缀直接判重，否则用相似度比率。"""
    if not a or not b:
        return False
    if len(a) >= 12 and len(b) >= 12 and a[:20] == b[:20]:
        return True
    return SequenceMatcher(None, a, b).ratio() >= DUP_RATIO


def _collapse(items):
    """近重复折叠：每组只保留发布最新的一条（信息通常最完整）。

    返回 (保留列表[原顺序], 折叠数)。
    """
    epoch = datetime.min.replace(tzinfo=CST)
    ordered = sorted(items, key=lambda i: i.get("published") or epoch, reverse=True)
    reps = []  # 每组的代表（最新一条）
    for it in ordered:
        key = _norm_title(it["title"])
        if not any(_similar(key, _norm_title(g["title"])) for g in reps):
            reps.append(it)
    kept_ids = {g["id"] for g in reps}
    return [it for it in items if it["id"] in kept_ids], len(items) - len(reps)


def _candidate_view(it):
    """构造提示词用的候选条目：空字段不输出，压缩 token。"""
    c = {"id": it["id"], "section": it["section"], "source": it["source"]}
    c["published"] = (it["published"].strftime("%m-%d %H:%M")
                      if it.get("published") else "今天")
    c["title"] = it["title"]
    if it.get("summary"):
        c["summary"] = it["summary"]
    return c


def _llm_curate(items, llm_cfg, budget, interests=(), recent_titles=()):
    kept, collapsed_n = _collapse(items)
    if collapsed_n:
        log.info("近重复折叠：%d 条候选压缩为 %d 条", len(items), len(kept))
    candidates = [_candidate_view(it) for it in kept]
    user_prompt = (
        "下面是今天抓取到的候选新闻(JSON)。请挑选最有价值的信息，输出格式如下：\n"
        f"{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=1)}\n\n"
        "规则：\n"
        f"1. 各版块条数预算：{json.dumps(budget, ensure_ascii=False)}，可少选不可编造。\n"
        "2. 只能选择给定 id 的条目，标题和摘要必须忠于原文，禁止编造细节；"
        "原文是英文的必须翻译成中文。\n"
        "3. summary 为 45 字以内的中文摘要，只保留最重要的信息（谁、做了什么、关键数字/结果）。\n"
        "4. items 按重要性从高到低排序；版块名必须与候选中的 section 完全一致。\n"
        "5. 同一事件只能出现一次：若多个候选描述同一事件（措辞或来源不同），"
        "只选信息最完整、来源最权威的一条，其余舍弃。\n"
    )
    if interests:
        user_prompt += (
            "6. 读者特别关注这些主题：" + "、".join(interests) + "。"
            "重要性相近时，优先选择与关注主题相关的条目。\n"
        )
    if recent_titles:
        user_prompt += (
            "近两天已推送过的主题（不要重复选择；若是重大新进展可以选用，"
            "并在 summary 末尾标注[续报]）：\n- "
            + "\n- ".join(recent_titles) + "\n"
        )
    user_prompt += f"\n候选新闻：\n{json.dumps(candidates, ensure_ascii=False)}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # 请求与解析一并纳入重试：模型偶发的格式错误也值得重试
    data = _call_and_parse(llm_cfg, messages)
    by_id = {it["id"]: it for it in items}
    picked_titles = set()  # 跨版块防重：同一事件不重复出现
    sections = []
    for sec in data.get("sections", []):
        name = str(sec.get("name", "")).strip()
        picked = []
        for row in sec.get("items", []):
            it = by_id.get(str(row.get("id", "")))
            if not it:
                continue
            key = _norm_title(it["title"])
            if key in picked_titles:
                continue
            picked_titles.add(key)
            picked.append({**it,
                           "orig_title": it["title"],  # 原始标题，供跨天主题去重
                           "title": str(row.get("title") or it["title"]).strip(),
                           "summary": str(row.get("summary") or it["summary"]).strip()})
        if name and picked:
            sections.append({"name": name, "items": picked})
    if not sections:
        raise ValueError("LLM 未返回有效条目")
    return str(data.get("intro", "")).strip(), sections


def _call_and_parse(llm_cfg, messages):
    """调用 LLM 并解析 JSON；解析失败在重试循环内处理（换模型/重试）。"""
    return _chat_with_retry(llm_cfg, messages, parse=_extract_json)


class LLMError(RuntimeError):
    """LLM 调用失败。retryable 表示该错误值得重试（限流/服务端故障/网络）。"""

    def __init__(self, message, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def _build_models(llm_cfg):
    """主模型 + 备用模型（去重，保持顺序）。"""
    models = [llm_cfg["model"]]
    for m in llm_cfg.get("fallback_models") or []:
        if m and m not in models:
            models.append(m)
    return models


def _chat(llm_cfg, messages, model=None):
    """单次调用。可恢复错误抛 LLMError(retryable=True)，交由上层重试/切换模型。"""
    base = llm_cfg["base_url"].rstrip("/")
    payload = {"model": model or llm_cfg["model"], "messages": messages}
    # 推理模型（gpt-5.x、o 系列等）通常拒绝 temperature，配置为 null 则不发送
    temperature = llm_cfg.get("temperature")
    if temperature is not None:
        payload["temperature"] = float(temperature)
    max_tokens = llm_cfg.get("max_tokens")
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {llm_cfg['api_key']}"},
            json=payload,
            timeout=int(llm_cfg.get("timeout", 120)),
        )
    except requests.RequestException as exc:
        raise LLMError(f"网络错误: {exc}", retryable=True) from exc

    if resp.status_code != 200:
        # 429 限流与 5xx 服务端故障可重试；401/403/404 属配置问题，重试无意义
        retryable = resp.status_code == 429 or resp.status_code >= 500
        raise LLMError(
            f"LLM 接口返回 {resp.status_code}: {resp.text[:200]}",
            retryable=retryable, status=resp.status_code,
        )
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"LLM 响应结构异常: {str(data)[:300]}") from exc
    if not content:
        raise LLMError("LLM 返回内容为空")
    return content


def chat_json(llm_cfg, system, user):
    """供其他模块（周报等）使用的通用对话入口：带重试/故障转移，返回解析后的 JSON。"""
    api_key = _api_key(llm_cfg)
    if not api_key:
        raise RuntimeError("LLM api_key 未配置")
    return _call_and_parse({**llm_cfg, "api_key": api_key},
                           [{"role": "system", "content": system},
                            {"role": "user", "content": user}])


def _chat_with_retry(llm_cfg, messages, parse=None):
    """按模型依次尝试；每个模型内部按指数退避重试，用尽后切换到下一个模型。

    各模型的限流额度相互独立，切换模型常能立即绕过 429。
    parse 传入时，解析失败也视为可重试错误（模型偶发格式瑕疵很常见）。
    设有总时限（max_total_seconds），避免多模型叠加重试拖过发送时间点。
    """
    models = _build_models(llm_cfg)
    retries = max(1, int(llm_cfg.get("retries", 3)))
    backoff = float(llm_cfg.get("retry_backoff", 5))
    deadline = time.monotonic() + float(llm_cfg.get("max_total_seconds", 600))
    last_err = None

    for mi, model in enumerate(models):
        for attempt in range(1, retries + 1):
            try:
                content = _chat(llm_cfg, messages, model=model)
                result = parse(content) if parse else content
                if mi or attempt > 1:
                    log.info("模型 %s 第 %d 次尝试成功", model, attempt)
                return result
            except ValueError as exc:
                # 模型返回了内容但无法解析为 JSON —— 值得换个模型/重试
                last_err = LLMError(f"输出无法解析: {exc}", retryable=True)
                if attempt < retries:
                    delay = backoff * (2 ** (attempt - 1))
                    if time.monotonic() + delay > deadline:
                        raise last_err
                    log.warning("模型 %s 第 %d/%d 次输出无法解析，%.0fs 后重试",
                                model, attempt, retries, delay)
                    time.sleep(delay)
                else:
                    log.warning("模型 %s 重试 %d 次仍无法解析，切换下一个模型", model, retries)
            except LLMError as exc:
                last_err = exc
                if not exc.retryable:
                    log.warning("模型 %s 不可重试的错误，立即切换: %s", model, exc)
                    break
                if attempt < retries:
                    delay = backoff * (2 ** (attempt - 1))
                    if time.monotonic() + delay > deadline:
                        log.warning("已达重试总时限，放弃重试")
                        raise last_err
                    log.warning("模型 %s 第 %d/%d 次失败(%s)，%.0fs 后重试",
                                model, attempt, retries, exc, delay)
                    time.sleep(delay)
                else:
                    log.warning("模型 %s 重试 %d 次均失败，切换下一个模型", model, retries)
    raise last_err or LLMError("没有可用的模型")


def _extract_json(text):
    """从模型输出中提取 JSON。容忍 markdown 代码围栏与多余说明文字。"""
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("输出中未找到 JSON 对象")
    raw = match.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 常见瑕疵：尾随逗号、中文全角引号；做一次温和修复后重试
        fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        fixed = fixed.replace("“", '"').replace("”", '"')
        return json.loads(fixed)


# ---------------------------------------------------------------- 降级
def _heuristic(items, budget):
    """无 AI 时：按来源权重排序选取，并做跨版块标题去重。"""
    used_titles, sections = set(), []
    for name, limit in budget.items():
        pool = sorted(
            [i for i in items if i["section"] == name],
            key=lambda i: i["weight"], reverse=True,
        )
        picked = []
        for it in pool:
            if len(picked) >= limit:
                break
            key = _norm_title(it["title"])
            if key in used_titles:
                continue  # 同一事件已在其他版块出现
            used_titles.add(key)
            picked.append(it)
        if picked:
            sections.append({"name": name, "items": picked})
    intro = sections[0]["items"][0]["title"] if sections else "今日暂无精选内容"
    return intro, sections
