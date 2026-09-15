#!/usr/bin/env python3
"""把双语 title 合并进 workshop_details_*.sqlite（1837/1838 共用主库）。

从 workshop_i18n.sqlite 写入 details.title_en / details.title_zh。
缺侧用另一侧或原 title 填满。可选同步 brief.author → details.author。

用法:
  python3 merge_titles_into_details.py
  python3 merge_titles_into_details.py --also-ge300
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETAILS = ROOT / "核心模块/data/workshop_details/workshop_details_all.sqlite"
GE300_DETAILS = ROOT / "核心模块/data/workshop_details/workshop_details_ge300.sqlite"
DEFAULT_I18N = ROOT / "核心模块/data/workshop_details/workshop_i18n.sqlite"
DEFAULT_BRIEF = ROOT / "核心模块/data/workshop_brief/vic3_mods_all.sqlite"


def _ensure_columns(con: sqlite3.Connection, cols: dict[str, str]) -> list[str]:
    present = {r[1] for r in con.execute("PRAGMA table_info(details)")}
    added = []
    for name, decl in cols.items():
        if name not in present:
            con.execute(f"ALTER TABLE details ADD COLUMN {name} {decl}")
            added.append(name)
    return added


def merge_one(details: Path, i18n: Path, brief: Path | None) -> dict:
    if not details.is_file():
        raise FileNotFoundError(details)
    if not i18n.is_file():
        raise FileNotFoundError(i18n)

    i18n_con = sqlite3.connect(f"file:{i18n}?mode=ro", uri=True)
    titles = {
        str(mid): (str(te or ""), str(tz or ""))
        for mid, te, tz in i18n_con.execute(
            "SELECT id, title_en, title_zh FROM i18n"
        )
    }
    i18n_con.close()

    authors: dict[str, str] = {}
    if brief and brief.is_file():
        b = sqlite3.connect(f"file:{brief}?mode=ro", uri=True)
        cols = {r[1] for r in b.execute("PRAGMA table_info(mods)")}
        if "id" in cols and "author" in cols:
            authors = {
                str(mid): str(name)
                for mid, name in b.execute(
                    "SELECT id, author FROM mods WHERE author IS NOT NULL AND author != ''"
                )
            }
        b.close()

    con = sqlite3.connect(details)
    added = _ensure_columns(
        con,
        {
            "title_en": "TEXT",
            "title_zh": "TEXT",
            "author": "TEXT",
        },
    )
    rows = con.execute("SELECT id, COALESCE(title,'') FROM details").fetchall()
    updated = 0
    split = 0
    for mid, title in rows:
        mid = str(mid)
        te, tz = titles.get(mid, ("", ""))
        te = (te or "").strip()
        tz = (tz or "").strip()
        raw = (title or "").strip()
        if not te:
            te = tz or raw
        if not tz:
            tz = te or raw
        author = authors.get(mid, "")
        if authors:
            con.execute(
                "UPDATE details SET title_en=?, title_zh=?, author=? WHERE id=?",
                (te, tz, author, mid),
            )
        else:
            con.execute(
                "UPDATE details SET title_en=?, title_zh=? WHERE id=?",
                (te, tz, mid),
            )
        updated += 1
        if te != tz:
            split += 1
    con.commit()
    # 统计
    n = con.execute("SELECT COUNT(*) FROM details").fetchone()[0]
    with_en = con.execute(
        "SELECT COUNT(*) FROM details WHERE title_en IS NOT NULL AND title_en != ''"
    ).fetchone()[0]
    with_author = 0
    if "author" in {r[1] for r in con.execute("PRAGMA table_info(details)")}:
        with_author = con.execute(
            "SELECT COUNT(*) FROM details WHERE author IS NOT NULL AND author != ''"
        ).fetchone()[0]
    con.close()
    return {
        "details": str(details),
        "added_columns": added,
        "rows": n,
        "updated": updated,
        "title_split": split,
        "with_title_en": with_en,
        "with_author": with_author,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    ap.add_argument("--i18n", type=Path, default=DEFAULT_I18N)
    ap.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    ap.add_argument("--also-ge300", action="store_true")
    ap.add_argument("--no-author", action="store_true")
    args = ap.parse_args()
    brief = None if args.no_author else args.brief
    print(merge_one(args.details, args.i18n, brief))
    if args.also_ge300 and GE300_DETAILS.is_file():
        print(merge_one(GE300_DETAILS, args.i18n, brief))


if __name__ == "__main__":
    main()
