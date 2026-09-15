"""工坊 Agent 工具：run_code / grep_text / recommend_mods / submit。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scoring.filters import apply_hard_filter, parse_hard_filter
from scoring.fit_tfidf import FitTfidf
from scoring.rank import rank_mods

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": "在沙箱跑短 Python。环境变量 WORKSHOP_DB 为 sqlite 路径；用 sqlite3 查 details 表。打印结果到 stdout。",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_text",
            "description": "在 title_zh/title_en/title/description/author/tags 上子串或正则搜索，返回匹配模组摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "regex": {"type": "boolean", "description": "默认 false，字面子串"},
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "默认 title_zh,title_en,title,description,author,tags",
                    },
                    "limit": {"type": "integer"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_mods",
            "description": "模糊推荐：Fit×订阅×新鲜度召回。仅适合需求不尖、候选面大。尖需求/统计勿用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": {"type": "object"},
                    "top_n": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_evidence_package",
            "description": "提交证据包并结束找侧。",
            "parameters": {
                "type": "object",
                "properties": {
                    "coverage": {
                        "type": "string",
                        "enum": ["sufficient", "partial", "empty"],
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "key": {"type": "string"},
                                "entity_type": {"type": "string"},
                                "text": {"type": "string"},
                                "note": {"type": "string"},
                            },
                        },
                    },
                    "unresolved": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["coverage", "items"],
            },
        },
    },
]


@dataclass
class ToolContext:
    db_path: Path
    mods: list = field(default_factory=list)
    fit_model: FitTfidf | None = None
    structure_text: str = ""
    last_recommend_items: list[dict] = field(default_factory=list)


def dumps_tool_result(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:12000]


def _run_code(ctx: ToolContext, args: dict) -> dict:
    code = str(args.get("code") or "")
    if not code.strip():
        return {"ok": False, "error": "empty code"}
    env = os.environ.copy()
    env["WORKSHOP_DB"] = str(ctx.db_path)
    env["PYTHONPATH"] = env.get("PYTHONPATH", "")
    preamble = (
        "import os, sqlite3\n"
        f"DB = os.environ.get('WORKSHOP_DB', {str(ctx.db_path)!r})\n"
        "con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)\n"
        "con.row_factory = sqlite3.Row\n"
    )
    full = preamble + "\n" + code
    try:
        proc = subprocess.run(
            [sys.executable, "-"],
            input=full,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=str(ctx.db_path.parent),
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "")[:8000],
            "stderr": (proc.stderr or "")[:2000],
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}


def _mod_row_summary(row: sqlite3.Row | dict) -> dict:
    if isinstance(row, sqlite3.Row):
        d = {k: row[k] for k in row.keys()}
    else:
        d = dict(row)
    zh = (d.get("title_zh") or d.get("title") or "").strip()
    en = (d.get("title_en") or d.get("title") or "").strip()
    return {
        "id": str(d.get("id") or ""),
        "title_zh": zh,
        "title_en": en,
        "author": (d.get("author") or ""),
        "subscriptions": d.get("subscriptions") or 0,
        "tags": (d.get("tags") or "")[:120],
    }


def _grep(ctx: ToolContext, args: dict) -> dict:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return {"ok": False, "error": "empty pattern"}
    use_re = bool(args.get("regex"))
    limit = max(1, min(int(args.get("limit") or 30), 80))
    fields = args.get("fields") or [
        "title_zh",
        "title_en",
        "title",
        "description",
        "author",
        "tags",
    ]
    fields = [str(f) for f in fields]
    con = sqlite3.connect(f"file:{ctx.db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(details)")}
    use_fields = [f for f in fields if f in cols]
    if not use_fields:
        con.close()
        return {"ok": False, "error": "no valid fields"}
    select = ["id", "subscriptions"] + [
        c for c in ("title", "title_zh", "title_en", "author", "tags", "description") if c in cols
    ]
    rows = con.execute(
        f"SELECT {', '.join(select)} FROM details ORDER BY subscriptions DESC"
    ).fetchall()
    con.close()
    cre = re.compile(pattern, re.I) if use_re else None
    hits = []
    for row in rows:
        blob = "\n".join(str(row[f] or "") for f in use_fields if f in row.keys())
        ok = bool(cre.search(blob)) if cre else (pattern.casefold() in blob.casefold())
        if ok:
            hits.append(_mod_row_summary(row))
            if len(hits) >= limit:
                break
    return {"ok": True, "count": len(hits), "items": hits}


def _recommend(ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}
    if ctx.fit_model is None or not ctx.mods:
        return {"ok": False, "error": "recommend model not ready"}
    top_n = max(1, min(int(args.get("top_n") or 10), 20))
    filters = parse_hard_filter(args.get("filters") or {})
    pool = apply_hard_filter(ctx.mods, filters) if not filters.is_empty() else ctx.mods
    _all, short = rank_mods(
        pool,
        ctx.fit_model,
        query,
        endorse=0.0,
        top_fraction=1.0,
        top_n=top_n,
    )
    items = []
    for r in short:
        m = r.mod
        items.append(
            {
                "id": m.id,
                "title_zh": m.title_zh or m.title,
                "title_en": m.title_en or m.title,
                "author": m.author or "",
                "subscriptions": m.subscribers,
                "tags": m.tags,
                "score": round(r.score, 3),
                "fit": round(r.fit, 3),
                "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={m.id}",
            }
        )
    ctx.last_recommend_items = items
    return {
        "ok": True,
        "query": query,
        "pool": len(pool),
        "count": len(items),
        "items": items,
    }


def make_dispatcher(ctx: ToolContext) -> Callable[[str, dict], dict]:
    def dispatch(name: str, args: dict) -> dict:
        if name == "run_code":
            return _run_code(ctx, args)
        if name == "grep_text":
            return _grep(ctx, args)
        if name == "recommend_mods":
            return _recommend(ctx, args)
        if name == "submit_evidence_package":
            return {"ok": True, "submitted": True, "package": args}
        return {"ok": False, "error": f"unknown tool {name}"}

    return dispatch
