#!/usr/bin/env python3
"""1837 工坊社区 Agent 烟测：工具层 + surprise；有密钥时再打 /api/ask。"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.tools import ToolContext, make_dispatcher
from scoring.catalog import DEFAULT_DB, load_ge300_details, resolve_brief_path
from scoring.fit_tfidf import FitTfidf
from scoring.surprise import build_surprise_shortlist, weighted_sample


def _http_json(url: str, payload: dict | None = None, timeout: float = 180.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def smoke_tools() -> None:
    db = Path(os.environ.get("WORKSHOP_DB") or DEFAULT_DB)
    assert db.is_file(), f"missing db {db}"
    brief = resolve_brief_path(os.environ.get("WORKSHOP_BRIEF_DB"))
    mods = load_ge300_details(db, brief_path=brief)
    fit = FitTfidf(mods)
    ctx = ToolContext(db_path=db, mods=mods, fit_model=fit)
    d = make_dispatcher(ctx)

    print("== run_code: by天草 count ==")
    r = d(
        "run_code",
        {
            "code": (
                "cur = con.execute("
                "\"SELECT COUNT(*) FROM details WHERE author LIKE '%天草%' "
                "OR author LIKE '%因幡%' OR author LIKE '%美咕噜%'\")"
                "\nprint(cur.fetchone()[0])"
            )
        },
    )
    print(r.get("stdout") or r)
    assert r.get("ok"), r

    print("== run_code: subscriptions >1000 by天草 ==")
    r = d(
        "run_code",
        {
            "code": (
                "cur = con.execute("
                "\"SELECT COUNT(*) FROM details WHERE subscriptions > 1000 "
                "AND (author LIKE '%天草%' OR author LIKE '%因幡%' OR author LIKE '%美咕噜%')\")"
                "\nprint(cur.fetchone()[0])"
            )
        },
    )
    print(r.get("stdout") or r)
    assert r.get("ok"), r

    print("== run_code: top 10 subs ==")
    r = d(
        "run_code",
        {
            "code": (
                "rows = con.execute("
                "'SELECT id, title_zh, title_en, subscriptions FROM details "
                "ORDER BY subscriptions DESC LIMIT 10').fetchall()\n"
                "for row in rows:\n"
                "    print(dict(row))"
            )
        },
    )
    print((r.get("stdout") or "")[:1500])
    assert r.get("ok"), r

    print("== grep_text: 洋名 ==")
    r = d("grep_text", {"pattern": "洋名", "limit": 8})
    print("hits", r.get("count"), (r.get("items") or [])[:3])
    assert r.get("ok") and int(r.get("count") or 0) > 0, r

    print("== recommend_mods: 冷战大型 ==")
    r = d("recommend_mods", {"query": "冷战大型模组", "top_n": 5})
    print("count", r.get("count"), [(x.get("title_zh") or x.get("title_en"), x.get("score")) for x in (r.get("items") or [])[:3]])
    assert r.get("ok") and int(r.get("count") or 0) > 0, r

    print("== surprise shortlist sample (no LLM) ==")
    short = build_surprise_shortlist(mods, top_k=200)
    sample = weighted_sample(short, n=10)
    assert len(sample) == 10
    for s in sample[:2]:
        m = s.mod
        print(m.id, m.title_zh or m.title, "/", m.title_en or "")
    print("OK tools+surprise")


def smoke_http(base: str) -> None:
    health = _http_json(f"{base}/health")
    print("health", health)
    assert health.get("ok"), health

    t0 = time.time()
    sur = _http_json(f"{base}/api/surprise", {"n": 10, "seed": 42})
    dt = time.time() - t0
    print(f"surprise items={len(sur.get('items') or [])} in {dt:.2f}s used_llm={ (sur.get('present') or {}).get('used_llm') }")
    assert sur.get("ok") and len(sur.get("items") or []) > 0
    assert (sur.get("present") or {}).get("used_llm") is False
    assert dt < 5.0, f"surprise too slow: {dt}"

    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        print("skip /api/ask (no DEEPSEEK_API_KEY)")
        return

    device = "smoke-device"
    conv = "smoke-conv-" + str(int(time.time()))
    questions = [
        "by天草做了多少模组？订阅超过1000的有几个？",
        "总订阅前10是哪些（要中英标题）？",
        "有没有和洋名汉化相关的模组？",
        "有没有冷战大型模组？",
        "最好玩的模组是什么？",
    ]
    for q in questions:
        print("== ask:", q)
        t0 = time.time()
        try:
            j = _http_json(
                f"{base}/api/ask",
                {
                    "question": q,
                    "device_id": device,
                    "conversation_id": conv,
                },
                timeout=240,
            )
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read()[:400])
            raise
        print(f"  {time.time()-t0:.1f}s coverage={(j.get('evidence_summary') or {}).get('coverage')} find={j.get('find')}")
        ans = (j.get("answer") or "")[:400]
        print("  answer:", ans.replace("\n", " ")[:300])
        assert j.get("answer"), j

    # 短记忆追问
    print("== ask follow-up memory ==")
    j = _http_json(
        f"{base}/api/ask",
        {
            "question": "刚才说的那个作者订阅破千的有几个来着？",
            "device_id": device,
            "conversation_id": conv,
        },
        timeout=240,
    )
    print((j.get("answer") or "")[:400])
    assert j.get("answer")
    print("OK http ask+memory")


def main() -> None:
    smoke_tools()
    base = (os.environ.get("SMOKE_BASE") or "").strip()
    if base:
        smoke_http(base.rstrip("/"))
    else:
        print("set SMOKE_BASE=http://127.0.0.1:1837 to hit running server")


if __name__ == "__main__":
    main()
