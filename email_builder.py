"""渲染 HTML / 纯文本邮件 —— 「牛油果日和」色卡 · 印刷色样风。

设计语言取自参考色卡：大留白、衬线刊名加宽字距、纯色块色板、圆角胶囊、
底部色样栏。正文保持编辑部式高密度（全左对齐、无装饰）。

色彩（色卡原值）：
  #7f3b07 Burnt Caramel  /  #355410 Avocado Leaf  /  #b5c472 Lemon Basil
  #e5d699 Honey Cream    /  #e6e6ba（浅底）

- 600px 表格布局，全内联样式（Outlook、QQ 邮箱等会剥离 <style>）
- 锁定浅色（color-scheme=light），避免客户端反色破坏色彩系统
"""
import html
from datetime import date, datetime

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
LAUNCH = date(2026, 10, 1)  # 创刊日，用于期号

# ---- 色卡原值 ----
CARAMEL = "#7f3b07"
GREEN = "#355410"
BASIL = "#b5c472"
HONEY = "#e5d699"
PALE = "#e6e6ba"

# ---- 功能性用色 ----
INK = "#2f3524"      # 主文字：墨绿灰
BODY = "#565c44"     # 摘要
MUTED = "#83836a"    # meta / 弱化
PAGE = "#f2f1e6"     # 页面底：通风纸色
CARD = "#fdfcf4"     # 纸面
FRAME = "#ddd9b0"    # 卡片边
HAIR = "#e3e2c2"     # 细线
PILL = "#f0e8c8"     # 导读胶囊底（Honey 稀释）
BASIL_TEXT = "#6f7d3a"  # 柠檬罗勒加深（文字可读性）

SANS = ("font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',"
        "'Hiragino Sans GB','Microsoft YaHei','Segoe UI',sans-serif;")
SERIF = "font-family:Georgia,'Times New Roman','Songti SC','STSong','SimSun',serif;"

# 报头五色样条（色卡顺序）
PALETTE_STRIP = [CARAMEL, GREEN, BASIL, HONEY, PALE]

# 版块 -> (色块色, 文字色)；文字色按可读性可深于色块
SECTIONS = {
    "国际": {"swatch": GREEN, "accent": GREEN, "label": "国际要闻"},
    "国内": {"swatch": CARAMEL, "accent": CARAMEL, "label": "国内动态"},
    "科技人物": {"swatch": BASIL, "accent": BASIL_TEXT, "label": "科技 · 大佬动向"},
}
SECTION_DEFAULT = {"swatch": BASIL, "accent": BASIL_TEXT, "label": None}


def _esc(text):
    return html.escape(str(text), quote=True)


def build_subject(now):
    return f"每日要闻速览 | {now.month}月{now.day}日 {WEEKDAYS[now.weekday()]}"


def _vol(now):
    return max(1, (now.date() - LAUNCH).days + 1)


def build_html(now, intro, sections, tip=""):
    date_line = f"{now.year}年{now.month}月{now.day}日 · {WEEKDAYS[now.weekday()]} · 第{_vol(now)}期"
    first_title = next((s["items"][0]["title"] for s in sections if s["items"]), "")
    preheader = _esc(f"{intro or first_title}")[:90]

    parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        '<meta name="supported-color-schemes" content="light"></head>',
        '<body style="margin:0;padding:0;background:' + PAGE + ';">',
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{PAGE};"><tr><td align="center" style="padding:24px 12px;">',
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="width:600px;max-width:100%;background:{CARD};border:1px solid {FRAME};'
        f'border-radius:8px;overflow:hidden;{SANS}">',
        _masthead(now, date_line),
        _intro_pill(intro),
    ]
    for sec in sections:
        parts.append(_section(sec))
    parts.append(_footer(now, tip))
    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


def _masthead(now, date_line):
    """色卡式刊名：小标 + 衬线加宽字距刊名 + 日期，下挂五色样条与细线。"""
    swatches = "".join(
        f'<td style="width:22px;height:22px;background:{c};border-radius:3px;'
        'font-size:0;line-height:22px;">&nbsp;</td><td style="width:5px;"></td>'
        for c in PALETTE_STRIP
    )
    return (
        '<tr><td style="padding:36px 36px 0;text-align:center;">'
        f'<div style="font-size:10px;letter-spacing:4px;color:{BASIL_TEXT};font-weight:600;">'
        'DAY60s · DAILY BRIEFING</div>'
        f'<div style="{SERIF}font-size:26px;font-weight:700;color:{INK};'
        'letter-spacing:10px;text-indent:10px;margin-top:14px;">每日要闻速览</div>'
        f'<div style="font-size:12px;color:{MUTED};margin-top:12px;letter-spacing:1px;">'
        f'{_esc(date_line)}</div>'
        "</td></tr>"
        '<tr><td style="padding:20px 36px 0;text-align:center;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" align="center">'
        f"<tr>{swatches}</tr></table>"
        "</td></tr>"
        f'<tr><td style="padding:20px 36px 0;"><div style="border-top:1px solid {HAIR};"></div></td></tr>'
    )


def _intro_pill(intro):
    """导读：蜜奶油圆角胶囊（呼应色卡的胶囊色条）。"""
    if not intro:
        return ""
    return (
        '<tr><td style="padding:20px 36px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="background:{PILL};border-radius:999px;padding:10px 20px;'
        'font-size:13.5px;line-height:1.8;color:' + BODY + ';">'
        f'<span style="{SERIF}font-weight:700;color:{CARAMEL};">导读</span>'
        '<span style="color:#b5ac7d;margin:0 8px;">/</span>'
        f'{_esc(intro)}'
        "</td></tr></table></td></tr>"
    )


def _section(sec):
    style = SECTIONS.get(sec["name"], {**SECTION_DEFAULT, "label": sec["name"]})
    accent = style["accent"]
    count = len(sec["items"])

    header = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-bottom:10px;border-bottom:1px solid {HAIR};">'
        f'<span style="display:inline-block;width:11px;height:11px;background:{style["swatch"]};'
        'border-radius:2px;vertical-align:middle;"></span>'
        f'<span style="{SERIF}font-size:17px;font-weight:700;color:{accent};'
        'margin-left:9px;vertical-align:middle;letter-spacing:1px;">'
        + _esc(style["label"] or sec["name"]) + "</span></td>"
        f'<td align="right" style="padding-bottom:10px;border-bottom:1px solid {HAIR};'
        f'font-size:11px;color:{MUTED};vertical-align:baseline;">{count} 条</td>'
        "</tr></table>"
    )
    return (
        '<tr><td style="padding:28px 36px 0;">'
        + header
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;">'
        + "".join(
            _item(idx, item, accent, last=(idx == count))
            for idx, item in enumerate(sec["items"], 1)
        )
        + "</table></td></tr>"
    )


def _item(idx, item, accent, last=False):
    summary = item.get("summary") or ""
    if item.get("url"):
        title_html = (
            f'<a href="{_esc(item["url"])}" '
            f'style="color:{INK};text-decoration:none;">' + _esc(item["title"]) + "</a>"
        )
    else:
        title_html = _esc(item["title"])

    meta_bits = [_esc(item["source"])]
    if item.get("published"):
        meta_bits.append(f'{item["published"]:%H:%M}')
    meta = " · ".join(meta_bits)

    divider = (
        'style="padding:13px 0 14px;"' if last
        else 'style="padding:13px 0 14px;border-bottom:1px solid ' + HAIR + ';"'
    )

    body = (
        f'<div style="font-size:15px;font-weight:700;line-height:1.55;color:{INK};">{title_html}</div>'
        + (
            f'<div style="font-size:13px;color:{BODY};line-height:1.8;margin-top:4px;">{_esc(summary)}</div>'
            if summary else ""
        )
        + f'<div style="font-size:11px;color:{MUTED};margin-top:6px;">{meta}</div>'
    )
    return (
        f'<tr><td {divider}>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td width="34" style="vertical-align:top;padding-top:2px;{SERIF}'
        f'font-size:14px;font-weight:700;color:{accent};">{idx:02d}</td>'
        f'<td style="vertical-align:top;">{body}</td>'
        "</tr></table></td></tr>"
    )


def _footer(now, tip):
    blocks = ['<tr><td style="padding:26px 36px 28px;">']
    if tip:
        blocks.append(
            f'<div style="font-size:12px;color:{MUTED};line-height:1.8;margin-bottom:14px;">'
            f'{_esc(tip)}</div>'
        )
    blocks.append(
        f'<div style="border-top:1px solid {HAIR};padding-top:12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="font-size:11px;color:{MUTED};">由 day60sMsg 自动生成 · '
        + _esc(now.strftime("%Y-%m-%d %H:%M")) + "</td>"
        f'<td align="right" style="font-size:11px;color:{MUTED};">第 {_vol(now)} 期</td>'
        "</tr></table></div></td></tr>"
    )
    return "".join(blocks)


def build_plain(intro, sections):
    lines = []
    if intro:
        lines.append(f"【导读】{intro}\n")
    for sec in sections:
        lines.append(f"■ {sec['name']}")
        for idx, item in enumerate(sec["items"], 1):
            line = f"{idx}. {item['title']}"
            if item.get("summary"):
                line += f"——{item['summary']}"
            lines.append(line)
        lines.append("")
    lines.append("由 day60sMsg 自动生成")
    return "\n".join(lines)
