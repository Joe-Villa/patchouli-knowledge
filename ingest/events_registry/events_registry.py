#!/usr/bin/env python3
"""为 events 建立权威注册表：仅 namespace.number。

语义（与 common 注册表不同）：
- 凡符合 ``namespace.number``（number 为十进制数字；namespace 可含点）的顶层事件块入表
- 未入表的事件 id **视为不存在**（形式固定，注册表即全集）
- 非标准顶层块记入 rejected，不进入 entries

字段：key（完整 id）, namespace, number, file（相对 events/ 的 posix 路径，可含子目录）
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import data_dir, setup  # noqa: E402

setup()

# 完整 id：最后一段为纯数字
EVENT_ID_RE = re.compile(
    r"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?"
    r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)[ \t]*=[ \t]*\{"
)
NS_DECL_RE = re.compile(r"(?m)^[ \t]*namespace[ \t]*=[ \t]*([^\s#]+)")
# 顶层疑似块头（用于找出非 ns.number 的杂质）
ANY_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?"
    r"([A-Za-z_][A-Za-z0-9_:]*)[ \t]*=[ \t]*\{"
)
VALID_EVENT_KEY_RE = re.compile(r"^(.+)\.(\d+)$")


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


def parse_event_key(key: str) -> tuple[str, str] | None:
    m = VALID_EVENT_KEY_RE.match(key)
    if not m:
        return None
    return m.group(1), m.group(2)


@dataclass
class EventsBuildResult:
    entries: list[tuple[str, str, str, str]]  # key, namespace, number, file
    rejected: list[dict]
    files: list[dict]
    generated_at: str
    namespaces_declared: list[str] = field(default_factory=list)


def build_events_registry(events_dir: Path) -> EventsBuildResult:
    if not events_dir.is_dir():
        raise FileNotFoundError(f"events 不存在: {events_dir}")

    entries: list[tuple[str, str, str, str]] = []
    rejected: list[dict] = []
    files_meta: list[dict] = []
    ns_declared: set[str] = set()

    txt_files = sorted(events_dir.rglob("*.txt"))
    for path in txt_files:
        rel = path.relative_to(events_dir).as_posix()
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            files_meta.append(
                {
                    "file": rel,
                    "status": "read_error",
                    "entry_count": 0,
                    "rejected_count": 0,
                }
            )
            rejected.append({"file": rel, "key": "", "reason": "read_error"})
            continue

        masked = prepare_content(raw)
        depths = _depths_before(masked)
        for ns in NS_DECL_RE.findall(masked):
            ns_declared.add(ns.strip())

        file_entries = 0
        file_rejected = 0
        seen_offsets: set[int] = set()

        for m in EVENT_ID_RE.finditer(masked):
            if depths[m.start()] != 0:
                continue
            seen_offsets.add(m.start())
            full = m.group(2)
            parsed = parse_event_key(full)
            if parsed is None:
                file_rejected += 1
                rejected.append(
                    {
                        "file": rel,
                        "key": full,
                        "reason": "not_namespace_number",
                    }
                )
                continue
            namespace, number = parsed
            entries.append((full, namespace, number, rel))
            file_entries += 1

        # 顶层非点分 / 非事件形态块
        for m in ANY_BLOCK_RE.finditer(masked):
            if depths[m.start()] != 0:
                continue
            if m.start() in seen_offsets:
                continue
            key = m.group(2)
            if key == "namespace":
                continue
            # 点分但已被 EVENT_ID_RE 覆盖的不会到这里；无点顶层块
            file_rejected += 1
            rejected.append(
                {
                    "file": rel,
                    "key": key,
                    "reason": "top_level_non_event_block",
                }
            )

        status = "ok"
        if file_entries == 0 and file_rejected == 0:
            status = "empty"
        elif file_entries == 0 and file_rejected > 0:
            status = "no_valid_events"
        elif file_rejected > 0:
            status = "ok_with_rejects"

        files_meta.append(
            {
                "file": rel,
                "status": status,
                "entry_count": file_entries,
                "rejected_count": file_rejected,
            }
        )

    return EventsBuildResult(
        entries=entries,
        rejected=rejected,
        files=files_meta,
        generated_at=datetime.now(timezone.utc).isoformat(),
        namespaces_declared=sorted(ns_declared),
    )


def write_events_registry(
    result: EventsBuildResult,
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
              namespace TEXT NOT NULL,
              number TEXT NOT NULL,
              file TEXT NOT NULL
            );
            CREATE INDEX idx_events_key ON entries(key);
            CREATE INDEX idx_events_namespace ON entries(namespace);
            CREATE INDEX idx_events_file ON entries(file);

            CREATE TABLE rejected (
              file TEXT NOT NULL,
              key TEXT NOT NULL,
              reason TEXT NOT NULL
            );

            CREATE TABLE files (
              file TEXT PRIMARY KEY,
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
            "INSERT INTO entries (key, namespace, number, file) VALUES (?, ?, ?, ?)",
            result.entries,
        )
        conn.executemany(
            "INSERT INTO rejected (file, key, reason) VALUES (?, ?, ?)",
            [(r["file"], r["key"], r["reason"]) for r in result.rejected],
        )
        conn.executemany(
            """
            INSERT INTO files (file, status, entry_count, rejected_count)
            VALUES (?, ?, ?, ?)
            """,
            [
                (f["file"], f["status"], f["entry_count"], f["rejected_count"])
                for f in result.files
            ],
        )
        meta = {
            "generated_at": result.generated_at,
            "schema": "patchouli.events_registry.v1",
            "entry_count": str(len(result.entries)),
            "rejected_count": str(len(result.rejected)),
            "file_count": str(len(result.files)),
            "completeness": "authoritative",
            "completeness_note": (
                "Only namespace.number events are indexed; "
                "ids absent from entries are treated as nonexistent"
            ),
        }
        conn.executemany("INSERT INTO meta (k, v) VALUES (?, ?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()

    if summary_path is not None:
        soft_remove(summary_path)
        empty_files = [f["file"] for f in result.files if f["status"] == "empty"]
        payload = {
            "schema": "patchouli.events_registry.v1",
            "generated_at": result.generated_at,
            "db": db_path.name,
            "completeness": "authoritative",
            "notes": [
                "entries 仅含 namespace.number；未出现在 entries 的事件视为不存在",
                "与 common_registry 不同：common 未收录只是无快索引，原文仍可能有定义",
                "file 为相对 events/ 的路径（允许子目录）",
                "namespace 可含点（如 1648_ai.events.1）",
            ],
            "counts": {
                "entries": len(result.entries),
                "rejected": len(result.rejected),
                "files": len(result.files),
                "empty_files": len(empty_files),
                "namespaces_declared": len(result.namespaces_declared),
            },
            "empty_files": empty_files,
            "rejected_sample": result.rejected[:20],
            "namespaces_declared": result.namespaces_declared,
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 events namespace.number 注册表")
    game = data_dir() / "game"
    parser.add_argument(
        "--events",
        type=Path,
        default=game / "events",
    )
    parser.add_argument(
        "--out-db",
        type=Path,
        default=game / "events_registry.sqlite",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=game / "events_registry_summary.json",
    )
    args = parser.parse_args()
    result = build_events_registry(args.events)
    write_events_registry(result, args.out_db, args.out_summary)
    print(f"events={args.events}")
    print(f"db={args.out_db}")
    print(
        f"entries={len(result.entries)} rejected={len(result.rejected)} "
        f"files={len(result.files)} namespaces={len(result.namespaces_declared)}"
    )


if __name__ == "__main__":
    main()
