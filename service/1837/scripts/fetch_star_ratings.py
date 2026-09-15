#!/usr/bin/env python3
"""从 Steam 工坊简略浏览页拉取 star_rating / total_votes。

复用 analysis_paradox/scrape_vic3 的抓取（代理 + SSR 解析）。
写出：核心模块/data/workshop_brief/star_ratings.json

用法:
  python3 模组推荐/scripts/fetch_star_ratings.py
  python3 模组推荐/scripts/fetch_star_ratings.py --pages 3
  python3 模组推荐/scripts/fetch_star_ratings.py --fresh
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_PK = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_PK / "核心模块" / "data" / "workshop_brief" / "star_ratings.json"
STATE_PATH = OUT_PATH.with_suffix(".state.json")
SCRAPER_ROOT = Path("/home/liulingda/桌面/我为社区的持久贡献/analysis_paradox")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=0, help="最多抓 N 页；0=全部")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.35)
    args = ap.parse_args()

    if not SCRAPER_ROOT.is_dir():
        print(f"scraper root missing: {SCRAPER_ROOT}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(SCRAPER_ROOT))
    from scrape_vic3 import build_opener, fetch_page  # type: ignore

    items: dict[str, dict] = {}
    last_page = 0
    total_pages: int | None = None
    if STATE_PATH.is_file() and not args.fresh:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        items = {str(k): v for k, v in (st.get("items") or {}).items()}
        last_page = int(st.get("last_page") or 0)
        total_pages = st.get("total_pages")
        print(f"resume last_page={last_page} items={len(items)}")

    if args.fresh:
        items = {}
        last_page = 0
        total_pages = None

    opener = build_opener()

    def ingest(data: dict) -> None:
        for row in data.get("results") or []:
            mid = str(row.get("publishedfileid") or "")
            if not mid:
                continue
            items[mid] = {
                "star_rating": int(row.get("star_rating") or -1),
                "total_votes": int(row.get("total_votes") or 0),
            }

    def checkpoint(page: int) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {"last_page": page, "total_pages": total_pages, "items": items},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    if total_pages is None:
        data0 = fetch_page(opener, 1)
        total_pages = int(data0.get("total_pages") or 1)
        ingest(data0)
        last_page = 1
        checkpoint(1)
        print(f"total_pages={total_pages} items={len(items)}")

    limit = int(args.pages) if args.pages and args.pages > 0 else int(total_pages or 1)
    start = last_page + 1
    for page in range(start, limit + 1):
        data = fetch_page(opener, page)
        ingest(data)
        last_page = page
        if page % 10 == 0 or page == limit:
            checkpoint(page)
            print(f"page {page}/{limit} items={len(items)}")
        time.sleep(max(0.0, args.sleep))

    rated = sum(1 for v in items.values() if int(v.get("star_rating") or -1) >= 1)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "workshop_browse.star_rating",
        "count": len(items),
        "rated_count": rated,
        "items": items,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH} count={len(items)} rated={rated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
