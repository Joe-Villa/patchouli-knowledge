#!/usr/bin/env python3
"""扫描 Vic3 game 下 N=13 目录的后缀名分布（递归）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import setup  # noqa: E402

setup()

N_FOLDERS = [
    "common",
    "dlc",
    "dlc_metadata",
    "events",
    "fonts",
    "gfx",
    "gui",
    "interface",
    "localization",
    "map_data",
    "music",
    "notifications",
    "sound",
]

NO_EXT = "(无后缀)"


def default_game_dir() -> Path:
    env = os.environ.get("VIC3_GAME_DIR", "").strip()
    if env:
        return Path(env) / "game"

    home = Path.home()
    candidates = [
        home / ".steam/steam/steamapps/common/Victoria 3/game",
        home / ".steam/debian-installation/steamapps/common/Victoria 3/game",
        home / ".local/share/Steam/steamapps/common/Victoria 3/game",
        home
        / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/common/Victoria 3/game",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def file_suffix_key(name: str) -> str:
    """取后缀键：无后缀归为 NO_EXT；多段后缀只取最后一段（.dds / .yml）。"""
    if name.startswith(".") and name.count(".") == 1:
        # 形如 .gitignore：整名当作无后缀类别之外的特殊名，仍按“有点但无扩展名”处理
        # 这里统一：最后一个点后为空 → 无后缀；否则取 .xxx
        return NO_EXT
    if "." not in name:
        return NO_EXT
    ext = name.rsplit(".", 1)[-1]
    if not ext or ext == name:
        return NO_EXT
    return f".{ext.lower()}"


def scan_folder(folder: Path) -> dict:
    counts: Counter[str] = Counter()
    total = 0
    for root, _dirs, files in os.walk(folder, followlinks=False):
        for fname in files:
            key = file_suffix_key(fname)
            counts[key] += 1
            total += 1

    by_ext = []
    for key, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        ratio = (count / total) if total else 0.0
        by_ext.append(
            {
                "suffix": key,
                "count": count,
                "ratio": round(ratio, 6),
                "ratio_pct": round(ratio * 100, 4),
            }
        )
    return {
        "folder": folder.name,
        "path": str(folder),
        "total_files": total,
        "suffix_kinds": len(by_ext),
        "by_suffix": by_ext,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="统计 N=13 目录后缀分布")
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=None,
        help="Vic3 的 game 目录；默认自动探测或 VIC3_GAME_DIR/game",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "extension_stats.json",
        help="输出 JSON 路径",
    )
    args = parser.parse_args()
    game_dir = args.game_dir or default_game_dir()
    if not game_dir.is_dir():
        raise SystemExit(f"game 目录不存在: {game_dir}")

    folders = []
    missing = []
    for name in N_FOLDERS:
        p = game_dir / name
        if not p.is_dir():
            missing.append(name)
            folders.append(
                {
                    "folder": name,
                    "path": str(p),
                    "total_files": 0,
                    "suffix_kinds": 0,
                    "by_suffix": [],
                    "missing": True,
                }
            )
        else:
            folders.append(scan_folder(p))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game_dir": str(game_dir),
        "n": len(N_FOLDERS),
        "folders_defined": N_FOLDERS,
        "missing_folders": missing,
        "no_ext_label": NO_EXT,
        "folders": folders,
        "grand_total_files": sum(f["total_files"] for f in folders),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output}")
    print(f"game_dir={game_dir}")
    print(f"grand_total_files={report['grand_total_files']}")
    if missing:
        print(f"missing: {missing}")
    for f in folders:
        print(f"  {f['folder']}: files={f['total_files']} kinds={f['suffix_kinds']}")


if __name__ == "__main__":
    main()
