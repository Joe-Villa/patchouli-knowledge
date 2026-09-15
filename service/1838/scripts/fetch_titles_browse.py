#!/usr/bin/env python3
"""用工坊浏览页批量抓双语 title（每页 30 条），写入 workshop_i18n.sqlite。

description 仍用详情库单语快照（中英相同）。只要 title 双语时用本脚本，
比详情页逐条快一个数量级（~355 页 × 2 语）。

用法:
  python3 fetch_titles_browse.py
  python3 fetch_titles_browse.py --lang schinese   # 只补一侧
  python3 fetch_titles_browse.py --pages 3         # 试跑
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETAILS = ROOT / "核心模块/data/workshop_details/workshop_details_all.sqlite"
DEFAULT_OUT = ROOT / "核心模块/data/workshop_details/workshop_i18n.sqlite"
DEFAULT_BRIEF = ROOT / "核心模块/data/workshop_brief/vic3_mods_all.sqlite"
APPID = 529340
SORT = "totaluniquesubscribers"
NUM_PER_PAGE = 30
PROXY_CANDIDATES = (
    "http://127.0.0.1:7891",
    "http://127.0.0.1:7892",
    "http://127.0.0.1:7893",
    "http://127.0.0.1:26561",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _proxy_ok(proxy: str, timeout: float = 5.0) -> bool:
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        req = urllib.request.Request(
            "https://steamcommunity.com/", headers={"User-Agent": UA}
        )
        with opener.open(req, timeout=timeout) as r:
            r.read(128)
        return True
    except Exception:
        return False


def pick_proxy(explicit: str | None) -> str:
    if explicit:
        if not _proxy_ok(explicit):
            raise SystemExit(f"代理不可用: {explicit}")
        return explicit
    for p in PROXY_CANDIDATES:
        if _proxy_ok(p):
            return p
    raise SystemExit("无可用代理（7891/7892/7893/26561）")


def build_opener(proxy: str) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def _extract_js_json_string(html: str, marker: str) -> str:
    i = html.find(marker)
    if i < 0:
        raise ValueError(f"marker not found: {marker}")
    start = i + len(marker) + 1
    chars: list[str] = []
    k = start
    while k < len(html):
        c = html[k]
        if c == "\\":
            chars.append(html[k + 1])
            k += 2
            continue
        if c == '"':
            break
        chars.append(c)
        k += 1
    return "".join(chars)


def parse_browse_page(html: str) -> dict:
    marker = "window.SSR.renderContext=JSON.parse("
    if html.find(marker) < 0:
        marker = "window.SSR.renderContext = JSON.parse("
    render_context = json.loads(_extract_js_json_string(html, marker))
    query_data = json.loads(render_context["queryData"])
    for query in query_data.get("queries", []):
        qk = query.get("queryKey")
        if isinstance(qk, list) and qk and qk[0] == "workshop_browse":
            data = query.get("state", {}).get("data")
            if data:
                return data
    raise ValueError("workshop_browse data missing")


def fetch_browse(
    opener: urllib.request.OpenerDirector,
    page: int,
    lang: str,
    timeout: float,
) -> dict:
    url = (
        f"https://steamcommunity.com/workshop/browse/"
        f"?appid={APPID}&browsesort={SORT}&section=readytouseitems"
        f"&actualsort={SORT}&p={page}&numperpage={NUM_PER_PAGE}&l={lang}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": lang})
    last: Exception | None = None
    for attempt in range(4):
        try:
            with opener.open(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            return parse_browse_page(html)
        except Exception as e:
            last = e
            time.sleep(min(2.0 * (attempt + 1), 10.0))
    raise RuntimeError(f"page {page} lang={lang}: {last}")


def collect_titles(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in data.get("results") or data.get("publishedfiledetails") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("publishedfileid") or row.get("id") or "")
        title = str(row.get("title") or "").strip()
        if mid and title:
            out[mid] = title
    return out


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS i18n (
          id TEXT PRIMARY KEY,
          title_en TEXT NOT NULL,
          title_zh TEXT NOT NULL,
          description_en TEXT NOT NULL,
          description_zh TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'mirror'
        );
        """
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--gap", type=float, default=0.8)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--pages", type=int, default=0, help="限制页数（试跑）")
    ap.add_argument(
        "--lang",
        choices=("both", "english", "schinese"),
        default="both",
    )
    args = ap.parse_args()

    if not args.details.is_file():
        raise SystemExit(f"missing details: {args.details}")

    proxy = pick_proxy(args.proxy.strip() or None)
    opener = build_opener(proxy)
    print(f"proxy={proxy} lang={args.lang}", flush=True)

    langs = ["english", "schinese"] if args.lang == "both" else [args.lang]
    title_maps: dict[str, dict[str, str]] = {lang: {} for lang in langs}

    # 首页拿 total_pages
    first_lang = langs[0]
    first = fetch_browse(opener, 1, first_lang, args.timeout)
    total_pages = int(first.get("total_pages") or 0)
    total_count = int(first.get("total_count") or 0)
    if args.pages > 0:
        total_pages = min(total_pages, args.pages)
    print(f"browse total_count={total_count} pages={total_pages}", flush=True)
    title_maps[first_lang].update(collect_titles(first))
    time.sleep(args.gap)

    for lang in langs:
        start = 2 if lang == first_lang else 1
        for page in range(start, total_pages + 1):
            data = fetch_browse(opener, page, lang, args.timeout)
            got = collect_titles(data)
            title_maps[lang].update(got)
            print(
                f"[{lang}] page {page}/{total_pages} +{len(got)} "
                f"cum={len(title_maps[lang])}",
                flush=True,
            )
            time.sleep(args.gap)

    # 详情库 description
    src = sqlite3.connect(args.details)
    details = {
        str(mid): (str(title or ""), str(desc or ""))
        for mid, title, desc in src.execute(
            "SELECT id, COALESCE(title,''), COALESCE(description,'') FROM details"
        )
    }
    src.close()

    en_map = title_maps.get("english") or {}
    zh_map = title_maps.get("schinese") or {}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(args.out)
    ensure_schema(dst)

    # 保留已有行的 description；title 用浏览结果覆盖
    existing_desc = {
        r[0]: (r[1], r[2])
        for r in dst.execute(
            "SELECT id, description_en, description_zh FROM i18n"
        ).fetchall()
    }

    all_ids = set(details) | set(en_map) | set(zh_map)
    upserted = 0
    split_n = 0
    for mid in sorted(all_ids, key=lambda x: int(x) if x.isdigit() else 0):
        raw_title, raw_desc = details.get(mid, ("", ""))
        te = en_map.get(mid) or raw_title
        tz = zh_map.get(mid) or raw_title
        if not te and tz:
            te = tz
        if not tz and te:
            tz = te
        if not te and not tz:
            continue
        if existing_desc.get(mid):
            de, dz = existing_desc[mid]
            # 若仍是空，用详情
            de = de or raw_desc
            dz = dz or raw_desc
        else:
            de = dz = raw_desc
        # title-only 策略：简介保持单语相同
        if not de and not dz:
            de = dz = raw_desc
        else:
            # 统一成同一份（优先详情 BBCode）
            d = raw_desc or de or dz
            de = dz = d
        dst.execute(
            """
            INSERT INTO i18n (id, title_en, title_zh, description_en, description_zh, source)
            VALUES (?, ?, ?, ?, ?, 'browse_titles')
            ON CONFLICT(id) DO UPDATE SET
              title_en=excluded.title_en,
              title_zh=excluded.title_zh,
              description_en=excluded.description_en,
              description_zh=excluded.description_zh,
              source='browse_titles'
            """,
            (mid, te, tz, de, dz),
        )
        upserted += 1
        if te != tz:
            split_n += 1
    dst.commit()
    total = dst.execute("SELECT COUNT(*) FROM i18n").fetchone()[0]
    dst.close()

    summary = {
        "upserted": upserted,
        "title_split": split_n,
        "en_titles": len(en_map),
        "zh_titles": len(zh_map),
        "i18n_total": total,
        "pages": total_pages,
        "out": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)

    # 同步进 1837/1838 共用主库
    try:
        from merge_titles_into_details import merge_one

        merged = merge_one(args.details, args.out, DEFAULT_BRIEF if DEFAULT_BRIEF.is_file() else None)
        print(json.dumps({"merged_into_details": merged}, ensure_ascii=False), flush=True)
    except Exception as e:
        print(f"[warn] merge into details failed: {e}", flush=True)


if __name__ == "__main__":
    main()
