"""1936 HOI4 问答网页：协议对齐旧 gatekeeper.web，实现不依赖 archive。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.ask import (
    format_completed_content,
    list_mods_for_api,
    load_request_config,
    new_task_id,
    normalize_corpus,
    resolve_mods_catalog,
    run_ask,
)
from web.history import WebHistoryStore
from web.ids import normalize_conversation_id, normalize_device_id
from web.progress import progress_extra, progress_label

log = logging.getLogger("web.app")

_STATIC = Path(__file__).resolve().parent / "static"
_STATUS_ZH = {
    "accepted": "可处理",
    "progress": "进度",
    "completed": "完成",
    "busy": "忙",
    "rate_limited": "频率过高",
}

_MEMORY_MAX_TURNS = max(0, int(os.environ.get("SHORT_MEMORY_WEB_MAX_TURNS") or "8"))
_MEMORY_MAX_CHARS = max(500, int(os.environ.get("SHORT_MEMORY_INJECT_MAX_CHARS") or "3500"))


def create_app(
    *,
    history: WebHistoryStore | None = None,
) -> FastAPI:
    store = history or WebHistoryStore()
    config = load_request_config()
    catalog = resolve_mods_catalog(config)

    app = FastAPI(title="Patchouli HOI4 QA")
    app.state.history = store
    app.state.config = config
    app.state.catalog = catalog

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            _STATIC / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "1", "source": "web"}

    @app.get("/api/mods")
    async def api_mods(
        status: str | None = Query(None),
        category: str | None = Query(None),
    ) -> JSONResponse:
        filt = status if status is not None else category
        st = None
        if filt is not None:
            raw = str(filt).strip().lower().replace("-", "").replace("_", "")
            if raw in {"vanilla", "vanillaonly", "onlyvanilla"}:
                st = "vanillaonly"
            elif raw == "common":
                st = "common"
        if st == "vanillaonly":
            mods: list[dict[str, str]] = []
            cat_filt = None
        elif st == "common":
            mods = list_mods_for_api(catalog_path=catalog, category=st)
            cat_filt = st
        else:
            mods = list_mods_for_api(catalog_path=catalog)
            cat_filt = None
        return JSONResponse(
            {
                "ok": True,
                "max_tags": 2,
                "statuses": [
                    {"id": "common", "label": "通用"},
                    {"id": "vanillaonly", "label": "只看原版"},
                ],
                "status": st or "common",
                "category": cat_filt,
                "mods": mods,
            }
        )

    @app.get("/api/conversations")
    async def api_conversations(
        device_id: str = Query(..., min_length=1, max_length=64),
    ) -> JSONResponse:
        try:
            did = normalize_device_id(device_id)
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "无效的 device_id"},
                status_code=400,
            )
        convs = store.list_conversations(did)
        return JSONResponse(
            {"ok": True, "device_id": did, "conversations": convs}
        )

    @app.delete("/api/conversations/{conversation_id}")
    async def api_hide_conversation(
        conversation_id: str,
        device_id: str = Query(..., min_length=1, max_length=64),
    ) -> JSONResponse:
        try:
            did = normalize_device_id(device_id)
            cid = normalize_conversation_id(conversation_id)
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "无效的 device_id 或 conversation_id"},
                status_code=400,
            )
        store.hide(did, cid)
        return JSONResponse(
            {
                "ok": True,
                "device_id": did,
                "conversation_id": cid,
                "hidden": True,
            }
        )

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        send_lock = asyncio.Lock()
        in_flight: set[str] = set()
        log.info("web ws open")
        await websocket.send_json(
            {
                "type": "hello",
                "source": "web",
                "note": "设备身份；进页拉取服务端历史；同对话生成中锁定",
            }
        )
        try:
            while True:
                raw = await websocket.receive_json()
                if not isinstance(raw, dict):
                    continue
                if raw.get("type") != "ask":
                    await websocket.send_json(
                        {"type": "error", "message": "未知消息类型"}
                    )
                    continue
                question = str(raw.get("question") or "").strip()
                if not question:
                    await websocket.send_json(
                        {"type": "error", "message": "问题不能为空"}
                    )
                    continue
                corpus = normalize_corpus(raw.get("corpus"))
                try:
                    device_id = normalize_device_id(str(raw.get("device_id") or ""))
                    conversation_id = normalize_conversation_id(
                        str(raw.get("conversation_id") or "")
                    )
                except ValueError as exc:
                    msg = str(exc)
                    err = (
                        "无效的 device_id"
                        if "device_id" in msg
                        else "无效的 conversation_id"
                    )
                    await websocket.send_json({"type": "error", "message": err})
                    continue
                if conversation_id in in_flight:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "conversation_id": conversation_id,
                            "message": "本对话正在生成，请稍候或切换到其他对话",
                        }
                    )
                    continue
                in_flight.add(conversation_id)
                asyncio.create_task(
                    _handle_ask(
                        websocket,
                        store=store,
                        config=config,
                        question=question,
                        device_id=device_id,
                        conversation_id=conversation_id,
                        corpus=corpus,
                        send_lock=send_lock,
                        in_flight=in_flight,
                    ),
                    name="web-ask",
                )
        except WebSocketDisconnect:
            log.info("web ws closed")
        except Exception:
            log.exception("web ws error")
            try:
                await websocket.close()
            except Exception:
                pass

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    return app


async def _handle_ask(
    websocket: WebSocket,
    *,
    store: WebHistoryStore,
    config: Any,
    question: str,
    device_id: str,
    conversation_id: str,
    corpus: dict[str, Any],
    send_lock: asyncio.Lock,
    in_flight: set[str],
) -> None:
    task_id = new_task_id()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    def on_progress(ev: dict[str, Any]) -> None:
        extra = progress_extra(ev)
        label = progress_label(
            phase=str(ev.get("phase") or "find"),
            round_i=int(ev.get("round") or 0),
            max_rounds=int(ev.get("max_rounds") or 12),
            tool=str(ev["tool"]) if ev.get("tool") else None,
        )
        fut = asyncio.run_coroutine_threadsafe(
            queue.put(
                {
                    "type": "reply",
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "status": "progress",
                    "status_zh": _STATUS_ZH["progress"],
                    "content": label,
                    "echo_question": question,
                    "elapsed_sec": None,
                    "ok": True,
                    "extra": extra,
                }
            ),
            loop,
        )
        try:
            fut.result(timeout=5)
        except Exception:
            log.debug("progress queue put failed", exc_info=True)

    try:
        await send(
            {
                "type": "ack",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "question": question,
            }
        )
        await send(
            {
                "type": "reply",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "accepted",
                "status_zh": _STATUS_ZH["accepted"],
                "content": f"收到您的问题：{question}\n正在处理中。请稍等一段时间。",
                "echo_question": question,
                "elapsed_sec": None,
                "ok": True,
                "extra": {},
            }
        )

        memory_blob = store.recent_turns_text(
            device_id=device_id,
            conversation_id=conversation_id,
            max_turns=_MEMORY_MAX_TURNS,
            max_chars=_MEMORY_MAX_CHARS,
        )

        async def pump() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    break
                await send(item)

        pump_task = asyncio.create_task(pump(), name="web-progress-pump")

        def work() -> dict[str, Any]:
            return run_ask(
                question,
                corpus=corpus,
                memory_blob=memory_blob,
                on_progress=on_progress,
                config=config,
            )

        try:
            result = await asyncio.to_thread(work)
        finally:
            await queue.put(None)
            await pump_task

        content = format_completed_content(
            question, result.get("answer") or "", float(result.get("elapsed_sec") or 0)
        )
        ok = bool(result.get("ok"))
        await send(
            {
                "type": "reply",
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "completed",
                "status_zh": _STATUS_ZH["completed"],
                "content": content,
                "echo_question": question,
                "elapsed_sec": result.get("elapsed_sec"),
                "ok": ok,
                "extra": {
                    "core_request_id": result.get("request_id"),
                    "core_log_dir": result.get("log_dir"),
                    "error": result.get("error"),
                    "corpus": corpus,
                },
            }
        )
        try:
            store.append_turn(
                device_id=device_id,
                conversation_id=conversation_id,
                question=question,
                answer=content,
                corpus=corpus,
                ok=ok,
            )
        except Exception:
            log.exception("web history append failed")
    except Exception:
        log.exception(
            "web ask failed device=%s conv=%s", device_id, conversation_id
        )
        try:
            await send(
                {
                    "type": "error",
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                    "message": "处理失败，请重试",
                }
            )
        except Exception:
            pass
    finally:
        in_flight.discard(conversation_id)


def serve() -> None:
    import uvicorn

    host = (os.environ.get("WEB_HOST") or "127.0.0.1").strip() or "0.0.0.0"
    port = max(1, int(os.environ.get("WEB_PORT") or "1936"))
    log.info("1936 web on http://%s:%s", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
