#!/usr/bin/env python3
"""localization 跳桥：正反向查找、按需 $REF$ 展开、近似文案检索。

库内 value 为原样字符串；expand_* 仅在调用时展开，不写回 DB。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from localization_db import ensure_localization_fts

ASSOC_PATH = Path(__file__).resolve().parent / "localization_assoc.json"
DEFAULT_DB = Path(__file__).resolve().parent / "game" / "localization.sqlite"

DOLLAR_REF_RE = re.compile(r"\$([A-Za-z0-9_.]+)\$")
SCOPE_PREFIX_RE = re.compile(r"^(?:s|c|ig|p|g|d|f):")

EVENT_SUFFIXES = ("t", "d", "f", "a", "b", "c", "e", "g", "h", "i")
HUB_SLOTS = ("city", "port", "mine", "farm", "wood")
COMMON_SUFFIXES = ("_desc", "_name", "_tt", "_tooltip")


def load_assoc_rules(path: Path = ASSOC_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_usable_loc_db(db_path: Path | None) -> bool:
    """库文件存在且含 localization 表（排除缺文件 / 0 字节空壳）。"""
    if db_path is None:
        return False
    path = Path(db_path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False


def connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path)
    # 禁止对缺失路径 connect：sqlite 会建出 0 字节空库，随后炸 no such table
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"localization db missing or empty: {path}")
    # 卷只读时普通 connect 会失败；immutable 避免创建 -wal/-shm
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization'"
    ).fetchone()
    if has is None:
        conn.close()
        raise sqlite3.OperationalError(f"no such table: localization in {path}")
    return conn


def ensure_fts(conn: sqlite3.Connection, *, rebuild: bool = False) -> None:
    ensure_localization_fts(conn, rebuild=rebuild)
    conn.commit()

def strip_script_scope(key: str) -> str:
    return SCOPE_PREFIX_RE.sub("", key.strip())


def lookup_exact(
    conn: sqlite3.Connection, key: str, lang: str = "simp_chinese"
) -> dict | None:
    key = strip_script_scope(key)
    row = conn.execute(
        "SELECT key, lang, value, source_file FROM localization WHERE key=? AND lang=?",
        (key, lang),
    ).fetchone()
    return dict(row) if row else None


def expand_value(
    conn: sqlite3.Connection,
    value: str,
    lang: str = "simp_chinese",
    *,
    max_depth: int = 6,
    _stack: frozenset[str] | None = None,
) -> str:
    """递归展开 $OTHER_KEY$；缺 key 时保留原 token。"""
    if not value or max_depth <= 0:
        return value
    stack = _stack or frozenset()

    def repl(m: re.Match[str]) -> str:
        ref = m.group(1)
        if ref in stack:
            return m.group(0)
        row = lookup_exact(conn, ref, lang)
        if not row:
            return m.group(0)
        return expand_value(
            conn,
            row["value"],
            lang,
            max_depth=max_depth - 1,
            _stack=stack | {ref},
        )

    return DOLLAR_REF_RE.sub(repl, value)


def lookup_exact_expanded(
    conn: sqlite3.Connection, key: str, lang: str = "simp_chinese"
) -> dict | None:
    row = lookup_exact(conn, key, lang)
    if not row:
        return None
    out = dict(row)
    out["value_expanded"] = expand_value(conn, row["value"], lang)
    return out


def candidate_loc_keys(entity_kind: str, entity_key: str) -> list[str]:
    """按关联模板生成正向候选 loc keys（去重保序）。"""
    k = strip_script_scope(entity_key)
    out: list[str] = []

    def add(x: str) -> None:
        if x not in out:
            out.append(x)

    add(k)
    for suf in COMMON_SUFFIXES:
        add(k + suf)

    kind = entity_kind.lower()
    if kind in {"event", "events"}:
        for suf in EVENT_SUFFIXES:
            add(f"{k}.{suf}")
    elif kind in {"state", "states", "state_region"}:
        for slot in HUB_SLOTS:
            add(f"HUB_NAME_{k}_{slot}")
    elif kind in {"country", "countries", "tag"}:
        add(f"{k}_ADJ")
    elif kind in {"building", "buildings", "law", "laws", "concept"}:
        add(f"{k}_desc")

    return out


def forward_bridge(
    conn: sqlite3.Connection,
    entity_kind: str,
    entity_key: str,
    lang: str = "simp_chinese",
    *,
    expand: bool = True,
) -> list[dict]:
    """实体 → 命中的 loc 行（含展开文案）。"""
    hits: list[dict] = []
    for loc_key in candidate_loc_keys(entity_kind, entity_key):
        row = lookup_exact(conn, loc_key, lang)
        if not row:
            continue
        item = dict(row)
        item["assoc"] = "template_or_exact"
        if expand:
            item["value_expanded"] = expand_value(conn, row["value"], lang)
        hits.append(item)
    return hits


def reverse_exact(
    conn: sqlite3.Connection, text: str, lang: str = "simp_chinese"
) -> list[dict]:
    rows = conn.execute(
        "SELECT key, lang, value, source_file FROM localization WHERE lang=? AND value=?",
        (lang, text),
    ).fetchall()
    return [dict(r) for r in rows]


def reverse_fuzzy(
    conn: sqlite3.Connection,
    text: str,
    lang: str = "simp_chinese",
    *,
    limit: int = 20,
) -> list[dict]:
    """近似/子串跳桥入口。优先 FTS trigram；失败则 LIKE。"""
    text = text.strip()
    if not text:
        return []
    ensure_fts(conn)
    try:
        rows = conn.execute(
            """
            SELECT f.key, f.lang, f.value, l.source_file,
                   bm25(localization_fts) AS score
            FROM localization_fts AS f
            JOIN localization AS l ON l.key = f.key AND l.lang = f.lang
            WHERE f.lang = ? AND localization_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (lang, text, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        like = f"%{text}%"
        rows = conn.execute(
            """
            SELECT key, lang, value, source_file
            FROM localization
            WHERE lang=? AND value LIKE ?
            LIMIT ?
            """,
            (lang, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="localization 跳桥工具")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fwd = sub.add_parser("forward", help="实体 → loc")
    p_fwd.add_argument("kind")
    p_fwd.add_argument("key")
    p_fwd.add_argument("--lang", default="simp_chinese")

    p_rev = sub.add_parser("reverse", help="文案 → loc key（精确+近似）")
    p_rev.add_argument("text")
    p_rev.add_argument("--lang", default="simp_chinese")
    p_rev.add_argument("--limit", type=int, default=10)

    p_exp = sub.add_parser("expand", help="展开某 key 的 $REF$")
    p_exp.add_argument("key")
    p_exp.add_argument("--lang", default="simp_chinese")

    p_fts = sub.add_parser("build-fts", help="为已有 DB 建/重建 FTS")
    p_fts.add_argument("--rebuild", action="store_true")

    args = parser.parse_args()
    conn = connect(args.db)
    try:
        if args.cmd == "build-fts":
            ensure_fts(conn, rebuild=args.rebuild)
            n = conn.execute("SELECT count(*) FROM localization_fts").fetchone()[0]
            print(f"fts_rows={n}")
            return
        if args.cmd == "forward":
            for row in forward_bridge(conn, args.kind, args.key, args.lang):
                print(
                    f"{row['key']}\t{row.get('value_expanded') or row['value']}"
                )
            return
        if args.cmd == "reverse":
            exact = reverse_exact(conn, args.text, args.lang)
            print(f"## exact ({len(exact)})")
            for row in exact:
                print(f"{row['key']}\t{row['value']}")
            fuzzy = reverse_fuzzy(conn, args.text, args.lang, limit=args.limit)
            print(f"## fuzzy ({len(fuzzy)})")
            for row in fuzzy:
                print(f"{row['key']}\t{row['value']}")
            return
        if args.cmd == "expand":
            row = lookup_exact_expanded(conn, args.key, args.lang)
            if not row:
                raise SystemExit("key not found")
            print("raw:", row["value"])
            print("expanded:", row["value_expanded"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
