"""模组卡片 HTTP 壳：:1838；与 1837 共用 workshop_details sqlite（含 title_en/title_zh）。"""

from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from service.catalog import WorkshopCatalog, resolve_brief_path, resolve_db_path

log = logging.getLogger("modcards")
_STATIC = Path(__file__).resolve().parent / "static"


class _State:
    catalog: WorkshopCatalog | None = None
    db_path: str = ""
    brief_path: str = ""
    lock = threading.Lock()
    ready = False
    error: str | None = None


STATE = _State()


def _load() -> None:
    path = resolve_db_path(os.environ.get("WORKSHOP_DB"))
    brief = resolve_brief_path(os.environ.get("WORKSHOP_BRIEF_DB"))
    STATE.db_path = str(path)
    STATE.brief_path = str(brief) if brief else ""
    try:
        cat = WorkshopCatalog(path, brief_path=brief)
    except Exception as e:
        STATE.error = str(e)
        STATE.ready = False
        STATE.catalog = None
        log.exception("load failed")
        return
    with STATE.lock:
        old = STATE.catalog
        STATE.catalog = cat
        STATE.error = None
        STATE.ready = True
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    log.info(
        "loaded mods=%s authors=%s title_split=%s db=%s brief=%s",
        cat.total,
        cat.authors_matched,
        cat.title_split,
        path,
        brief,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    _load()
    yield
    if STATE.catalog is not None:
        STATE.catalog.close()


app = FastAPI(title="Patchouli Mod Cards", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": STATE.ready,
        "mods": STATE.catalog.total if STATE.catalog else 0,
        "authors": STATE.catalog.authors_matched if STATE.catalog else 0,
        "title_split": STATE.catalog.title_split if STATE.catalog else 0,
        "has_title_i18n": STATE.catalog.has_title_i18n if STATE.catalog else False,
        "db": STATE.db_path,
        "brief": STATE.brief_path,
        "error": STATE.error,
    }


@app.get("/api/browse")
def browse(
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    sort: str = Query("subscriptions"),
) -> JSONResponse:
    if not STATE.ready or STATE.catalog is None:
        return JSONResponse({"ok": False, "error": STATE.error or "not ready"}, status_code=503)
    cards, total = STATE.catalog.browse(offset=offset, limit=limit, sort=sort)
    return JSONResponse(
        {
            "ok": True,
            "total": total,
            "offset": offset,
            "limit": limit,
            "sort": sort,
            "items": [c.summary() for c in cards],
        }
    )


@app.get("/api/search")
def search(
    q: str = Query("", max_length=200),
    limit: int = Query(40, ge=1, le=100),
) -> JSONResponse:
    if not STATE.ready or STATE.catalog is None:
        return JSONResponse({"ok": False, "error": STATE.error or "not ready"}, status_code=503)
    cards = STATE.catalog.search(q, limit=limit)
    return JSONResponse(
        {
            "ok": True,
            "q": q.strip(),
            "count": len(cards),
            "items": [c.summary() for c in cards],
        }
    )


@app.get("/api/mod/{mod_id}")
def mod_detail(mod_id: str) -> JSONResponse:
    if not STATE.ready or STATE.catalog is None:
        return JSONResponse({"ok": False, "error": STATE.error or "not ready"}, status_code=503)
    card = STATE.catalog.get(mod_id)
    if card is None:
        return JSONResponse({"ok": False, "error": "not found", "id": mod_id}, status_code=404)
    return JSONResponse({"ok": True, "item": card.to_dict()})
