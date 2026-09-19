"""控制台 FastAPI 应用：设备门禁 + 多服务日志（问答 / 模组推荐）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import DEVICE_HEADER, DeviceGate, normalize_device_id
from .config import ConsoleSettings, load_console_settings
from .log_reader import list_log_dates, load_llm_calls, load_recent_tasks
from .recommend_log_reader import (
    list_recommend_log_dates,
    load_recommend_detail,
    load_recent_recommend,
)

log = logging.getLogger("console.app")

_STATIC = Path(__file__).resolve().parent / "static"
_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}

SERVICES = (
    {
        "id": "qa",
        "name": "问答",
        "detail_kind": "llm",
    },
    {
        "id": "recommend",
        "name": "模组推荐",
        "detail_kind": "recommend",
    },
)


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=64)


def _html(path: Path) -> FileResponse:
    return FileResponse(path, media_type="text/html; charset=utf-8", headers=_NO_CACHE)


def _normalize_service(service: str | None) -> str:
    sid = (service or "qa").strip().lower()
    if sid in ("qa", "web", "问答"):
        return "qa"
    if sid in ("recommend", "mod", "模组推荐"):
        return "recommend"
    raise HTTPException(status_code=400, detail="未知 service（qa | recommend）")


def create_app(settings: ConsoleSettings | None = None) -> FastAPI:
    cfg = settings or load_console_settings()
    gate = DeviceGate(
        data_dir=cfg.data_dir,
        password=cfg.password,
        session_secret=cfg.session_secret,
    )

    app = FastAPI(title="Patchouli Console", docs_url=None, redoc_url=None)

    def _auth_device(request: Request) -> str:
        return gate.require(request)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> FileResponse:
        if not gate.is_authenticated(request):
            return _html(_STATIC / "login.html")
        return _html(_STATIC / "index.html")

    @app.get("/login", response_model=None)
    async def login_page(request: Request):
        if gate.is_authenticated(request):
            return RedirectResponse(url="/", status_code=303)
        return _html(_STATIC / "login.html")

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        did = gate.device_id_from_request(request)
        authenticated = gate.is_authenticated(request)
        return {
            "device_id": did or "",
            "authenticated": authenticated,
            "whitelisted": bool(did and gate.is_allowed_device(did)),
            "device_header": DEVICE_HEADER,
        }

    @app.post("/api/auth/login")
    async def auth_login(body: LoginBody, request: Request) -> JSONResponse:
        try:
            did = normalize_device_id(body.device_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 device_id") from None
        if not gate.verify_password(body.password):
            raise HTTPException(status_code=403, detail="密码错误")
        gate.remember_device(did, note="password")
        resp = JSONResponse({"ok": "1", "device_id": did, "redirect": "/"})
        gate.issue_cookie(resp, did)
        for k, v in _NO_CACHE.items():
            resp.headers[k] = v
        return resp

    @app.get("/api/services")
    async def services(_: str = Depends(_auth_device)) -> dict[str, Any]:
        return {"services": list(SERVICES), "default": "qa"}

    @app.get("/api/logs/dates")
    async def logs_dates(
        service: str | None = Query(None),
        _: str = Depends(_auth_device),
    ) -> dict[str, Any]:
        sid = _normalize_service(service)
        if sid == "recommend":
            dates = list_recommend_log_dates(cfg.recommend_log_dir)
        else:
            dates = list_log_dates(cfg.reqlog_dir)
        return {"service": sid, "dates": dates}

    @app.get("/api/logs/recent")
    async def logs_recent(
        date: str | None = Query(None),
        service: str | None = Query(None),
        _: str = Depends(_auth_device),
    ) -> dict[str, Any]:
        sid = _normalize_service(service)
        if sid == "recommend":
            try:
                rows, total = load_recent_recommend(
                    cfg.recommend_log_dir,
                    limit=cfg.log_limit,
                    date=date,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "service": sid,
                "date": date or "",
                "total": total,
                "limit": cfg.log_limit,
                "truncated": total > cfg.log_limit,
                "items": [
                    {
                        "task_id": r.request_id,
                        "time": r.time_bj,
                        "date": r.date_bj,
                        "user": "推荐",
                        "source": r.mode,
                        "conversation_id": "",
                        "question": r.query,
                        "status": "refused" if r.refused else "completed",
                        "status_zh": r.status_zh,
                        "result": r.result_excerpt,
                        "result_full": r.result_excerpt,
                        "expandable": True,
                        "elapsed_sec": None,
                        "core_request_id": "",
                        "has_llm": False,
                        "detail_kind": "recommend",
                        "mode": r.mode,
                        "query_rewritten": r.query_rewritten,
                        "filters": r.filters,
                        "recalled_n": len(r.recalled),
                        "passed_n": len(r.passed),
                    }
                    for r in rows
                ],
            }

        try:
            rows, total = load_recent_tasks(
                cfg.reqlog_dir,
                limit=cfg.log_limit,
                days=0,
                date=date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "service": sid,
            "date": date or "",
            "total": total,
            "limit": cfg.log_limit,
            "truncated": total > cfg.log_limit,
            "items": [
                {
                    "task_id": r.task_id,
                    "time": r.time_bj,
                    "date": r.date_bj,
                    "user": r.user_label,
                    "source": r.source,
                    "conversation_id": r.conversation_id,
                    "question": r.question,
                    "status": r.status,
                    "status_zh": r.status_zh,
                    "result": r.result_excerpt,
                    "result_full": r.result_full,
                    "expandable": r.result_full != r.result_excerpt,
                    "elapsed_sec": r.elapsed_sec,
                    "core_request_id": r.core_request_id,
                    "has_llm": r.has_llm,
                    "detail_kind": "llm",
                }
                for r in rows
            ],
        }

    @app.get("/api/logs/{task_id}/llm")
    async def logs_llm(
        task_id: str,
        _: str = Depends(_auth_device),
    ) -> dict[str, Any]:
        data = load_llm_calls(
            task_id=task_id,
            reqlog_root=cfg.reqlog_dir,
        )
        if not data.get("ok") and data.get("error") == "找不到该任务日志":
            raise HTTPException(status_code=404, detail=data["error"])
        if not data.get("ok") and data.get("error") == "无效 task_id":
            raise HTTPException(status_code=400, detail=data["error"])
        slim_calls = []
        for call in data.get("calls") or []:
            slim_calls.append(
                {
                    "stage": call.get("stage"),
                    "name": call.get("name"),
                    "file": call.get("file"),
                    "response_file": call.get("response_file") or "",
                    "model": call.get("model"),
                    "temperature": call.get("temperature"),
                    "tool_choice": call.get("tool_choice"),
                    "messages": call.get("messages") or [],
                    "output": call.get("output"),
                }
            )
        return {
            "ok": True,
            "task_id": data.get("task_id"),
            "core_request_id": data.get("core_request_id") or "",
            "note": data.get("note") or "",
            "calls": slim_calls,
        }

    @app.get("/api/logs/{task_id}/recommend")
    async def logs_recommend_detail(
        task_id: str,
        _: str = Depends(_auth_device),
    ) -> dict[str, Any]:
        data = load_recommend_detail(cfg.recommend_log_dir, task_id)
        if data is None:
            raise HTTPException(status_code=404, detail="找不到该推荐日志")
        return data

    if (_STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 401 and request.url.path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "auth_required"})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app
