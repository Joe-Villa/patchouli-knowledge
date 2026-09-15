"""QQ 场景纯文本清洗：去掉 Markdown / Vic3 富文本残留。"""

from __future__ import annotations

import re

# Vic3 loc 色/样式标记、图标
_VIC3_MARK = re.compile(r"#(!|[A-Za-z0-9_]+)|@[^!\s\n]+!")
# Markdown 常见强调/标题/代码（答侧约束；输出后再兜底剥一次）
_MD_EMPHASIS = re.compile(r"(?<!\w)(\*{1,3}|_{1,3})(.+?)\1(?!\w)")
_MD_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MD_CODE_FENCE = re.compile(r"```[\s\S]*?```|`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BULLET_STAR = re.compile(r"(?m)^\s*\*\s+")


def strip_vic3_markup(text: str) -> str:
    if not text:
        return ""
    return _VIC3_MARK.sub("", text).strip()


def sanitize_qq_text(text: str) -> str:
    """去掉 QQ 里难看/无效的 Markdown 与多余星号列表。"""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _MD_CODE_FENCE.sub(lambda m: m.group(1) or "", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_EMPHASIS.sub(r"\2", t)
    t = _BULLET_STAR.sub("· ", t)
    # 残留成对 ** /
    t = t.replace("**", "").replace("__", "")
    # 压缩过多空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
