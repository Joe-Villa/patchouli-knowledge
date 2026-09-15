"""问句剥壳 / 引号抽取：供 rewrite 兜底与 Fit 稀有子串加成共用。"""

from __future__ import annotations

import re

# 「…」 “…” '…' "…"
_QUOTE_RE = re.compile(
    r"「([^」]{1,40})」|"
    r"“([^”]{1,40})”|"
    r"'([^']{1,40})'|"
    r'"([^"]{1,40})"'
)

# 口语壳：有没有 / 就像…那样 / 模组 等（不删专名）
_SHELL_RES = [
    re.compile(r"有没有"),
    re.compile(r"有啥"),
    re.compile(r"请问"),
    re.compile(r"能不能"),
    re.compile(r"可以给我"),
    re.compile(r"给我看看"),
    re.compile(r"推荐一下?"),
    re.compile(r"推两?三?个"),
    re.compile(r"先说几个"),
    re.compile(r"之类的"),
    re.compile(r"那种"),
    re.compile(r"一下"),
    re.compile(r"模组"),
    re.compile(r"创意工坊"),
    re.compile(r"维多利亚\s*3|Vic3|V3", re.I),
    re.compile(r"就像[^，。？！；]*那样"),
    re.compile(r"好像[^，。？！；]*一样"),
]

_PUNCT_RE = re.compile(r"[，。？！、；：…·\s]+")
_WS = re.compile(r"\s+")


def extract_quoted_phrases(text: str) -> list[str]:
    """抽取引号内短语（去重、保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _QUOTE_RE.finditer(text or ""):
        phrase = next((g for g in m.groups() if g), "")
        phrase = (phrase or "").strip()
        if len(phrase) < 2 or phrase in seen:
            continue
        seen.add(phrase)
        out.append(phrase)
    return out


def strip_query_shell(text: str) -> str:
    """去掉口语壳与标点，保留内容词；引号内容先取出再拼回。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    quotes = extract_quoted_phrases(raw)
    s = raw
    for qr in _QUOTE_RE.finditer(raw):
        s = s.replace(qr.group(0), " ")
    for pat in _SHELL_RES:
        s = pat.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    parts = [p for p in s.split(" ") if p]
    for q in quotes:
        wrapped = f"「{q}」"
        if q not in " ".join(parts) and wrapped not in parts:
            parts.append(wrapped)
    # 若剥完太空，至少留下引号 / 原句截断
    if not parts:
        if quotes:
            return " ".join(f"「{q}」" for q in quotes)
        return _WS.sub(" ", raw)[:40].strip()
    return " ".join(parts)


def fallback_rewrite(text: str) -> str:
    """LLM 失败或未配置时的规则改写：剥壳 + 保留引号专名。"""
    return strip_query_shell(text)


def enrich_rewrite(original: str, rewritten: str) -> str:
    """LLM 改写后补回原句引号专名（若缺失）。"""
    base = (rewritten or "").strip() or fallback_rewrite(original)
    extras = []
    for q in extract_quoted_phrases(original):
        if q in base or f"「{q}」" in base:
            continue
        extras.append(f"「{q}」")
    if not extras:
        return base
    return _WS.sub(" ", f"{base} {' '.join(extras)}").strip()
