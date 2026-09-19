"""轻量 Paradox 文本扫描（不引入 vic3kit）。"""

from __future__ import annotations

import re
from pathlib import Path

_COMMENT = re.compile(r"#.*$")
_C_TAG = re.compile(r"\bc:([A-Z0-9]{3})\b")
_OBJ = re.compile(
    r"^(objective_\w+)\s*=\s*\{",
    re.M,
)
_TAGS = re.compile(r"recommended_tags\s*=\s*\{([^}]*)\}")
_BUILDING = re.compile(r"^(building_[a-z0-9_]+)\s*=\s*\{", re.M)
_LAW = re.compile(r"activate_law\s*=\s*law_type:(law_\w+)")
# 直写科技 / 开局 tier effect / 整纪元科技，按出现顺序收集后展开
_TECH_TOKEN = re.compile(
    r"(?:"
    r"add_technology_researched\s*=\s*([A-Za-z0-9_-]+)"
    r"|(effect_starting_technology_tier_\d+_tech)\s*=\s*yes"
    r"|add_era_researched\s*=\s*(era_\d+)"
    r")"
)
_TECH = re.compile(r"add_technology_researched\s*=\s*([A-Za-z0-9_-]+)")
_COUNTRY_HEADER = re.compile(r"c:([A-Z0-9]{3})\s*\?*=\s*\{")
_START_DATE = re.compile(r'START_DATE\s*=\s*"([^"]+)"')
_STATE = re.compile(r"^(STATE_\w+)\s*=\s*\{", re.M)
_ARABLE = re.compile(r"arable_land\s*=\s*(\d+)")


def strip_comments(text: str) -> str:
    return "\n".join(_COMMENT.sub("", line) for line in text.splitlines())


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def extract_block(text: str, start_brace: int) -> str:
    depth = 0
    for i, ch in enumerate(text[start_brace:], start_brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_brace : i + 1]
    return text[start_brace:]


def iter_txt(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*.txt") if p.is_file()]
