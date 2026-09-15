"""一次请求的日志会话。

目录布局（log_root / request_id /）::

    summary.json          # 核心摘要：问题/答案/ok/用时/轮次/coverage…
    index.jsonl           # （可选）追加到 log_root/index.jsonl 的一行摘要
    find/
      detail.json         # 找侧全量（package + transcript + llm_calls + final_messages）
      llm/
        r001_request.json
        r001_response.json
        …
    answer/
      detail.json         # 答侧全量（package + glossary + messages + answer）
      llm/
        request.json
        response.json

设计原则：全量、不怕大；核心字段单独进 summary，便于扫目录。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("patchouli.reqlog")


def new_request_id() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:12]}"


def _json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _json_default(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "to_api_dict"):
        return o.to_api_dict()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    raise TypeError(f"not JSON serializable: {type(o)!r}")


@dataclass
class RequestLogSession:
    """一次请求的落盘句柄。log_root 为 None 时全部 no-op。"""

    log_root: Path | None
    request_id: str = field(default_factory=new_request_id)
    question: str = ""
    started_at: float = field(default_factory=time.time)
    started_mono: float = field(default_factory=time.monotonic)
    config_snapshot: dict[str, Any] | None = None

    # 运行中累积
    _find_summary: dict[str, Any] = field(default_factory=dict, repr=False)
    _answer_summary: dict[str, Any] = field(default_factory=dict, repr=False)
    _llm_seq_find: int = field(default=0, repr=False)
    _closed: bool = field(default=False, repr=False)

    @classmethod
    def create(
        cls,
        log_root: Path | None,
        *,
        question: str = "",
        request_id: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        sandbox_session_id: str | None = None,
    ) -> RequestLogSession:
        rid = request_id or new_request_id()
        if sandbox_session_id:
            # 让目录名带上沙箱 id，便于和旧 find 日志对照
            if not rid.endswith(sandbox_session_id):
                rid = f"{rid}_{sandbox_session_id}"
        sess = cls(
            log_root=None if log_root is None else Path(log_root).resolve(),
            request_id=rid,
            question=question,
            config_snapshot=config_snapshot,
        )
        if sess.log_root is not None:
            sess.dir.mkdir(parents=True, exist_ok=True)
            (sess.dir / "find" / "llm").mkdir(parents=True, exist_ok=True)
            (sess.dir / "answer" / "llm").mkdir(parents=True, exist_ok=True)
            _json_dump(
                sess.dir / "_meta.json",
                {
                    "request_id": sess.request_id,
                    "question": question,
                    "started_at": _iso(sess.started_at),
                    "config": config_snapshot,
                },
            )
        return sess

    @property
    def enabled(self) -> bool:
        return self.log_root is not None and not self._closed

    @property
    def dir(self) -> Path:
        if self.log_root is None:
            raise RuntimeError("log disabled")
        return self.log_root / "requests" / self.request_id

    @property
    def summary_path(self) -> Path | None:
        return None if self.log_root is None else self.dir / "summary.json"

    def record_find_llm_call(
        self,
        *,
        round_i: int,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        elapsed_sec: float,
        error: str | None = None,
        force_submit: bool = False,
        finish_reason: str | None = None,
    ) -> dict[str, Any]:
        """记录找侧一轮 LLM 全量输入输出。返回写入 llm_calls 的条目（含相对路径）。"""
        self._llm_seq_find += 1
        seq = self._llm_seq_find
        entry: dict[str, Any] = {
            "phase": "find",
            "seq": seq,
            "round": round_i,
            "force_submit": force_submit,
            "elapsed_sec": elapsed_sec,
            "finish_reason": finish_reason,
            "error": error,
            "request_path": None,
            "response_path": None,
        }
        if not self.enabled:
            entry["request"] = request
            entry["response"] = response
            return entry

        stem = f"r{seq:03d}_round{round_i:02d}"
        req_path = self.dir / "find" / "llm" / f"{stem}_request.json"
        resp_path = self.dir / "find" / "llm" / f"{stem}_response.json"
        _json_dump(req_path, request)
        _json_dump(
            resp_path,
            {
                "elapsed_sec": elapsed_sec,
                "finish_reason": finish_reason,
                "error": error,
                "body": response,
            },
        )
        entry["request_path"] = str(req_path)
        entry["response_path"] = str(resp_path)
        # detail 里仍内嵌一份，保证单文件即可还原
        entry["request"] = request
        entry["response"] = response
        return entry

    def record_answer_llm_call(
        self,
        *,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        elapsed_sec: float,
        error: str | None = None,
        answer_text: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "phase": "answer",
            "elapsed_sec": elapsed_sec,
            "error": error,
            "answer_text": answer_text,
            "request_path": None,
            "response_path": None,
            "request": request,
            "response": response,
        }
        if not self.enabled:
            return entry
        req_path = self.dir / "answer" / "llm" / "request.json"
        resp_path = self.dir / "answer" / "llm" / "response.json"
        _json_dump(req_path, request)
        _json_dump(
            resp_path,
            {
                "elapsed_sec": elapsed_sec,
                "error": error,
                "answer_text": answer_text,
                "body": response,
            },
        )
        entry["request_path"] = str(req_path)
        entry["response_path"] = str(resp_path)
        return entry

    def write_find_detail(
        self, payload: dict[str, Any], *, side: str | None = None
    ) -> str | None:
        """写找侧全量 detail；并缓存摘要字段。

        side 非空时写入 find/{side}/detail.json，并累积到 _find_summary["sides"]。
        side 为空时写入 find/detail.json（覆盖主摘要）。
        """
        summary = {
            "ok": payload.get("ok"),
            "stop_reason": payload.get("stop_reason"),
            "rounds": payload.get("rounds"),
            "tool_calls": payload.get("tool_calls"),
            "elapsed_sec": payload.get("elapsed_sec"),
            "session_id": payload.get("session_id"),
            "error": payload.get("error"),
            "coverage": (payload.get("package") or {}).get("coverage")
            if isinstance(payload.get("package"), dict)
            else None,
            "max_rounds": payload.get("max_rounds"),
            "submit_grace": payload.get("submit_grace"),
            "side": side,
        }
        if side:
            sides = dict(self._find_summary.get("sides") or {})
            sides[side] = summary
            # 聚合轮次/工具/耗时
            total_rounds = sum(int(s.get("rounds") or 0) for s in sides.values())
            total_tools = sum(int(s.get("tool_calls") or 0) for s in sides.values())
            total_elapsed = sum(float(s.get("elapsed_sec") or 0) for s in sides.values())
            self._find_summary = {
                **self._find_summary,
                "ok": all(bool(s.get("ok")) for s in sides.values()) if sides else False,
                "stop_reason": "compare",
                "rounds": total_rounds,
                "tool_calls": total_tools,
                "elapsed_sec": total_elapsed,
                "sides": sides,
                "coverage": payload.get("merged_coverage")
                or self._find_summary.get("coverage"),
            }
        else:
            self._find_summary = summary
        if not self.enabled:
            return None
        if side:
            path = self.dir / "find" / side / "detail.json"
        else:
            path = self.dir / "find" / "detail.json"
        _json_dump(path, payload)
        return str(path)

    def write_answer_detail(self, payload: dict[str, Any]) -> str | None:
        cov = payload.get("coverage")
        if cov is None and isinstance(payload.get("package"), dict):
            cov = payload["package"].get("coverage")
        self._answer_summary = {
            "ok": payload.get("ok"),
            "error": payload.get("error"),
            "elapsed_sec": payload.get("elapsed_sec"),
            "coverage": cov,
            "evidence_count": payload.get("evidence_count"),
        }
        if not self.enabled:
            return None
        path = self.dir / "answer" / "detail.json"
        _json_dump(path, payload)
        return str(path)

    def finalize(
        self,
        *,
        ok: bool,
        answer: str,
        elapsed_sec: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        """写 summary.json，并追加 index.jsonl。返回 summary 路径。"""
        if self.log_root is None:
            self._closed = True
            return None

        finished = time.time()
        elapsed = (
            elapsed_sec
            if elapsed_sec is not None
            else (time.monotonic() - self.started_mono)
        )
        summary: dict[str, Any] = {
            "request_id": self.request_id,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(finished),
            "elapsed_sec": elapsed,
            "ok": ok,
            "question": self.question,
            "answer": answer,
            "find": dict(self._find_summary),
            "answer_meta": dict(self._answer_summary),
            "config": self.config_snapshot,
            "paths": {
                "dir": str(self.dir),
                "summary": str(self.dir / "summary.json"),
                "find_detail": str(self.dir / "find" / "detail.json"),
                "answer_detail": str(self.dir / "answer" / "detail.json"),
                "find_llm_dir": str(self.dir / "find" / "llm"),
                "answer_llm_dir": str(self.dir / "answer" / "llm"),
            },
        }
        if extra:
            summary["extra"] = extra

        path = self.dir / "summary.json"
        try:
            _json_dump(path, summary)
            self._append_index(summary)
        except OSError as e:
            log.warning("finalize request log failed: %s", e)
            self._closed = True
            return None

        self._closed = True
        return str(path)

    def _append_index(self, summary: dict[str, Any]) -> None:
        """一行一条摘要，方便扫一天的请求。"""
        assert self.log_root is not None
        index_path = self.log_root / "index.jsonl"
        lean = {
            "request_id": summary["request_id"],
            "started_at": summary["started_at"],
            "finished_at": summary["finished_at"],
            "elapsed_sec": summary["elapsed_sec"],
            "ok": summary["ok"],
            "question": summary["question"],
            "answer": summary["answer"],
            "find_ok": (summary.get("find") or {}).get("ok"),
            "stop_reason": (summary.get("find") or {}).get("stop_reason"),
            "rounds": (summary.get("find") or {}).get("rounds"),
            "tool_calls": (summary.get("find") or {}).get("tool_calls"),
            "coverage": (summary.get("find") or {}).get("coverage"),
            "answer_ok": (summary.get("answer_meta") or {}).get("ok"),
            "dir": (summary.get("paths") or {}).get("dir"),
        }
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(lean, ensure_ascii=False, default=_json_default) + "\n")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
