"""网页会话历史：约定目录 log/1936/web/{device_id}.json。"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from web.ids import normalize_conversation_id, normalize_device_id

log = logging.getLogger("web.history")

_TITLE_MAX = 24
_LOCK = threading.Lock()


def default_web_history_dir() -> Path:
    # service/1936/web/history.py → 1936 → service → repo
    repo = Path(__file__).resolve().parents[3]
    return (repo / "log" / "1936" / "web").resolve()


def _truncate_title(text: str) -> str:
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return "新对话"
    return t if len(t) <= _TITLE_MAX else t[:_TITLE_MAX] + "…"


def _device_path(root: Path, device_id: str) -> Path:
    did = normalize_device_id(device_id)
    return Path(root) / f"{did}.json"


def _empty_doc(device_id: str) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "hidden": [],
        "conversations": {},
    }


def _load(path: Path, device_id: str) -> dict[str, Any]:
    if not path.is_file():
        return _empty_doc(device_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("web history load failed path=%s err=%s", path, exc)
        return _empty_doc(device_id)
    if not isinstance(data, dict):
        return _empty_doc(device_id)
    data.setdefault("device_id", device_id)
    data.setdefault("hidden", [])
    data.setdefault("conversations", {})
    if not isinstance(data["conversations"], dict):
        data["conversations"] = {}
    if not isinstance(data["hidden"], list):
        data["hidden"] = []
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


class WebHistoryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_web_history_dir()

    def list_conversations(self, device_id: str) -> list[dict[str, Any]]:
        did = normalize_device_id(device_id)
        path = _device_path(self.root, did)
        with _LOCK:
            doc = _load(path, did)
        hidden = {str(x) for x in doc.get("hidden") or []}
        out: list[dict[str, Any]] = []
        for cid, raw in (doc.get("conversations") or {}).items():
            if cid in hidden or not isinstance(raw, dict):
                continue
            messages = raw.get("messages") or []
            if not messages:
                continue
            out.append(
                {
                    "id": cid,
                    "title": str(raw.get("title") or "新对话"),
                    "updatedAt": int(raw.get("updatedAt") or 0),
                    "messages": list(messages),
                    "corpus": raw.get("corpus")
                    if isinstance(raw.get("corpus"), dict)
                    else None,
                }
            )
        out.sort(key=lambda c: c["updatedAt"], reverse=True)
        return out

    def hide(self, device_id: str, conversation_id: str) -> None:
        did = normalize_device_id(device_id)
        cid = normalize_conversation_id(conversation_id)
        path = _device_path(self.root, did)
        with _LOCK:
            doc = _load(path, did)
            hidden = [str(x) for x in doc.get("hidden") or []]
            if cid not in hidden:
                hidden.append(cid)
            doc["hidden"] = hidden
            _save(path, doc)

    def append_turn(
        self,
        *,
        device_id: str,
        conversation_id: str,
        question: str,
        answer: str,
        corpus: dict[str, Any] | None = None,
        ok: bool = True,
    ) -> None:
        did = normalize_device_id(device_id)
        cid = normalize_conversation_id(conversation_id)
        path = _device_path(self.root, did)
        now_ms = int(time.time() * 1000)
        q = (question or "").strip()
        a = (answer or "").strip()
        with _LOCK:
            doc = _load(path, did)
            convs = doc.setdefault("conversations", {})
            bucket = convs.get(cid)
            if not isinstance(bucket, dict):
                bucket = {
                    "id": cid,
                    "title": _truncate_title(q) if q else "新对话",
                    "updatedAt": now_ms,
                    "messages": [],
                    "corpus": corpus or {"status": "common", "mode": "auto", "mod_ids": []},
                }
                convs[cid] = bucket
            msgs = bucket.setdefault("messages", [])
            if not isinstance(msgs, list):
                msgs = []
                bucket["messages"] = msgs
            if q:
                msgs.append({"role": "user", "text": q})
                if not bucket.get("title") or bucket.get("title") == "新对话":
                    bucket["title"] = _truncate_title(q)
            if a:
                msgs.append({"role": "bot", "text": a})
            elif not ok:
                msgs.append({"role": "bot", "text": "[处理失败]"})
            if corpus:
                bucket["corpus"] = corpus
            bucket["updatedAt"] = now_ms
            # 软删后再次提问：取消隐藏
            hidden = [str(x) for x in doc.get("hidden") or [] if str(x) != cid]
            doc["hidden"] = hidden
            _save(path, doc)

    def recent_turns_text(
        self,
        *,
        device_id: str,
        conversation_id: str,
        max_turns: int = 8,
        max_chars: int = 3500,
    ) -> str:
        """拼近期问答，供注入当前问句前缀。"""
        did = normalize_device_id(device_id)
        cid = normalize_conversation_id(conversation_id)
        path = _device_path(self.root, did)
        with _LOCK:
            doc = _load(path, did)
            bucket = (doc.get("conversations") or {}).get(cid) or {}
            messages = list(bucket.get("messages") or [])
        if not messages or max_turns <= 0:
            return ""
        # 取尾部若干条 user/bot
        selected = [m for m in messages if m.get("role") in ("user", "bot")][
            -max_turns:
        ]
        lines: list[str] = []
        for m in selected:
            role = "用户" if m.get("role") == "user" else "助手"
            text = str(m.get("text") or "").strip()
            if text:
                lines.append(f"{role}：{text}")
        blob = "\n".join(lines).strip()
        if len(blob) > max_chars:
            blob = blob[-max_chars:]
        return blob
