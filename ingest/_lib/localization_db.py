#!/usr/bin/env python3
"""将 Vic3 localization yml（指定语种）写入 SQLite。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# 入库语种（目录名 / 文件名中的 l_<lang>）
DEFAULT_LANGS = ("simp_chinese", "english")

ENTRY_RE = re.compile(
    r'^\s*([^\s:#]+):(\d*)\s+"(.*)"\s*$'
)
LANG_HEADER_RE = re.compile(r"^\s*l_([A-Za-z0-9_]+)\s*:\s*$")


@dataclass
class LocStats:
    files_parsed: int = 0
    files_skipped: int = 0
    entries_read: int = 0
    entries_stored: int = 0
    by_lang: dict[str, int] | None = None


def iter_loc_yml_files(loc_root: Path, langs: tuple[str, ...]) -> list[tuple[str, Path]]:
    """返回 (lang, path) 列表，按 path 排序（后者覆盖前者）。"""
    wanted = set(langs)
    out: list[tuple[str, Path]] = []
    if not loc_root.is_dir():
        return out

    for path in sorted(loc_root.rglob("*.yml")):
        name = path.name
        lang: str | None = None
        # 标准：*_l_<lang>.yml 或目录名为语种
        for cand in wanted:
            if name.endswith(f"_l_{cand}.yml") or name == f"l_{cand}.yml":
                lang = cand
                break
        if lang is None:
            # languages.yml 等：尝试从内容头识别，此处先跳过非语种文件名
            rel_parts = path.relative_to(loc_root).parts
            if rel_parts and rel_parts[0] in wanted and path.suffix == ".yml":
                lang = rel_parts[0]
        if lang is None:
            continue
        out.append((lang, path))
    return out


def parse_localization_yml(text: str, expected_lang: str | None = None) -> list[tuple[str, str]]:
    """解析单个 yml，返回 [(key, value), ...]。value 已去掉外层引号。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    entries: list[tuple[str, str]] = []
    file_lang: str | None = None
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        hm = LANG_HEADER_RE.match(line)
        if hm:
            file_lang = hm.group(1)
            continue
        m = ENTRY_RE.match(line)
        if not m:
            continue
        key, _ver, value = m.group(1), m.group(2), m.group(3)
        # 还原常见转义
        value = value.replace(r"\"", '"').replace(r"\\", "\\")
        entries.append((key, value))
    if expected_lang and file_lang and file_lang != expected_lang:
        # 仍保留条目，由调用方决定；此处不丢弃（部分文件头与目录一致即可）
        pass
    return entries


def ensure_localization_fts(conn: sqlite3.Connection, *, rebuild: bool = False) -> None:
    """FTS5 trigram：供人话近似跳桥。"""
    if rebuild:
        conn.execute("DROP TABLE IF EXISTS localization_fts")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='localization_fts'"
    ).fetchone()
    if row:
        return
    conn.execute(
        """
        CREATE VIRTUAL TABLE localization_fts USING fts5(
          key,
          lang,
          value,
          tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO localization_fts(key, lang, value)
        SELECT key, lang, value FROM localization
        """
    )


def build_localization_db(
    loc_root: Path,
    db_path: Path,
    *,
    langs: tuple[str, ...] = DEFAULT_LANGS,
) -> LocStats:
    """从原版 localization/ 构建 DB。source_file 为相对 localization/ 的 posix 路径。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        import os
        import shutil
        import subprocess

        if shutil.which("gio"):
            subprocess.run(["gio", "trash", str(db_path)], check=True)
        else:
            trash = Path.home() / ".local/share/Trash/files"
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / db_path.name
            if dest.exists():
                dest = trash / f"{db_path.name}_{os.getpid()}"
            shutil.move(str(db_path), str(dest))

    files = iter_loc_yml_files(loc_root, langs)
    # (key, lang) -> (value, source_file)；后写覆盖
    store: dict[tuple[str, str], tuple[str, str]] = {}
    stats = LocStats(by_lang={lang: 0 for lang in langs})

    for lang, path in files:
        rel = path.relative_to(loc_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            stats.files_skipped += 1
            continue
        parsed = parse_localization_yml(text, expected_lang=lang)
        stats.files_parsed += 1
        stats.entries_read += len(parsed)
        for key, value in parsed:
            store[(key, lang)] = (value, rel)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE localization (
              key TEXT NOT NULL,
              lang TEXT NOT NULL,
              value TEXT NOT NULL,
              source_file TEXT NOT NULL,
              PRIMARY KEY (key, lang)
            );
            CREATE INDEX idx_localization_lang ON localization(lang);
            CREATE INDEX idx_localization_source ON localization(source_file);
            CREATE TABLE meta (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );
            """
        )
        rows = [
            (key, lang, value, source_file)
            for (key, lang), (value, source_file) in store.items()
        ]
        conn.executemany(
            "INSERT INTO localization (key, lang, value, source_file) VALUES (?, ?, ?, ?)",
            rows,
        )
        for key, lang, _value, _sf in rows:
            assert stats.by_lang is not None
            stats.by_lang[lang] = stats.by_lang.get(lang, 0) + 1
        stats.entries_stored = len(rows)
        conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            ("langs", ",".join(langs)),
        )
        conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            ("source_root", str(loc_root)),
        )
        ensure_localization_fts(conn)
        conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            ("fts", "localization_fts/trigram"),
        )
        conn.commit()
    finally:
        conn.close()
    return stats


def resolve_loc_root(mod_dir: Path) -> Path | None:
    """HOI4 常用 localisation；Vic3 用 localization。"""
    for name in ("localisation", "localization"):
        cand = Path(mod_dir) / name
        if cand.is_dir():
            return cand
    return None


def merge_localization_into_db(
    db_path: Path,
    loc_root: Path,
    *,
    langs: tuple[str, ...] = ("simp_chinese",),
    source_prefix: str = "",
) -> LocStats:
    """把 loc_root 下指定语种 upsert 进已有 localization.sqlite，并重建 FTS。

    source_file = source_prefix + 相对 loc_root 的路径（若有 prefix，中间加 /）。
    后写覆盖同 (key, lang)。不删其它语种或其它来源条目。
    """
    db_path = Path(db_path)
    loc_root = Path(loc_root)
    if not db_path.is_file():
        raise FileNotFoundError(f"localization db missing: {db_path}")
    if not loc_root.is_dir():
        raise FileNotFoundError(f"loc root missing: {loc_root}")

    files = iter_loc_yml_files(loc_root, langs)
    store: dict[tuple[str, str], tuple[str, str]] = {}
    stats = LocStats(by_lang={lang: 0 for lang in langs})
    prefix = source_prefix.strip().strip("/")

    for lang, path in files:
        rel = path.relative_to(loc_root).as_posix()
        source_file = f"{prefix}/{rel}" if prefix else rel
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            stats.files_skipped += 1
            continue
        parsed = parse_localization_yml(text, expected_lang=lang)
        stats.files_parsed += 1
        stats.entries_read += len(parsed)
        for key, value in parsed:
            store[(key, lang)] = (value, source_file)

    rows = [
        (key, lang, value, source_file)
        for (key, lang), (value, source_file) in store.items()
    ]
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany(
            """
            INSERT INTO localization (key, lang, value, source_file)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key, lang) DO UPDATE SET
              value = excluded.value,
              source_file = excluded.source_file
            """,
            rows,
        )
        for _key, lang, _value, _sf in rows:
            assert stats.by_lang is not None
            stats.by_lang[lang] = stats.by_lang.get(lang, 0) + 1
        stats.entries_stored = len(rows)

        ensure_localization_fts(conn, rebuild=True)
        tag = prefix or str(loc_root)
        row = conn.execute(
            "SELECT v FROM meta WHERE k = ?", ("loc_packs_merged",)
        ).fetchone()
        prev: list[str] = []
        if row and row[0]:
            try:
                raw = json.loads(row[0])
                if isinstance(raw, list):
                    prev = [str(x) for x in raw]
                elif isinstance(raw, str) and raw:
                    prev = [raw]
            except json.JSONDecodeError:
                prev = [str(row[0])]
        if tag and tag not in prev:
            prev.append(tag)
        conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            ("loc_packs_merged", json.dumps(prev, ensure_ascii=False)),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return stats
