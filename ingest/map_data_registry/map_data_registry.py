#!/usr/bin/env python3
"""为 map_data/state_regions 建立 state 注册表。

仅索引 ``state_regions/*.txt`` 顶层 ``STATE_* = {`` 块。
陆地/海洋：文件名是否为 ``99_seas.txt``（是 → sea，否 → land）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import data_dir, setup  # noqa: E402

setup()

SEAS_FILENAME = "99_seas.txt"

STATE_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?"
    r"(STATE_[A-Za-z0-9_]+)[ \t]*=[ \t]*\{"
)
# 顶层其它块（非 STATE_）记入 rejected
OTHER_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?"
    r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*\{"
)


def soft_remove(path: Path) -> None:
    if not path.exists():
        return
    if shutil.which("gio"):
        subprocess.run(["gio", "trash", str(path)], check=True)
        return
    trash = Path.home() / ".local/share/Trash/files"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / path.name
    if dest.exists():
        dest = trash / f"{path.name}_{os.getpid()}"
    shutil.move(str(path), str(dest))


def prepare_content(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == '"':
            out.append('"')
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(" ")
                    i += 1
                    if i < n:
                        out.append(" ")
                        i += 1
                    continue
                if text[i] == '"':
                    out.append('"')
                    i += 1
                    break
                out.append(" ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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


@dataclass
class MapDataBuildResult:
    entries: list[tuple[str, str, str]]  # key, kind (land|sea), file
    rejected: list[dict]
    files: list[dict]
    generated_at: str


def build_map_data_registry(map_data_dir: Path) -> MapDataBuildResult:
    regions = map_data_dir / "state_regions"
    if not regions.is_dir():
        raise FileNotFoundError(f"state_regions 不存在: {regions}")

    entries: list[tuple[str, str, str]] = []
    rejected: list[dict] = []
    files_meta: list[dict] = []

    # 仅直属 *.txt；嵌套忽略
    for path in sorted(regions.glob("*.txt")):
        fname = path.name
        kind = "sea" if fname == SEAS_FILENAME else "land"
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            files_meta.append(
                {
                    "file": fname,
                    "kind": kind,
                    "status": "read_error",
                    "entry_count": 0,
                    "rejected_count": 0,
                }
            )
            rejected.append({"file": fname, "key": "", "reason": "read_error"})
            continue

        masked = prepare_content(raw)
        depths = _depths_before(masked)
        file_entries = 0
        file_rejected = 0
        seen: set[int] = set()

        for m in STATE_BLOCK_RE.finditer(masked):
            if depths[m.start()] != 0:
                continue
            seen.add(m.start())
            key = m.group(2)
            entries.append((key, kind, fname))
            file_entries += 1

        for m in OTHER_BLOCK_RE.finditer(masked):
            if depths[m.start()] != 0:
                continue
            if m.start() in seen:
                continue
            key = m.group(2)
            if key.startswith("STATE_"):
                continue
            file_rejected += 1
            rejected.append(
                {
                    "file": fname,
                    "key": key,
                    "reason": "non_state_top_block",
                }
            )

        status = "ok"
        if file_entries == 0:
            status = "empty" if file_rejected == 0 else "no_states"
        elif file_rejected:
            status = "ok_with_rejects"

        files_meta.append(
            {
                "file": fname,
                "kind": kind,
                "status": status,
                "entry_count": file_entries,
                "rejected_count": file_rejected,
            }
        )

    # 嵌套 txt / 非 txt 记一笔（不索引）
    for p in sorted(regions.rglob("*")):
        if not p.is_file():
            continue
        if p.parent != regions:
            rejected.append(
                {
                    "file": p.relative_to(regions).as_posix(),
                    "key": "",
                    "reason": "nested_ignored",
                }
            )
        elif p.suffix.lower() != ".txt":
            rejected.append(
                {
                    "file": p.name,
                    "key": "",
                    "reason": "not_txt_ignored",
                }
            )

    return MapDataBuildResult(
        entries=entries,
        rejected=rejected,
        files=files_meta,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def write_map_data_registry(
    result: MapDataBuildResult,
    db_path: Path,
    summary_path: Path | None = None,
) -> None:
    soft_remove(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE entries (
              key TEXT NOT NULL,
              kind TEXT NOT NULL,
              file TEXT NOT NULL
            );
            CREATE INDEX idx_map_key ON entries(key);
            CREATE INDEX idx_map_kind ON entries(kind);
            CREATE INDEX idx_map_file ON entries(file);

            CREATE TABLE rejected (
              file TEXT NOT NULL,
              key TEXT NOT NULL,
              reason TEXT NOT NULL
            );

            CREATE TABLE files (
              file TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              entry_count INTEGER NOT NULL,
              rejected_count INTEGER NOT NULL
            );

            CREATE TABLE meta (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO entries (key, kind, file) VALUES (?, ?, ?)",
            result.entries,
        )
        conn.executemany(
            "INSERT INTO rejected (file, key, reason) VALUES (?, ?, ?)",
            [(r["file"], r["key"], r["reason"]) for r in result.rejected],
        )
        conn.executemany(
            """
            INSERT INTO files (file, kind, status, entry_count, rejected_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    f["file"],
                    f["kind"],
                    f["status"],
                    f["entry_count"],
                    f["rejected_count"],
                )
                for f in result.files
            ],
        )
        land = sum(1 for _k, kind, _f in result.entries if kind == "land")
        sea = sum(1 for _k, kind, _f in result.entries if kind == "sea")
        meta = {
            "generated_at": result.generated_at,
            "schema": "patchouli.map_data_registry.v1",
            "entry_count": str(len(result.entries)),
            "land_count": str(land),
            "sea_count": str(sea),
            "seas_filename": SEAS_FILENAME,
            "kind_rule": f"file=={SEAS_FILENAME} => sea, else land",
            "scope": "state_regions/*.txt top-level STATE_* only",
        }
        conn.executemany("INSERT INTO meta (k, v) VALUES (?, ?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()

    if summary_path is not None:
        soft_remove(summary_path)
        land = sum(1 for _k, kind, _f in result.entries if kind == "land")
        sea = sum(1 for _k, kind, _f in result.entries if kind == "sea")
        payload = {
            "schema": "patchouli.map_data_registry.v1",
            "generated_at": result.generated_at,
            "db": db_path.name,
            "notes": [
                "只登记 state_regions 直属 txt 的顶层 STATE_*",
                f"kind=sea 当且仅当 file=={SEAS_FILENAME}，否则 land",
                "不索引省份/邻接等其它 map_data",
            ],
            "counts": {
                "entries": len(result.entries),
                "land": land,
                "sea": sea,
                "files": len(result.files),
                "rejected": len(result.rejected),
            },
            "files": result.files,
            "rejected": result.rejected,
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 map_data state 注册表")
    game = data_dir() / "game"
    parser.add_argument(
        "--map-data",
        type=Path,
        default=game / "map_data",
    )
    parser.add_argument(
        "--out-db",
        type=Path,
        default=game / "map_data_registry.sqlite",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=game / "map_data_registry_summary.json",
    )
    args = parser.parse_args()
    result = build_map_data_registry(args.map_data)
    write_map_data_registry(result, args.out_db, args.out_summary)
    land = sum(1 for _k, kind, _f in result.entries if kind == "land")
    sea = sum(1 for _k, kind, _f in result.entries if kind == "sea")
    print(f"map_data={args.map_data}")
    print(f"db={args.out_db}")
    print(
        f"entries={len(result.entries)} land={land} sea={sea} "
        f"files={len(result.files)} rejected={len(result.rejected)}"
    )


if __name__ == "__main__":
    main()
