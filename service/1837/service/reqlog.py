"""模组推荐请求日志：按日 JSONL，只记问句 / 转义过滤 / 召回与通过的 id+标题+可玩点。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("recommend.reqlog")

BJ = timezone(timedelta(hours=8))

_lock = threading.Lock()


def default_log_dir() -> Path:
    """约定：仓库根 log/1837/。RECOMMEND_LOG_DIR 仅作可选覆盖（如容器挂载）。"""
    raw = (os.environ.get("RECOMMEND_LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # service/1837/service/reqlog.py → 1837 → service → 仓库根
    repo = Path(__file__).resolve().parents[3]
    return (repo / "log" / "1837").resolve()


def _slim_mod(row: dict[str, Any] | Any, *, with_hook: bool) -> dict[str, str]:
    if isinstance(row, dict):
        mid = str(row.get("id") or row.get("modid") or "")
        title = str(row.get("title") or "")
        author = str(row.get("author") or "")
        out = {"id": mid, "title": title}
        if author:
            out["author"] = author
        if with_hook:
            out["hook"] = str(row.get("hook") or "")
        return out
    m = getattr(row, "mod", None)
    if m is not None:
        mid = str(getattr(m, "id", "") or "")
        title = str(getattr(m, "title", "") or "")
        author = str(getattr(m, "author", "") or "")
    else:
        mid = str(getattr(row, "id", "") or "")
        title = str(getattr(row, "title", "") or "")
        author = str(getattr(row, "author", "") or "")
    out = {"id": mid, "title": title}
    if author:
        out["author"] = author
    if with_hook:
        out["hook"] = str(getattr(row, "hook", "") or "")
    return out


def write_recommend_log(
    *,
    mode: str,
    query: str,
    query_rewritten: str | None = None,
    filters: dict[str, Any] | None = None,
    recalled: list[Any] | None = None,
    passed: list[Any] | None = None,
    refused: bool = False,
    refuse_reason: str | None = None,
    used_rewrite: bool = False,
    rewrite_error: str | None = None,
    present_meta: dict[str, Any] | None = None,
    log_dir: Path | None = None,
) -> str:
    """追加一条请求日志，返回 request_id。失败只打 warning，不抛。"""
    rid = uuid.uuid4().hex[:16]
    now = time.time()
    dt = datetime.fromtimestamp(now, BJ)
    record: dict[str, Any] = {
        "id": rid,
        "ts": now,
        "time_bj": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date_bj": dt.strftime("%Y-%m-%d"),
        "mode": mode,
        "query": query or "",
        "query_rewritten": query_rewritten,
        "used_rewrite": bool(used_rewrite),
        "rewrite_error": rewrite_error,
        "filters": filters or {},
        "refused": bool(refused),
        "refuse_reason": refuse_reason,
        "recalled": [_slim_mod(x, with_hook=False) for x in (recalled or [])],
        "passed": [_slim_mod(x, with_hook=True) for x in (passed or [])],
    }
    if present_meta:
        record["present"] = {
            "candidates": present_meta.get("candidates"),
            "kept": present_meta.get("kept"),
            "refused": present_meta.get("refused"),
            "used_llm": present_meta.get("used_llm"),
            "empty": present_meta.get("empty"),
            "present_error": present_meta.get("present_error"),
        }

    root = Path(log_dir) if log_dir is not None else default_log_dir()
    day = dt.strftime("%Y%m%d")
    path = root / f"{day}.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with _lock:
            root.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        log.warning("reqlog write failed path=%s err=%s", path, e)
    return rid
