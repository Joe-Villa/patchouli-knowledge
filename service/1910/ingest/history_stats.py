"""直接解析 Interwar history pops/buildings（地图编辑器对嵌套 POPS 块会漏读）。"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ingest.paradox import extract_block, read_text, strip_comments

_STATE = re.compile(r"s:(STATE_\w+)\s*=\s*\{")
_REGION = re.compile(r"region_state:([A-Z0-9]{3})\s*=\s*\{")
_CREATE_POP = re.compile(r"create_pop\s*=\s*\{")
_CREATE_BLD = re.compile(r"create_building\s*=\s*\{")
_SIZE = re.compile(r"size\s*=\s*(\d+)")
_LEVELS = re.compile(r"levels\s*=\s*(\d+)")


def _history_files(mod: Path, folder: str) -> list[Path]:
    d = mod / "common" / "history" / folder
    if not d.is_dir():
        return []
    # 优先 iw_*.txt；空 stub（仅 BOM）跳过
    files = []
    for path in sorted(d.glob("*.txt")):
        if path.stat().st_size < 16:
            continue
        files.append(path)
    return files


def sum_population_by_tag(mod: Path) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for path in _history_files(mod, "pops"):
        text = strip_comments(read_text(path))
        for sm in _STATE.finditer(text):
            state_block = extract_block(text, sm.end() - 1)
            for rm in _REGION.finditer(state_block):
                tag = rm.group(1)
                region = extract_block(state_block, rm.end() - 1)
                for pm in _CREATE_POP.finditer(region):
                    pop = extract_block(region, pm.end() - 1)
                    m = _SIZE.search(pop)
                    if m:
                        totals[tag] += int(m.group(1))
    return dict(totals)


def sum_building_levels_by_tag(mod: Path) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for path in _history_files(mod, "buildings"):
        text = strip_comments(read_text(path))
        for sm in _STATE.finditer(text):
            state_block = extract_block(text, sm.end() - 1)
            for rm in _REGION.finditer(state_block):
                tag = rm.group(1)
                region = extract_block(state_block, rm.end() - 1)
                for bm in _CREATE_BLD.finditer(region):
                    bld = extract_block(region, bm.end() - 1)
                    for lv in _LEVELS.finditer(bld):
                        totals[tag] += int(lv.group(1))
    return dict(totals)
