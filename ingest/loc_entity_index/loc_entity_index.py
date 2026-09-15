#!/usr/bin/env python3
"""loc.key → 实体 反向索引（跳桥第二跳）。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import data_dir, setup  # noqa: E402

setup()

from localization_bridge import COMMON_SUFFIXES, EVENT_SUFFIXES, HUB_SLOTS  # noqa: E402

HUB_NAME_RE = re.compile(
    r"^HUB_NAME_(STATE_[A-Za-z0-9_]+)_(city|port|mine|farm|wood)(?:_([A-Za-z0-9_]+))?$"
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


def _add(
    rows: list[tuple[str, str, str, str]],
    loc_key: str,
    entity_kind: str,
    entity_key: str,
    relation: str,
) -> None:
    rows.append((loc_key, entity_kind, entity_key, relation))


def build_loc_entity_rows(
    *,
    common_db: Path,
    events_db: Path,
    map_db: Path,
    loc_db: Path | None = None,
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []

    if common_db.is_file():
        conn = sqlite3.connect(str(common_db))
        for key, type_name in conn.execute("SELECT DISTINCT key, type FROM entries"):
            _add(rows, key, type_name, key, "exact")
            for suf in COMMON_SUFFIXES:
                _add(rows, key + suf, type_name, key, suf.lstrip("_"))
        conn.close()

    if events_db.is_file():
        conn = sqlite3.connect(str(events_db))
        for (key,) in conn.execute("SELECT DISTINCT key FROM entries"):
            _add(rows, key, "events", key, "exact")
            for suf in EVENT_SUFFIXES:
                _add(rows, f"{key}.{suf}", "events", key, f"event_{suf}")
        conn.close()

    if map_db.is_file():
        conn = sqlite3.connect(str(map_db))
        for (key,) in conn.execute("SELECT key FROM entries"):
            _add(rows, key, "state", key, "exact")
            for slot in HUB_SLOTS:
                _add(
                    rows,
                    f"HUB_NAME_{key}_{slot}",
                    "state_hub",
                    f"{key}:{slot}",
                    f"hub_{slot}",
                )
        conn.close()

    if loc_db and loc_db.is_file():
        conn = sqlite3.connect(str(loc_db))
        for (loc_key,) in conn.execute(
            "SELECT DISTINCT key FROM localization WHERE key LIKE 'HUB_NAME_STATE_%'"
        ):
            m = HUB_NAME_RE.match(loc_key)
            if not m:
                continue
            state_key, slot, culture = m.group(1), m.group(2), m.group(3)
            rel = f"hub_{slot}" + (f"_{culture}" if culture else "")
            entity_key = f"{state_key}:{slot}" + (f":{culture}" if culture else "")
            _add(rows, loc_key, "state_hub", entity_key, rel)
        conn.close()

    seen: set[tuple[str, str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for r in rows:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def write_loc_entity_index(
    rows: list[tuple[str, str, str, str]],
    db_path: Path,
    summary_path: Path | None = None,
) -> None:
    soft_remove(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    by_kind: dict[str, int] = {}
    for _lk, kind, _ek, _rel in rows:
        by_kind[kind] = by_kind.get(kind, 0) + 1

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE loc_entity (
              loc_key TEXT NOT NULL,
              entity_kind TEXT NOT NULL,
              entity_key TEXT NOT NULL,
              relation TEXT NOT NULL
            );
            CREATE INDEX idx_le_loc ON loc_entity(loc_key);
            CREATE INDEX idx_le_entity ON loc_entity(entity_kind, entity_key);
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )
        conn.executemany(
            """
            INSERT INTO loc_entity (loc_key, entity_kind, entity_key, relation)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        conn.executemany(
            "INSERT INTO meta (k, v) VALUES (?, ?)",
            [
                ("generated_at", now),
                ("schema", "patchouli.loc_entity_index.v1"),
                ("entry_count", str(len(rows))),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    if summary_path:
        soft_remove(summary_path)
        summary_path.write_text(
            json.dumps(
                {
                    "schema": "patchouli.loc_entity_index.v1",
                    "generated_at": now,
                    "db": db_path.name,
                    "counts": {"entries": len(rows), "by_entity_kind": by_kind},
                    "notes": [
                        "人话 → localization → loc_key → 本表 → 实体",
                        "state_hub 的 entity_key 形如 STATE_X:city",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def main() -> None:
    root = data_dir() / "game"
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, default=root)
    parser.add_argument("--out-db", type=Path, default=root / "loc_entity_index.sqlite")
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=root / "loc_entity_index_summary.json",
    )
    args = parser.parse_args()
    rows = build_loc_entity_rows(
        common_db=args.game / "common_registry.sqlite",
        events_db=args.game / "events_registry.sqlite",
        map_db=args.game / "map_data_registry.sqlite",
        loc_db=args.game / "localization.sqlite",
    )
    write_loc_entity_index(rows, args.out_db, args.out_summary)
    print(f"loc_entity_index={len(rows)} db={args.out_db}")


if __name__ == "__main__":
    main()
