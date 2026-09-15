#!/usr/bin/env python3
"""为 common 下「扁平 key=value 罗列」子目录建立快索引注册表。

收录规则：
- 默认：common/<type>/<file>.txt（无嵌套）
- 特例白名单：technology/technologies、technology/eras（嵌套仍索引）
- 跳过：history / defines / genes / named_colors 等非扁平结构

表字段：key, file, type
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

# 已确认不是「仅扁平 key=value 罗列」的子目录（有外层 namespace/开局壳）
SKIP_FOLDERS = frozenset(
    {
        "history",
        "defines",
        "genes",
        "named_colors",
    }
)

# 顶层无直属 txt、但指定嵌套子目录仍按扁平实体索引：
# (一级目录, 二级目录) → type 名
NESTED_FLAT_ALLOWLIST: dict[tuple[str, str], str] = {
    ("technology", "technologies"): "technologies",
    ("technology", "eras"): "eras",
}

_KEY = r"[A-Za-z0-9_.:|+\-]+"
_BLOCK_HEADER_RE = re.compile(
    rf"(?m)^[ \t]*(?:(REPLACE_OR_CREATE|REPLACE|INJECT):)?({_KEY})[ \t]*=[ \t]*\{{"
)
_SCALAR_RE = re.compile(rf"(?m)^[ \t]*({_KEY})[ \t]*=[ \t]*([^{{\n#]+)$")


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
    """去掉 BOM；用空格遮罩字符串与 # 行注释，保留长度便于偏移。"""
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


def extract_top_level_keys(text: str) -> list[str]:
    """深度 0 的 key（block 与标量）；跳过 @宏。"""
    masked = prepare_content(text)
    depths = _depths_before(masked)
    keys: list[str] = []
    seen_spans: set[tuple[int, str]] = set()

    for m in _BLOCK_HEADER_RE.finditer(masked):
        if depths[m.start()] != 0:
            continue
        key = m.group(2)
        if key.startswith("@"):
            continue
        span = (m.start(), key)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        keys.append(key)

    for m in _SCALAR_RE.finditer(masked):
        if depths[m.start()] != 0:
            continue
        key = m.group(1)
        if key.startswith("@"):
            continue
        # 排除实际是 block 头的行
        line_end = masked.find("\n", m.start())
        snippet = masked[m.start() : line_end if line_end != -1 else len(masked)]
        if "{" in snippet:
            continue
        span = (m.start(), key)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        keys.append(key)

    return keys


@dataclass
class FolderReport:
    folder: str
    status: str  # registered | skipped
    reason: str
    direct_txt: int = 0
    nested_txt: int = 0
    non_txt_direct: int = 0
    entry_count: int = 0
    indexed_files: list[str] = field(default_factory=list)


@dataclass
class BuildResult:
    entries: list[tuple[str, str, str]]  # key, file, type
    folders: list[FolderReport]
    skipped_items: list[dict]
    generated_at: str


def build_common_registry(common_dir: Path) -> BuildResult:
    if not common_dir.is_dir():
        raise FileNotFoundError(f"common 不存在: {common_dir}")

    entries: list[tuple[str, str, str]] = []
    folders: list[FolderReport] = []
    skipped_items: list[dict] = []

    # common 根下文件
    for p in sorted(common_dir.iterdir()):
        if p.is_file():
            skipped_items.append(
                {
                    "path": p.relative_to(common_dir).as_posix(),
                    "reason": "not_in_subfolder",
                }
            )

    subdirs = sorted([p for p in common_dir.iterdir() if p.is_dir()])
    for sub in subdirs:
        name = sub.name
        direct_txt = sorted(sub.glob("*.txt"))
        nested_txt = sorted(p for p in sub.rglob("*.txt") if p.parent != sub)
        non_txt_direct = sorted(
            p for p in sub.iterdir() if p.is_file() and p.suffix.lower() != ".txt"
        )

        for p in nested_txt:
            skipped_items.append(
                {
                    "path": p.relative_to(common_dir).as_posix(),
                    "reason": "nested_path",
                }
            )
        for p in non_txt_direct:
            skipped_items.append(
                {
                    "path": p.relative_to(common_dir).as_posix(),
                    "reason": "not_txt",
                }
            )

        if name in SKIP_FOLDERS:
            folders.append(
                FolderReport(
                    folder=name,
                    status="skipped",
                    reason="non_flat_structure",
                    direct_txt=len(direct_txt),
                    nested_txt=len(nested_txt),
                    non_txt_direct=len(non_txt_direct),
                )
            )
            for p in direct_txt:
                skipped_items.append(
                    {
                        "path": p.relative_to(common_dir).as_posix(),
                        "reason": "folder_non_flat",
                    }
                )
            continue

        if not direct_txt:
            # 特例：technology/technologies、technology/eras 等嵌套扁平表
            nested_allow = [
                (child, NESTED_FLAT_ALLOWLIST[(name, child.name)])
                for child in sorted(sub.iterdir())
                if child.is_dir() and (name, child.name) in NESTED_FLAT_ALLOWLIST
            ]
            if nested_allow:
                total_entries = 0
                all_indexed: list[str] = []
                # 先从 skipped_items 去掉将被索引的嵌套路径
                indexed_nested_paths = set()
                for child, type_name in nested_allow:
                    for path in sorted(child.glob("*.txt")):
                        indexed_nested_paths.add(
                            path.relative_to(common_dir).as_posix()
                        )
                        try:
                            text = path.read_text(
                                encoding="utf-8-sig", errors="replace"
                            )
                        except OSError:
                            skipped_items.append(
                                {
                                    "path": path.relative_to(common_dir).as_posix(),
                                    "reason": "read_error",
                                }
                            )
                            continue
                        keys = extract_top_level_keys(text)
                        # file 用相对一级目录的路径，便于定位
                        rel_file = path.relative_to(sub).as_posix()
                        for key in keys:
                            entries.append((key, rel_file, type_name))
                            total_entries += 1
                        all_indexed.append(rel_file)
                skipped_items[:] = [
                    s
                    for s in skipped_items
                    if s.get("path") not in indexed_nested_paths
                ]
                folders.append(
                    FolderReport(
                        folder=name,
                        status="registered",
                        reason="nested_flat_allowlist",
                        direct_txt=0,
                        nested_txt=len(nested_txt),
                        non_txt_direct=len(non_txt_direct),
                        entry_count=total_entries,
                        indexed_files=all_indexed,
                    )
                )
                continue

            reason = "no_direct_txt"
            if nested_txt:
                reason = "nested_only"
            folders.append(
                FolderReport(
                    folder=name,
                    status="skipped",
                    reason=reason,
                    direct_txt=0,
                    nested_txt=len(nested_txt),
                    non_txt_direct=len(non_txt_direct),
                )
            )
            continue

        # 扁平目录：索引直属 txt
        indexed_files: list[str] = []
        entry_count = 0
        for path in direct_txt:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                skipped_items.append(
                    {
                        "path": path.relative_to(common_dir).as_posix(),
                        "reason": "read_error",
                    }
                )
                continue
            keys = extract_top_level_keys(text)
            fname = path.name
            for key in keys:
                entries.append((key, fname, name))
                entry_count += 1
            indexed_files.append(fname)

        folders.append(
            FolderReport(
                folder=name,
                status="registered",
                reason="flat_key_value",
                direct_txt=len(direct_txt),
                nested_txt=len(nested_txt),
                non_txt_direct=len(non_txt_direct),
                entry_count=entry_count,
                indexed_files=indexed_files,
            )
        )

    return BuildResult(
        entries=entries,
        folders=folders,
        skipped_items=skipped_items,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def write_registry(
    result: BuildResult,
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
              file TEXT NOT NULL,
              type TEXT NOT NULL
            );
            CREATE INDEX idx_entries_key ON entries(key);
            CREATE INDEX idx_entries_type ON entries(type);
            CREATE INDEX idx_entries_type_key ON entries(type, key);

            CREATE TABLE folders (
              folder TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              reason TEXT NOT NULL,
              direct_txt INTEGER NOT NULL,
              nested_txt INTEGER NOT NULL,
              non_txt_direct INTEGER NOT NULL,
              entry_count INTEGER NOT NULL
            );

            CREATE TABLE skipped_items (
              path TEXT NOT NULL,
              reason TEXT NOT NULL
            );

            CREATE TABLE meta (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO entries (key, file, type) VALUES (?, ?, ?)",
            result.entries,
        )
        conn.executemany(
            """
            INSERT INTO folders
              (folder, status, reason, direct_txt, nested_txt, non_txt_direct, entry_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f.folder,
                    f.status,
                    f.reason,
                    f.direct_txt,
                    f.nested_txt,
                    f.non_txt_direct,
                    f.entry_count,
                )
                for f in result.folders
            ],
        )
        conn.executemany(
            "INSERT INTO skipped_items (path, reason) VALUES (?, ?)",
            [(s["path"], s["reason"]) for s in result.skipped_items],
        )
        registered = [f.folder for f in result.folders if f.status == "registered"]
        skipped = [f.folder for f in result.folders if f.status == "skipped"]
        meta = {
            "generated_at": result.generated_at,
            "schema": "patchouli.common_registry.v1",
            "entry_count": str(len(result.entries)),
            "registered_folder_count": str(len(registered)),
            "skipped_folder_count": str(len(skipped)),
            "skip_folders_policy": ",".join(sorted(SKIP_FOLDERS)),
        }
        conn.executemany(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            list(meta.items()),
        )
        conn.commit()
    finally:
        conn.close()

    if summary_path is not None:
        soft_remove(summary_path)
        payload = {
            "schema": "patchouli.common_registry.v1",
            "generated_at": result.generated_at,
            "db": db_path.name,
            "counts": {
                "entries": len(result.entries),
                "registered_folders": len(registered),
                "skipped_folders": len(skipped),
                "skipped_items": len(result.skipped_items),
            },
            "registered_folders": sorted(registered),
            "skipped_folders": [
                {"folder": f.folder, "reason": f.reason, "nested_txt": f.nested_txt}
                for f in result.folders
                if f.status == "skipped"
            ],
            "skip_folders_policy": sorted(SKIP_FOLDERS),
            "notes": [
                "entries: key + file(basename) + type(folder)",
                "不代替原文；未注册仅意味无快索引",
                "仅 common/<type>/*.txt；嵌套/根文件/非txt 见 skipped_items",
            ],
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 common 扁平 key 注册表")
    game = data_dir() / "game"
    parser.add_argument(
        "--common",
        type=Path,
        default=game / "common",
        help="削减版或原版的 common 目录",
    )
    parser.add_argument(
        "--out-db",
        type=Path,
        default=game / "common_registry.sqlite",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=game / "common_registry_summary.json",
    )
    args = parser.parse_args()
    result = build_common_registry(args.common)
    write_registry(result, args.out_db, args.out_summary)
    reg = sum(1 for f in result.folders if f.status == "registered")
    skip = sum(1 for f in result.folders if f.status == "skipped")
    print(f"common={args.common}")
    print(f"db={args.out_db}")
    print(f"entries={len(result.entries)} registered_folders={reg} skipped_folders={skip}")
    print(f"skipped_items={len(result.skipped_items)}")
    print("skipped folders:")
    for f in result.folders:
        if f.status == "skipped":
            print(f"  {f.folder}: {f.reason} (direct={f.direct_txt} nested={f.nested_txt})")


if __name__ == "__main__":
    main()
