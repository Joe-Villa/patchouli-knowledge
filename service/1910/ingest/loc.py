"""Vic3 YAML 本地化：合并 vanilla+mod，剥颜色码。"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r'^\s*([A-Za-z0-9_.:-]+):\d*\s+"(.*)"\s*$')
_COLOR = re.compile(r"#[a-zA-Z][a-zA-Z0-9_]*|#!")
_LOC_KEY_LINE = re.compile(r"^\s*[A-Za-z0-9_.:-]+:")


def strip_loc(text: str) -> str:
    text = text.replace(r"\n", "\n").replace(r"\"", '"')
    text = _COLOR.sub("", text)
    text = re.sub(r"\[ROOT\.GetCountry\.GetName\]", "玩家国家", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def parse_yml(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in raw.splitlines():
        m = _KEY.match(line)
        if not m:
            continue
        out[m.group(1)] = strip_loc(m.group(2))
    return out


def count_keys(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    return sum(1 for line in raw.splitlines() if _LOC_KEY_LINE.match(line))


def load_locale(mod: Path, vanilla: Path, *, lang: str = "simp_chinese") -> dict[str, str]:
    merged: dict[str, str] = {}
    for root in (vanilla, mod):
        loc_dir = root / "localization" / lang
        if not loc_dir.is_dir():
            continue
        for path in sorted(loc_dir.rglob("*.yml")):
            merged.update(parse_yml(path))
    return merged


def lookup(loc: dict[str, str], *keys: str, default: str | None = None) -> str:
    for key in keys:
        if key and key in loc:
            return loc[key]
    if default is not None:
        return default
    return next((k for k in keys if k), "")
