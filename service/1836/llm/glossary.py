"""从证据文本抽出脚本 key，批量查 localization，做成作答用词表。

职责边界：
- 「找」侧给出证据块（可附带主实体 loc）；不必穷举块内所有引用的 loc。
- 「答」侧入口做本确定性补全：抽 key → 查库 → 词表，再交给 LLM。
  不在答侧开工具环让模型自己决定查什么。
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
if str(_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_DATA_DIR))

from localization_bridge import (  # noqa: E402
    DEFAULT_DB,
    connect,
    expand_value,
    lookup_exact,
    strip_script_scope,
)
try:
    from qq_text import strip_vic3_markup  # noqa: E402
except ImportError:  # package import
    from .qq_text import strip_vic3_markup  # noqa: E402

# Clausewitz 标识符；含 law_type:law_xxx / ig:xxx 等
_TOKEN_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9_]*:)?([A-Za-z_][A-Za-z0-9_.]*)"
)

# 态度的人话（游戏 IDEOLOGY_LAW_STANCE_* 是带占位符的句子模板，不作显示名）
_STANCE_PLAIN = {
    "strongly_approve": "坚决赞成",
    "approve": "赞成",
    "neutral": "不在意",
    "disapprove": "反对",
    "strongly_disapprove": "坚决反对",
}

# 抽 key 时跳过的纯语法/常见非实体词
_STOP = frozenset(
    {
        "yes",
        "no",
        "not",
        "or",
        "and",
        "if",
        "else",
        "limit",
        "add",
        "multiply",
        "subtract",
        "value",
        "desc",
        "icon",
        "exists",
        "this",
        "owner",
        "root",
        "prev",
        "from",
        "rgb",
        "hsv",
        "hsv360",
        "color",
        "trigger",
        "effect",
        "modifier",
        "save_scope_as",
        "set_variable",
        "has_variable",
        "character_ideology",
        "country_trigger",
        "interest_group_leader_trigger",
        "non_interest_group_leader_trigger",
        "interest_group_leader_weight",
        "on_enable",
        "on_disable",
        "possible",
        "visible",
        "ai_will_do",
        "nor",
        "nand",
        "always",
        "never",
    }
)

_PREFIX_HINT = (
    "law_",
    "lawgroup_",
    "ideology_",
    "concept_",
    "building_",
    "pm_",
    "pmg_",
    "tech_",
    "movement_",
    "ig_",
    "je_",
    "modifier_",
    "STATE_",
    "HUB_NAME_",
)


def extract_script_keys(text: str) -> list[str]:
    """从脚本原文抽候选 key（去重保序）。"""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        k = strip_script_scope(raw)
        if not k or k in seen or k.lower() in _STOP:
            return
        # 意识形态法律态度：无下划线也必须保留
        if k in _STANCE_PLAIN:
            seen.add(k)
            out.append(k)
            return
        # 过滤过短、纯数字、无下划线且不像 TAG 的
        if len(k) < 3:
            return
        if k.isdigit():
            return
        if "_" not in k and not k.isupper() and not any(
            k.startswith(p) for p in _PREFIX_HINT
        ):
            # 三字母 TAG 等全大写保留；其它无下划线小词丢掉
            if not (k.isupper() and 2 <= len(k) <= 4):
                return
        seen.add(k)
        out.append(k)

    for m in _TOKEN_RE.finditer(text):
        add(m.group(0))
        add(m.group(1))
    return out


def _resolve_label(conn: sqlite3.Connection, key: str, lang: str) -> str | None:
    """查显示名；态度词直接用人话（游戏 loc 是带占位符的句子模板）。"""
    if key in _STANCE_PLAIN:
        return _STANCE_PLAIN[key]

    row = lookup_exact(conn, key, lang)
    if not row:
        return None
    label = strip_vic3_markup(expand_value(conn, row["value"], lang))
    if not label:
        return None
    # 未展开干净的模板/concept 引用不当作显示名
    if "$" in label or "[concept_" in label:
        return None
    return label


def build_loc_glossary(
    evidence_texts: list[str],
    *,
    lang: str = "simp_chinese",
    db_path: Path | None = None,
    extra_keys: list[str] | None = None,
) -> dict[str, str]:
    """返回 {script_key: 人话}；仅含查到的项。"""
    keys: list[str] = []
    seen: set[str] = set()
    for t in evidence_texts:
        for k in extract_script_keys(t):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    for k in extra_keys or []:
        k = strip_script_scope(k)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    # 态度词即使未出现在抽词结果也无所谓；已在 extract 里会命中
    conn = connect(db_path or DEFAULT_DB)
    glossary: dict[str, str] = {}
    try:
        for k in keys:
            label = _resolve_label(conn, k, lang)
            if label:
                glossary[k] = label
            elif k in _STANCE_PLAIN:
                glossary[k] = _STANCE_PLAIN[k]
    finally:
        conn.close()
    return glossary


def format_glossary_for_prompt(glossary: dict[str, str]) -> str:
    if not glossary:
        return "（无）"
    lines = [f"{k} = {v}" for k, v in sorted(glossary.items(), key=lambda x: x[0])]
    return "\n".join(lines)
