"""组装 RequestConfig、调用 run_request、映射进度事件。"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("web.ask")

ProgressCb = Callable[[dict[str, Any]], None]


def _ensure_core_paths() -> Path:
    core = Path(__file__).resolve().parents[1]  # service/1836
    repo = core.parent.parent
    import sys

    for p in (core, repo):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    return core


def load_request_config():
    _ensure_core_paths()
    from patchouli import RequestConfig

    raw = (os.environ.get("PATCHOULI_CONFIG") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_file():
            return RequestConfig.from_file(p)
    return RequestConfig.defaults()


def new_task_id() -> str:
    return uuid.uuid4().hex[:16]


def normalize_corpus(raw: Any) -> dict[str, Any]:
    _MAX_TAGS = 2
    _VALID_STATUSES = frozenset({"common", "bytiancao", "vanillaonly"})

    def _normalize_status(v: Any) -> str:
        if v is None:
            return "common"
        s = str(v).strip().lower().replace("-", "").replace("_", "")
        if s in {"vanilla", "vanillaonly", "onlyvanilla"}:
            return "vanillaonly"
        if s in {"bytiancao", "tiancao"}:
            return "bytiancao"
        if s == "common":
            return "common"
        s2 = str(v).strip().lower()
        if s2 in _VALID_STATUSES:
            return s2
        return "common"

    if not isinstance(raw, dict):
        return {"status": "common", "mode": "auto", "mod_ids": []}
    status = _normalize_status(raw.get("status"))
    mode = str(raw.get("mode") or "auto").strip().lower()
    ids: list[str] = []
    for item in raw.get("mod_ids") or raw.get("tags") or []:
        mid = str(item or "").strip()
        if mid and mid not in ids:
            ids.append(mid)
        if len(ids) >= _MAX_TAGS:
            break
    if (
        status == "vanillaonly"
        or mode in {"vanilla", "vanilla_only", "only_vanilla"}
        or bool(raw.get("vanilla_only"))
    ):
        return {"status": "vanillaonly", "mode": "vanilla", "mod_ids": []}
    if mode in {"mods", "mod", "tags", "tag"} or ids:
        return (
            {"status": status, "mode": "mods", "mod_ids": ids[:_MAX_TAGS]}
            if ids
            else {"status": status, "mode": "auto", "mod_ids": []}
        )
    return {"status": status, "mode": "auto", "mod_ids": []}

def list_mods_for_api(
    *,
    catalog_path: Path,
    category: str | None = None,
) -> list[dict[str, str]]:
    import json

    if not catalog_path.is_file():
        return []

    def _normalize_category(v: Any) -> str:
        c = str(v or "common").strip().lower()
        return c if c in {"common", "bytiancao"} else "common"

    def _normalize_status(v: Any) -> str:
        s = str(v or "").strip().lower().replace("-", "").replace("_", "")
        if s in {"vanilla", "vanillaonly", "onlyvanilla"}:
            return "vanillaonly"
        if s in {"common", "bytiancao"}:
            return s
        return "common"

    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if category is not None and _normalize_status(category) == "vanillaonly":
        return []
    want = _normalize_category(category) if category else None
    out: list[dict[str, str]] = []
    for raw in data.get("mods") or []:
        mid = str(raw.get("id") or "").strip()
        if not mid:
            continue
        cat = _normalize_category(raw.get("category", raw.get("catagory")))
        if want is not None and cat != want:
            continue
        short = str(raw.get("short") or mid).strip()
        name = str(raw.get("name") or mid).strip()
        label = (
            f"{short}（{name}）"
            if short and short != name
            else (name or short or mid)
        )
        out.append(
            {
                "id": mid,
                "short": short,
                "name": name,
                "label": label,
                "category": cat,
            }
        )
    return out


def resolve_mods_catalog(config: Any) -> Path:
    raw = getattr(config, "mods_catalog", None)
    if raw:
        p = Path(raw)
        if p.is_file():
            return p.resolve()
    core = Path(__file__).resolve().parents[1]
    return (core / "data" / "mods_catalog.json").resolve()


def augment_question(question: str, memory_blob: str) -> str:
    q = (question or "").strip()
    mem = (memory_blob or "").strip()
    if not mem:
        return q
    return (
        "以下是同一对话的近期问答，仅供指代消解，不要复述整段历史。\n"
        f"{mem}\n\n"
        f"当前问题：{q}"
    )


def run_ask(
    question: str,
    *,
    corpus: dict[str, Any] | None = None,
    memory_blob: str = "",
    on_progress: ProgressCb | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """同步执行；应在线程池中调用。返回 {ok, answer, elapsed_sec, request_id, error}。"""
    _ensure_core_paths()
    from patchouli import run_request

    cfg = config if config is not None else load_request_config()
    pin = corpus or {"status": "common", "mode": "auto", "mod_ids": []}
    q = augment_question(question, memory_blob)

    def _progress(ev: dict[str, Any]) -> None:
        if on_progress is None:
            return
        try:
            on_progress(dict(ev or {}))
        except Exception:
            log.debug("on_progress failed", exc_info=True)

    try:
        result = run_request(q, cfg, on_progress=_progress, corpus_pin=pin)
    except Exception as exc:
        log.exception("run_request failed")
        return {
            "ok": False,
            "answer": f"[处理失败: {type(exc).__name__}: {exc}]",
            "elapsed_sec": 0.0,
            "request_id": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    answer = (result.answer or "").strip()
    return {
        "ok": bool(result.ok),
        "answer": answer,
        "elapsed_sec": float(result.elapsed_sec or 0.0),
        "request_id": result.request_id,
        "log_dir": result.log_dir,
        "error": None if result.ok else "request_not_ok",
    }


def format_completed_content(question: str, answer: str, elapsed_sec: float) -> str:
    q = (question or "").strip() or "（无文本内容）"
    a = (answer or "").strip()
    secs = f"{elapsed_sec:.2f}"
    return f"对于您的问题：{q}\n耗费{secs}秒。\n回答如下：\n{a}"
