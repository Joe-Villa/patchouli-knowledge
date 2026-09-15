"""模组社区 HTTP：:1837；对话 Agent + 惊喜短名单（无 LLM）。"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from agent.llm import load_llm_config
from agent.pipeline import invoke as agent_invoke
from scoring.catalog import (
    DEFAULT_DB,
    FALLBACK_DB,
    load_ge300_details,
    resolve_brief_path,
)
from scoring.filters import HardFilterSpec, KNOWN_TAGS, apply_hard_filter, merge_hard_filters, parse_hard_filter
from scoring.fit_tfidf import FitTfidf
from scoring.surprise import (
    DEFAULT_SHORTLIST_K,
    DEFAULT_SURPRISE_FILTER,
    SurpriseRanked,
    build_surprise_shortlist,
    weighted_sample,
)
from service.memory import ShortMemoryStore, format_injection, web_memory_address
from service.memory.gate import LlmMemoryGate, NoopMemoryGate
from service.reqlog import write_recommend_log

log = logging.getLogger("workshop.community")

_STATIC = Path(__file__).resolve().parent / "static"
DISPLAY_N = 10
ASK_WORKERS = max(1, int(os.environ.get("ASK_WORKERS") or 2))


class _State:
    mods: list = []
    fit_model: FitTfidf | None = None
    surprise_shortlist: list = []
    db_path: str = ""
    brief_path: str = ""
    authors_matched: int = 0
    lock = threading.Lock()
    ready = False
    error: str | None = None
    llm_ready = False
    memory = ShortMemoryStore(gap_sec=0.0, max_turns=30)
    memory_gate: Any = NoopMemoryGate()
    pool: ThreadPoolExecutor | None = None


STATE = _State()


def _db_path() -> Path:
    raw = (os.environ.get("WORKSHOP_DB") or "").strip()
    if raw:
        return Path(raw)
    for p in (
        Path("/data/workshop_details_all.sqlite"),
        Path("/data/workshop_details_ge300.sqlite"),
        DEFAULT_DB,
        FALLBACK_DB,
    ):
        if p.is_file():
            return p
    return DEFAULT_DB


def _brief_path() -> Path | None:
    return resolve_brief_path(os.environ.get("WORKSHOP_BRIEF_DB"))


def _load() -> None:
    path = _db_path()
    brief = _brief_path()
    STATE.db_path = str(path)
    STATE.brief_path = str(brief) if brief else ""
    if not path.is_file():
        STATE.error = f"workshop db missing: {path}"
        STATE.ready = False
        return
    mods = load_ge300_details(path, brief_path=brief)
    authors_matched = sum(1 for m in mods if m.author)
    fit = FitTfidf(mods)
    shortlist_k = int(os.environ.get("SURPRISE_SHORTLIST_K") or DEFAULT_SHORTLIST_K)
    shortlist_k = max(50, min(shortlist_k, 2000))
    surprise_pool = build_surprise_shortlist(mods, top_k=shortlist_k)
    llm = load_llm_config()
    gate: Any
    if llm.api_key:
        gate = LlmMemoryGate(
            api_key=llm.api_key,
            base_url=llm.base_url,
            model=llm.model,
        )
    else:
        gate = NoopMemoryGate()
    with STATE.lock:
        STATE.mods = mods
        STATE.fit_model = fit
        STATE.surprise_shortlist = surprise_pool
        STATE.authors_matched = authors_matched
        STATE.error = None
        STATE.ready = True
        STATE.llm_ready = bool(llm.api_key)
        STATE.memory_gate = gate
    log.info(
        "loaded mods=%s authors=%s/%s surprise_shortlist=%s llm=%s brief=%s",
        len(mods),
        authors_matched,
        len(mods),
        len(surprise_pool),
        bool(llm.api_key),
        STATE.brief_path or "(none)",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    _load()
    STATE.pool = ThreadPoolExecutor(max_workers=ASK_WORKERS, thread_name_prefix="ask")
    try:
        yield
    finally:
        if STATE.pool:
            STATE.pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Patchouli Mod Community", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": STATE.ready,
        "service": "mod-community",
        "mods": len(STATE.mods) if STATE.ready else 0,
        "authors_matched": STATE.authors_matched if STATE.ready else 0,
        "surprise_shortlist": len(STATE.surprise_shortlist) if STATE.ready else 0,
        "db": STATE.db_path,
        "brief": STATE.brief_path,
        "llm": STATE.llm_ready,
        "error": STATE.error,
        "known_tags": list(KNOWN_TAGS),
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "product": "模组社区",
        "known_tags": list(KNOWN_TAGS),
        "display_n": DISPLAY_N,
        "endpoints": ["/api/ask", "/api/surprise", "/health"],
    }


def _base_item_fields(mod_like: Any) -> dict[str, Any]:
    if isinstance(mod_like, dict):
        return mod_like
    m = mod_like.mod
    zh = (m.title_zh or "").strip()
    en = (m.title_en or "").strip()
    return {
        "id": m.id,
        "modid": m.id,
        "title": m.title_display,
        "title_zh": zh,
        "title_en": en,
        "author": m.author or "",
        "tags": m.tags,
        "subscribers": m.subscribers,
        "file_size": m.file_size,
        "description": m.description or "",
        "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={m.id}",
    }


def _items_from_surprise(short: list[SurpriseRanked]) -> list[dict[str, Any]]:
    items = []
    for r in short:
        row = _base_item_fields(r)
        row.update(
            {
                "rank": r.rank,
                "score": round(r.score, 3),
                "hook_score": round(r.hook, 3),
                "soft_sub_sweet": round(r.soft_sub_sweet, 3),
                "SoftFresh": round(r.soft_fresh, 3),
                "hook": "",
            }
        )
        items.append(row)
    return items


def _public_surprise_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "rank": r.get("rank"),
                "id": r.get("id"),
                "modid": r.get("id"),
                "title": r.get("title"),
                "title_zh": r.get("title_zh") or "",
                "title_en": r.get("title_en") or "",
                "author": r.get("author") or "",
                "tags": r.get("tags"),
                "subscribers": r.get("subscribers"),
                "file_size": r.get("file_size"),
                "url": r.get("url"),
                "score": r.get("score"),
                "hook": r.get("hook") or "",
                "hook_score": r.get("hook_score"),
                "soft_sub_sweet": r.get("soft_sub_sweet"),
                "SoftFresh": r.get("SoftFresh"),
            }
        )
    return out


def _surprise_pool(
    *,
    extra_filters: HardFilterSpec | None = None,
) -> tuple[list[SurpriseRanked], HardFilterSpec]:
    user = extra_filters or HardFilterSpec()
    merged = merge_hard_filters(DEFAULT_SURPRISE_FILTER, user)
    with STATE.lock:
        base = list(STATE.surprise_shortlist)
        all_mods = STATE.mods

    if user.is_empty():
        return base, merged

    kept_ids = {m.id for m in apply_hard_filter([r.mod for r in base], user)}
    filtered = [r for r in base if r.mod.id in kept_ids]
    if len(filtered) >= DISPLAY_N:
        return filtered, merged

    rebuilt = build_surprise_shortlist(
        all_mods,
        top_k=max(DEFAULT_SHORTLIST_K, DISPLAY_N * 4),
        hard=merged,
    )
    return rebuilt, merged


def _execute_surprise(
    *,
    exclude_ids: list[str] | None = None,
    extra_filters: HardFilterSpec | None = None,
    rng=None,
    n: int | None = None,
) -> dict[str, Any]:
    pool, filters_merged = _surprise_pool(extra_filters=extra_filters)
    take = max(1, min(int(n or DISPLAY_N), 20))
    raw = weighted_sample(
        pool,
        n=take,
        exclude_ids=[str(x) for x in (exclude_ids or [])],
        rng=rng,
    )
    # 补 rank
    for i, r in enumerate(raw, 1):
        r.rank = i
    candidates = _items_from_surprise(raw)
    public_items = _public_surprise_items(candidates)
    filters_pub = filters_merged.to_public_dict()
    write_recommend_log(
        mode="surprise",
        query="给我惊喜",
        query_rewritten=None,
        filters=filters_pub,
        recalled=candidates,
        passed=public_items,
        present_meta={"candidates": len(candidates), "kept": len(public_items), "used_llm": False},
    )
    return {
        "ok": True,
        "mode": "surprise",
        "shortlist": len(pool),
        "filters": filters_pub,
        "items": public_items,
        "pool_before_filter": len(STATE.mods),
        "pool": len(pool),
        "present": {"candidates": len(candidates), "kept": len(public_items), "used_llm": False},
    }


def _prepare_memory(question: str, device_id: str, conversation_id: str) -> tuple[str, str, str]:
    """返回 (memory_prefix_or_question_block, logical_user, logical_address)。"""
    logical_user = f"web:{device_id or 'anon'}"
    logical_address = web_memory_address(device_id, conversation_id)
    candidates = STATE.memory.candidates(logical_user, logical_address)
    gate = STATE.memory_gate.select(question, candidates)
    if gate.selected:
        injected = format_injection(question, gate.selected)
        # format_injection 已含【当前问题】；找侧再拼会重复，故整段当 user
        return injected, logical_user, logical_address
    return "", logical_user, logical_address


def _run_ask(
    question: str,
    *,
    device_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    memory_block, logical_user, logical_address = _prepare_memory(
        question, device_id, conversation_id
    )
    with STATE.lock:
        mods = list(STATE.mods)
        fit = STATE.fit_model
        db = Path(STATE.db_path)

    result = agent_invoke(
        question,
        db_path=db,
        mods=mods,
        fit_model=fit,
        memory_prefix=memory_block,
    )
    public = result.to_public()
    task_id = uuid.uuid4().hex[:16]
    if result.ok and (result.answer or "").strip():
        STATE.memory.record_success(
            task_id=task_id,
            logical_user=logical_user,
            logical_address=logical_address,
            question=question,
            answer=result.answer,
            finished_at=time.time(),
        )
    write_recommend_log(
        mode="ask",
        query=question,
        query_rewritten=None,
        filters={},
        recalled=result.items,
        passed=result.items,
        present_meta={
            "coverage": result.package.coverage,
            "find": public.get("find"),
            "task_id": task_id,
        },
    )
    public["task_id"] = task_id
    public["conversation_id"] = conversation_id
    public["device_id"] = device_id
    return public


@app.post("/api/ask")
async def ask(request: Request) -> JSONResponse:
    if not STATE.ready or STATE.fit_model is None:
        return JSONResponse(
            {"ok": False, "error": STATE.error or "not ready"},
            status_code=503,
        )
    if not STATE.llm_ready:
        return JSONResponse(
            {"ok": False, "error": "missing DEEPSEEK_API_KEY"},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = str((body or {}).get("question") or (body or {}).get("query") or (body or {}).get("q") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "empty question"}, status_code=400)
    device_id = str((body or {}).get("device_id") or "").strip() or "anon"
    conversation_id = str((body or {}).get("conversation_id") or "").strip() or "default"

    loop = __import__("asyncio").get_event_loop()
    pool = STATE.pool
    if pool is None:
        payload = _run_ask(question, device_id=device_id, conversation_id=conversation_id)
    else:
        payload = await loop.run_in_executor(
            pool,
            lambda: _run_ask(question, device_id=device_id, conversation_id=conversation_id),
        )
    return JSONResponse(payload)


@app.post("/api/surprise")
async def surprise(request: Request) -> JSONResponse:
    """短名单加权抽 N 张卡；无 LLM。"""
    if not STATE.ready or not STATE.surprise_shortlist:
        return JSONResponse(
            {"ok": False, "error": STATE.error or "surprise shortlist not ready"},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    exclude_ids = (body or {}).get("exclude_ids") or []
    if not isinstance(exclude_ids, list):
        exclude_ids = []
    seed = (body or {}).get("seed")
    rng = None
    if seed is not None and str(seed).strip() != "":
        try:
            import random as _random

            rng = _random.Random(int(seed))
        except (TypeError, ValueError):
            rng = None
    n = (body or {}).get("n") or (body or {}).get("limit") or DISPLAY_N
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DISPLAY_N
    extra = parse_hard_filter((body or {}).get("filters") or {})
    payload = _execute_surprise(
        exclude_ids=[str(x) for x in exclude_ids],
        extra_filters=extra,
        rng=rng,
        n=n,
    )
    return JSONResponse(payload)


@app.post("/api/recommend")
async def recommend_compat(request: Request) -> JSONResponse:
    """兼容旧前端：转发到 /api/ask。"""
    return await ask(request)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("RECOMMEND_HOST", "0.0.0.0")
    port = int(os.environ.get("RECOMMEND_PORT", "1837"))
    uvicorn.run(app, host=host, port=port)
