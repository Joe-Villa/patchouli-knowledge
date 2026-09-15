#!/usr/bin/env python3
"""补全双语 title/description → workshop_i18n.sqlite。

策略（默认 --mode extra）：
  详情库已有主语言快照（API）。再只抓「另一语」工坊页：
  - 主文偏中文 → 抓 l=english；否则抓 l=schinese
  - 与主文相同 → 单语，两边都用主文（保留 API 的 BBCode description）
  - 不同 → 主文归一侧，抓到的归另一侧

真双语对照也可 --mode both（每种语言都抓，更慢）。

代理：默认依次试 7891 / 26561 / 7890 / 7892 / 7893。

用法:
  python3 fetch_i18n.py --limit 30
  python3 fetch_i18n.py                 # 全量断点续跑
  python3 fetch_i18n.py --mode both --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DETAILS = ROOT / "核心模块/data/workshop_details/workshop_details_all.sqlite"
DEFAULT_OUT = ROOT / "核心模块/data/workshop_details/workshop_i18n.sqlite"
PROXY_CANDIDATES = (
    "http://127.0.0.1:7891",
    "http://127.0.0.1:26561",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7892",
    "http://127.0.0.1:7893",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TITLE_RE = re.compile(r'class="workshopItemTitle"[^>]*>(.*?)</div>', re.I | re.S)
_TITLE_RE2 = re.compile(r'workshopItemTitle[^>]*>(.*?)</div>', re.I | re.S)
_DESC_RE = re.compile(
    r'id="highlightContent"[^>]*>(.*?)</div>\s*<div class="clear',
    re.I | re.S,
)
_TITLE_META = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.I)
_DESC_META = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WS_RE = re.compile(r"\s+")


def _proxy_ok(proxy: str, timeout: float = 5.0) -> bool:
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        req = urllib.request.Request(
            "https://steamcommunity.com/",
            headers={"User-Agent": UA},
            method="GET",
        )
        with opener.open(req, timeout=timeout) as r:
            r.read(256)
        return True
    except Exception:
        return False


def pick_proxy(explicit: str | None) -> str:
    if explicit:
        if not _proxy_ok(explicit):
            raise SystemExit(f"指定代理不可用: {explicit}")
        return explicit
    for p in PROXY_CANDIDATES:
        if _proxy_ok(p):
            return p
    raise SystemExit(
        "无可用代理（试过 "
        + ", ".join(PROXY_CANDIDATES)
        + "）。工坊抓取需本机代理。"
    )


def _strip_html(s: str) -> str:
    s = (
        (s or "")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return _TAG_RE.sub("", s).strip()


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip()).casefold()


def looks_cjk(text: str) -> bool:
    if not text:
        return False
    cjk = len(_CJK_RE.findall(text))
    return cjk >= 2 or (cjk >= 1 and cjk / max(len(text), 1) > 0.08)


def parse_page(html: str) -> tuple[str, str]:
    if "workshopItemTitle" not in html and "og:title" not in html:
        raise ValueError("empty or soft-blocked page")
    title = ""
    m = _TITLE_RE.search(html) or _TITLE_RE2.search(html)
    if m:
        title = _strip_html(m.group(1))
    if not title:
        m = _TITLE_META.search(html)
        if m:
            title = _strip_html(m.group(1))
            for suf in (
                " - Steam Workshop",
                " :: Steam Community",
                "Steam Workshop::",
                "Steam 创意工坊::",
            ):
                if title.startswith(suf):
                    title = title[len(suf) :].strip()
                if title.endswith(suf):
                    title = title[: -len(suf)].strip()
            if "::" in title and title.split("::", 1)[0].strip() in (
                "Steam Workshop",
                "Steam 创意工坊",
            ):
                title = title.split("::", 1)[1].strip()
    desc = ""
    m = _DESC_RE.search(html)
    if m:
        raw = m.group(1)
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
        raw = re.sub(r"</p\s*>", "\n", raw, flags=re.I)
        desc = _strip_html(raw)
    if not desc:
        m = _DESC_META.search(html)
        if m:
            desc = _strip_html(m.group(1))
    return title, desc


def fetch_lang(
    opener: urllib.request.OpenerDirector,
    mod_id: str,
    lang: str,
    timeout: float,
    *,
    retries: int = 1,
    cool: float = 15.0,
) -> tuple[str, str]:
    url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}&l={lang}"
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Language": lang}
            )
            with opener.open(req, timeout=timeout) as r:
                html = r.read().decode("utf-8", errors="replace")
            return parse_page(html)
        except Exception as e:
            last_err = e
            if attempt + 1 < retries:
                time.sleep(cool * (attempt + 1))
    assert last_err is not None
    raise last_err


def build_opener(proxy: str) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def list_working_proxies(explicit: str | None) -> list[str]:
    if explicit:
        if not _proxy_ok(explicit):
            raise SystemExit(f"指定代理不可用: {explicit}")
        return [explicit]
    found = [p for p in PROXY_CANDIDATES if _proxy_ok(p)]
    if not found:
        raise SystemExit(
            "无可用代理（试过 "
            + ", ".join(PROXY_CANDIDATES)
            + "）。工坊抓取需本机代理。"
        )
    return found


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
        CREATE TABLE IF NOT EXISTS i18n_state (
          id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          error TEXT,
          updated_at REAL,
          mode TEXT
        );
        """
    )


def normalize_pair(a: str, b: str) -> tuple[str, str]:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a and b:
        a = b
    if not b and a:
        b = a
    return a, b


def same_text(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return True
    if not na or not nb:
        return False
    return na == nb


def resolve_extra(
    title0: str,
    desc0: str,
    fetched_lang: str,
    title_f: str,
    desc_f: str,
) -> tuple[str, str, str, str, str]:
    """返回 title_en, title_zh, desc_en, desc_zh, note。

    判定以标题为主：HTML 简介与 API BBCode 本来就不可比，不能用来判单语。
    标题相同 → 单语，两边都用 API 主文（保留 BBCode）。
    标题不同 → 双语，主文归未抓语种，抓取归 fetched_lang。
    """
    if same_text(title0, title_f) or (not title_f and title0):
        t, d = title0 or title_f, desc0 or desc_f
        return t, t, d, d, "mono"

    if fetched_lang == "english":
        title_en, title_zh = title_f, title0
        desc_en, desc_zh = (desc_f or desc0), desc0
    else:
        title_zh, title_en = title_f, title0
        desc_zh, desc_en = (desc_f or desc0), desc0

    title_en, title_zh = normalize_pair(title_en, title_zh)
    desc_en, desc_zh = normalize_pair(desc_en, desc_zh)
    return title_en, title_zh, desc_en, desc_zh, "split"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--proxy", default="", help="指定代理；默认自动探测")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument(
        "--mode",
        choices=("extra", "both"),
        default="extra",
        help="extra=只补另一语；both=中英都抓",
    )
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args()

    proxy_list = list_working_proxies(args.proxy.strip() or None)
    proxy_i = 0
    proxy = proxy_list[0]
    print(f"proxies={proxy_list} using={proxy} mode={args.mode}", flush=True)

    if not args.details.is_file():
        raise SystemExit(f"missing details: {args.details}")

    src = sqlite3.connect(args.details)
    rows = src.execute(
        "SELECT id, COALESCE(title,''), COALESCE(description,'') FROM details "
        "ORDER BY CAST(id AS INTEGER)"
    ).fetchall()
    src.close()
    rows = rows[args.offset :]
    if args.limit > 0:
        rows = rows[: args.limit]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.log or (ROOT / "模组卡片/logs" / f"fetch_i18n_{time.strftime('%Y%m%d_%H%M%S')}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    dst = sqlite3.connect(args.out)
    ensure_schema(dst)
    done: set[str] = set()
    if not args.fresh:
        done = {
            r[0]
            for r in dst.execute("SELECT id FROM i18n WHERE source='steam_dual'").fetchall()
        }

    opener = build_opener(proxy)

    ok = fail = skip = mono = split = 0
    with log_path.open("a", encoding="utf-8") as logf:
        logf.write(f"# start proxies={proxy_list} mode={args.mode} n={len(rows)}\n")
        for i, (mid, title0, desc0) in enumerate(rows, 1):
            if mid in done:
                skip += 1
                continue
            try:
                if args.mode == "both":
                    te, de = fetch_lang(opener, mid, "english", args.timeout)
                    time.sleep(args.gap)
                    tz, dz = fetch_lang(opener, mid, "schinese", args.timeout)
                    te, tz = normalize_pair(te, tz)
                    de, dz = normalize_pair(de, dz)
                    note = "mono" if same_text(te, tz) else "split"
                    if same_text(te, title0):
                        de = desc0 or de
                    if same_text(tz, title0):
                        dz = desc0 or dz
                else:
                    probe = f"{title0}\n{(desc0 or '')[:400]}"
                    fetched_lang = "english" if looks_cjk(probe) else "schinese"
                    tf, df = fetch_lang(opener, mid, fetched_lang, args.timeout)
                    te, tz, de, dz, note = resolve_extra(title0, desc0, fetched_lang, tf, df)

                if not te and not tz:
                    raise ValueError("empty titles")
                dst.execute(
                    """
                    INSERT INTO i18n (id, title_en, title_zh, description_en, description_zh, source)
                    VALUES (?, ?, ?, ?, ?, 'steam_dual')
                    ON CONFLICT(id) DO UPDATE SET
                      title_en=excluded.title_en,
                      title_zh=excluded.title_zh,
                      description_en=excluded.description_en,
                      description_zh=excluded.description_zh,
                      source='steam_dual'
                    """,
                    (mid, te, tz, de, dz),
                )
                dst.execute(
                    """
                    INSERT INTO i18n_state (id, status, error, updated_at, mode)
                    VALUES (?, 'ok', ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      status='ok', error=excluded.error,
                      updated_at=excluded.updated_at, mode=excluded.mode
                    """,
                    (mid, note, time.time(), args.mode),
                )
                dst.commit()
                ok += 1
                if note == "mono":
                    mono += 1
                else:
                    split += 1
                line = (
                    f"[{i}/{len(rows)}] ok {mid} {note} "
                    f"zh={tz[:40]!r} en={te[:40]!r}"
                )
                print(line, flush=True)
                logf.write(line + "\n")
                logf.flush()
            except Exception as e:
                fail += 1
                dst.execute(
                    """
                    INSERT INTO i18n_state (id, status, error, updated_at, mode)
                    VALUES (?, 'fail', ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      status='fail', error=excluded.error,
                      updated_at=excluded.updated_at, mode=excluded.mode
                    """,
                    (mid, str(e)[:500], time.time(), args.mode),
                )
                dst.commit()
                line = f"[{i}/{len(rows)}] FAIL {mid}: {e}"
                print(line, file=sys.stderr, flush=True)
                logf.write(line + "\n")
                logf.flush()
                proxy_i = (proxy_i + 1) % len(proxy_list)
                proxy = proxy_list[proxy_i]
                opener = build_opener(proxy)
                cool = max(args.gap * 8, 60.0)
                print(f"  cool {cool:.0f}s switch_proxy={proxy}", flush=True)
                logf.write(f"  cool {cool:.0f}s switch_proxy={proxy}\n")
                time.sleep(cool)
            time.sleep(args.gap)

    summary = {
        "ok": ok,
        "fail": fail,
        "skip": skip,
        "mono": mono,
        "split": split,
        "proxy": proxy,
        "proxies": proxy_list,
        "mode": args.mode,
        "out": str(args.out),
        "log": str(log_path),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    dst.close()


if __name__ == "__main__":
    main()
