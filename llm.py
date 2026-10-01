"""AI 策展：把候选新闻交给 LLM 挑选、翻译、浓缩；无 key 时降级为按权重挑选。"""
import json
import logging
import re

import requests

log = logging.getLogger("day60s")

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


def curate(items, cfg):
    """返回 (intro, sections)。sections: [{name, items:[完整条目dict]}]。"""
    budget = cfg.get("budget") or {}
    llm_cfg = cfg.get("llm") or {}
    api_key = _api_key(llm_cfg)
    if not api_key:
        log.info("未配置有效的 LLM api_key，使用权重降级挑选")
        return _heuristic(items, budget)
    try:
        intro, sections = _llm_curate(items, {**llm_cfg, "api_key": api_key}, budget)
        return intro, sections
    except Exception as exc:
        log.warning("AI 策展失败，降级为权重挑选: %s", exc)
        return _heuristic(items, budget)


# ---------------------------------------------------------------- LLM
def _llm_curate(items, llm_cfg, budget):
    candidates = [
        {
            "id": it["id"],
            "section": it["section"],
            "source": it["source"],
            "published": it["published"].strftime("%m-%d %H:%M") if it["published"] else "今天",
            "title": it["title"],
            "summary": it["summary"],
        }
        for it in items
    ]
    user_prompt = (
        "下面是今天抓取到的候选新闻(JSON)。请挑选最有价值的信息，输出格式如下：\n"
        f"{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=1)}\n\n"
        "规则：\n"
        f"1. 各版块条数预算：{json.dumps(budget, ensure_ascii=False)}，可少选不可编造。\n"
        "2. 只能选择给定 id 的条目，标题和摘要必须忠于原文，禁止编造细节；"
        "原文是英文的必须翻译成中文。\n"
        "3. summary 为 45 字以内的中文摘要，只保留最重要的信息（谁、做了什么、关键数字/结果）。\n"
        "4. items 按重要性从高到低排序；版块名必须与候选中的 section 完全一致。\n\n"
        f"候选新闻：\n{json.dumps(candidates, ensure_ascii=False)}"
    )

    content = _chat(llm_cfg, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    data = _extract_json(content)
    by_id = {it["id"]: it for it in items}
    sections = []
    for sec in data.get("sections", []):
        name = str(sec.get("name", "")).strip()
        picked = []
        for row in sec.get("items", []):
            it = by_id.get(str(row.get("id", "")))
            if not it:
                continue
            picked.append({**it,
                           "title": str(row.get("title") or it["title"]).strip(),
                           "summary": str(row.get("summary") or it["summary"]).strip()})
        if name and picked:
            sections.append({"name": name, "items": picked})
    if not sections:
        raise ValueError("LLM 未返回有效条目")
    return str(data.get("intro", "")).strip(), sections


def _chat(llm_cfg, messages):
    base = llm_cfg["base_url"].rstrip("/")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {llm_cfg['api_key']}"},
        json={
            "model": llm_cfg["model"],
            "messages": messages,
            "temperature": float(llm_cfg.get("temperature", 0.2)),
        },
        timeout=int(llm_cfg.get("timeout", 120)),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("LLM 输出中未找到 JSON")
    return json.loads(match.group(0))


# ---------------------------------------------------------------- 降级
def _heuristic(items, budget):
    """无 AI 时：按 权重*来源去重 排序，直接截取预算条数。"""
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
            picked.append(it)
        if picked:
            sections.append({"name": name, "items": picked})
            used_titles.add(picked[0]["title"])
    intro = sections[0]["items"][0]["title"] if sections else "今日暂无精选内容"
    return intro, sections
