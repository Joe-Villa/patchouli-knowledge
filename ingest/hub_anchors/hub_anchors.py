#!/usr/bin/env python3
"""从 map_data/state_regions 抽取枢纽锚点：STATE + slot → 省份色号。"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import data_dir, setup  # noqa: E402

setup()

from map_data_registry import SEAS_FILENAME, prepare_content, soft_remove  # noqa: E402

HUB_SLOTS = ("city", "port", "mine", "farm", "wood")
STATE_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(STATE_[A-Za-z0-9_]+)[ \t]*=[ \t]*\{"
)
# 在块内：city = "xRRGGBB"
SLOT_RE = re.compile(
    r"(?m)^[ \t]*(" + "|".join(HUB_SLOTS) + r')[ \t]*=[ \t]*"([xX][0-9A-Fa-f]+)"'
)


def _depths_before(masked: str) -> list[int]:
    depths = [0] * len(masked)
    depth = 0
    for i, ch in enumerate(masked):
        depths[i] = depth
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depths


def find_block_end(masked: str, start_brace: int) -> int:
    depth = 0
    for j in range(start_brace, len(masked)):
        if masked[j] == "{":
            depth += 1
        elif masked[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(masked) - 1


@dataclass
class HubAnchorsResult:
    entries: list[tuple[str, str, str, str, str]]  # state_key, slot, province, kind, file
    generated_at: str


def build_hub_anchors(map_data_dir: Path) -> HubAnchorsResult:
    regions = map_data_dir / "state_regions"
    if not regions.is_dir():
        raise FileNotFoundError(regions)

    entries: list[tuple[str, str, str, str, str]] = []
    for path in sorted(regions.glob("*.txt")):
        fname = path.name
        kind = "sea" if fname == SEAS_FILENAME else "land"
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        masked = prepare_content(raw)
        depths = _depths_before(masked)
        for m in STATE_HEADER_RE.finditer(masked):
            if depths[m.start()] != 0:
                continue
            state_key = m.group(1)
            brace = m.end() - 1
            end = find_block_end(masked, brace)
            # 用原文切片匹配引号内容（masked 中字符串被空格化）
            block_raw = raw[m.start() : end + 1]
            for sm in SLOT_RE.finditer(block_raw):
                slot, province = sm.group(1), sm.group(2)
                entries.append((state_key, slot, province, kind, fname))

    return HubAnchorsResult(
        entries=entries,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def write_hub_anchors(
    result: HubAnchorsResult,
    db_path: Path,
    summary_path: Path | None = None,
) -> None:
    soft_remove(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE anchors (
              state_key TEXT NOT NULL,
              slot TEXT NOT NULL,
              province TEXT NOT NULL,
              kind TEXT NOT NULL,
              file TEXT NOT NULL,
              PRIMARY KEY (state_key, slot)
            );
            CREATE INDEX idx_anchors_province ON anchors(province);
            CREATE INDEX idx_anchors_slot ON anchors(slot);
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO anchors (state_key, slot, province, kind, file)
            VALUES (?, ?, ?, ?, ?)
            """,
            result.entries,
        )
        conn.executemany(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            [
                ("generated_at", result.generated_at),
                ("schema", "patchouli.hub_anchors.v1"),
                ("entry_count", str(len(result.entries))),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    if summary_path:
        soft_remove(summary_path)
        by_slot: dict[str, int] = {}
        for _sk, slot, _p, _k, _f in result.entries:
            by_slot[slot] = by_slot.get(slot, 0) + 1
        summary_path.write_text(
            json.dumps(
                {
                    "schema": "patchouli.hub_anchors.v1",
                    "generated_at": result.generated_at,
                    "db": db_path.name,
                    "counts": {
                        "entries": len(result.entries),
                        "by_slot": by_slot,
                    },
                    "notes": [
                        "state_key+slot → province 色号（地图锚点）",
                        "显示名仍走 HUB_NAME_{state_key}_{slot} localization",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    game = data_dir() / "game"
    parser.add_argument(
        "--map-data",
        type=Path,
        default=game / "map_data",
    )
    parser.add_argument(
        "--out-db",
        type=Path,
        default=game / "hub_anchors.sqlite",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=game / "hub_anchors_summary.json",
    )
    args = parser.parse_args()
    result = build_hub_anchors(args.map_data)
    write_hub_anchors(result, args.out_db, args.out_summary)
    print(f"hub_anchors={len(result.entries)} db={args.out_db}")


if __name__ == "__main__":
    main()
