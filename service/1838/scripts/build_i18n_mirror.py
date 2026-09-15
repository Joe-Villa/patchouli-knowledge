#!/usr/bin/env python3
"""从详情库生成双语 title/description 侧车库（先镜像；真双语靠 fetch_i18n 重抓）。

用法:
  python3 build_i18n_mirror.py
  python3 build_i18n_mirror.py --details .../workshop_details_all.sqlite --out .../workshop_i18n.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETAILS = ROOT / "核心模块/data/workshop_details/workshop_details_all.sqlite"
DEFAULT_OUT = ROOT / "核心模块/data/workshop_details/workshop_i18n.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS i18n (
  id TEXT PRIMARY KEY,
  title_en TEXT NOT NULL,
  title_zh TEXT NOT NULL,
  description_en TEXT NOT NULL,
  description_zh TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'mirror'
);
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    if not args.details.is_file():
        raise SystemExit(f"missing details db: {args.details}")

    src = sqlite3.connect(args.details)
    rows = src.execute("SELECT id, COALESCE(title,''), COALESCE(description,'') FROM details").fetchall()
    src.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        # 保留已由 fetch_i18n 写入的 steam_dual 行
        dst = sqlite3.connect(args.out)
        dst.executescript(SCHEMA)
        existing = {
            r[0]: r[1]
            for r in dst.execute("SELECT id, source FROM i18n").fetchall()
        }
    else:
        dst = sqlite3.connect(args.out)
        dst.executescript(SCHEMA)
        existing = {}

    upserted = 0
    skipped = 0
    for mid, title, desc in rows:
        if existing.get(mid) == "steam_dual":
            skipped += 1
            continue
        dst.execute(
            """
            INSERT INTO i18n (id, title_en, title_zh, description_en, description_zh, source)
            VALUES (?, ?, ?, ?, ?, 'mirror')
            ON CONFLICT(id) DO UPDATE SET
              title_en=excluded.title_en,
              title_zh=excluded.title_zh,
              description_en=excluded.description_en,
              description_zh=excluded.description_zh,
              source='mirror'
            WHERE i18n.source != 'steam_dual'
            """,
            (str(mid), title, title, desc, desc),
        )
        upserted += 1
    dst.commit()
    total = dst.execute("SELECT COUNT(*) FROM i18n").fetchone()[0]
    dual = dst.execute("SELECT COUNT(*) FROM i18n WHERE source='steam_dual'").fetchone()[0]
    dst.close()
    print(f"out={args.out} total={total} upserted_mirror={upserted} kept_steam_dual={skipped} dual_now={dual}")


if __name__ == "__main__":
    main()
