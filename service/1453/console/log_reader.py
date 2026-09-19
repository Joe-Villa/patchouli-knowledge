"""从 log/1836 reqlog（index.jsonl + requests/）汇总问答；按需读 LLM 输入输出。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("console.log_reader")

BJ = timezone(timedelta(hours=8))


@dataclass
class TaskRow:
    task_id: str
    time_bj: str
    ts_unix: float
    user_label: str
    question: str
    status: str
    status_zh: str
    result_full: str
    result_excerpt: str
    source: str = ""
    conversation_id: str = ""
    elapsed_sec: float | None = None
    core_request_id: str = ""
    core_log_dir: str = ""
    has_llm: bool = False
    date_bj: str = ""  # YYYY-MM-DD


def _fmt_bj(ts: float) -> str:
    return datetime.fromtimestamp(ts, BJ).strftime("%m-%d %H:%M:%S")


def _date_bj(ts: float) -> str:
    return datetime.fromtimestamp(ts, BJ).strftime("%Y-%m-%d")


def _excerpt(content: str, *, max_len: int = 160) -> str:
    text = (content or "").replace("\r\n", "\n").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# web/ask 会把会话记忆拼进 question；控制台列表只展示真实当前问句。
_CURRENT_QUESTION_MARKERS = (
    "【当前问题】",
    "当前问题：",
    "当前问题:",
)


def display_question(raw: str) -> str:
    """去掉指代消解记忆前缀，返回用户当前问题。"""
    text = (raw or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    best = -1
    marker_len = 0
    for marker in _CURRENT_QUESTION_MARKERS:
        idx = text.rfind(marker)
        if idx > best:
            best = idx
            marker_len = len(marker)
    if best >= 0:
        return text[best + marker_len :].strip()
    return text


def _normalize_date(raw: str | None) -> str | None:
    """返回 YYYYMMDD；空则 None。接受 YYYY-MM-DD / YYYYMMDD。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in {"all", "*", "全部"}:
        return None
    s = s.replace("-", "")
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError("无效日期，请用 YYYY-MM-DD")
    return s


def date_display(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _parse_started_at(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    # 常见：2026-09-15T16:13:01+0800 / +08:00 / Z
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-5] + s[-5:-2] + ":" + s[-2:]
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ)
    return dt.astimezone(BJ)


def _status_from_index(rec: dict[str, Any]) -> tuple[str, str]:
    ok = rec.get("ok")
    if ok is True:
        return "completed", "完成"
    if ok is False:
        return "failed", "失败"
    stop = str(rec.get("stop_reason") or "").strip()
    if stop:
        return stop, stop
    return "unknown", "未知"


def _reqlog_dir_exists(req_dir: Path) -> bool:
    if not req_dir.is_dir():
        return False
    find_llm = req_dir / "find" / "llm"
    answer_llm = req_dir / "answer" / "llm"
    if find_llm.is_dir() and any(find_llm.glob("*_request.json")):
        return True
    if answer_llm.is_dir() and (answer_llm / "request.json").is_file():
        return True
    return False


def resolve_reqlog_dir(
    *,
    core_request_id: str,
    core_log_dir: str = "",
    reqlog_root: Path | None,
) -> Path | None:
    """定位一次请求的 reqlog 目录。优先 reqlog_root/requests/{id}。"""
    rid = (core_request_id or "").strip()
    raw = (core_log_dir or "").strip()
    candidates: list[Path] = []
    if reqlog_root is not None:
        root = Path(reqlog_root)
        if rid:
            candidates.append(root / "requests" / rid)
            candidates.append(root / rid)
        if raw:
            name = Path(raw).name
            if name:
                candidates.append(root / "requests" / name)
                candidates.append(root / name)
    if raw:
        candidates.append(Path(raw))
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            return p
    return None


def _iter_index_records(reqlog_root: Path) -> list[dict[str, Any]]:
    path = Path(reqlog_root) / "index.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("request_id"):
            out.append(rec)
    return out


def list_log_dates(reqlog_root: Path) -> list[str]:
    """有 reqlog 记录的日期，降序，形如 YYYY-MM-DD。"""
    days: set[str] = set()
    for rec in _iter_index_records(reqlog_root):
        dt = _parse_started_at(str(rec.get("started_at") or ""))
        if dt is None:
            rid = str(rec.get("request_id") or "")
            m = re.match(r"^(\d{8})_", rid)
            if m:
                days.add(m.group(1))
            continue
        days.add(dt.strftime("%Y%m%d"))
    ordered = sorted(days, reverse=True)
    return [date_display(d) for d in ordered]


def load_recent_tasks(
    reqlog_root: Path,
    *,
    limit: int = 80,
    days: int = 0,
    date: str | None = None,
) -> tuple[list[TaskRow], int]:
    """从 index.jsonl 列问答任务。date=YYYY-MM-DD 时只保留该日。"""
    root = Path(reqlog_root)
    date_key = _normalize_date(date)
    now = datetime.now(BJ)
    min_ts: float | None = None
    if date_key is None and days > 0:
        min_ts = (now - timedelta(days=days)).timestamp()

    rows: list[TaskRow] = []
    for rec in _iter_index_records(root):
        rid = str(rec.get("request_id") or "").strip()
        if not rid:
            continue
        dt = _parse_started_at(str(rec.get("started_at") or ""))
        if dt is None:
            m = re.match(r"^(\d{8})_", rid)
            if m:
                try:
                    dt = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=BJ)
                except ValueError:
                    dt = None
        if dt is None:
            continue
        day = dt.strftime("%Y%m%d")
        if date_key is not None and day != date_key:
            continue
        ts = dt.timestamp()
        if min_ts is not None and ts < min_ts:
            continue

        status, status_zh = _status_from_index(rec)
        answer = str(rec.get("answer") or "").replace("\r\n", "\n").strip()
        elapsed = rec.get("elapsed_sec")
        try:
            elapsed_f = float(elapsed) if elapsed is not None else None
        except (TypeError, ValueError):
            elapsed_f = None
        req_dir = resolve_reqlog_dir(
            core_request_id=rid,
            core_log_dir=str(rec.get("dir") or ""),
            reqlog_root=root,
        )
        source = str(rec.get("source") or "").strip() or "web"
        rows.append(
            TaskRow(
                task_id=rid,
                time_bj=_fmt_bj(ts),
                ts_unix=ts,
                user_label="Web",
                question=display_question(str(rec.get("question") or "")),
                status=status,
                status_zh=status_zh,
                result_full=answer,
                result_excerpt=_excerpt(answer),
                source=source,
                conversation_id=str(rec.get("conversation_id") or "").strip(),
                elapsed_sec=elapsed_f,
                core_request_id=rid,
                core_log_dir=str(req_dir) if req_dir else "",
                has_llm=_reqlog_dir_exists(req_dir) if req_dir else False,
                date_bj=_date_bj(ts),
            )
        )

    rows.sort(key=lambda r: r.ts_unix, reverse=True)
    total = len(rows)
    return rows[: max(1, limit)], total


def _safe_load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if "text" in block:
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


_THINK_TAG_RE = re.compile(
    r"<(think|thinking|reasoning)>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)


def _split_think_tags(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    chunks: list[str] = []

    def _keep(m: re.Match[str]) -> str:
        chunks.append((m.group(2) or "").strip())
        return ""

    rest = _THINK_TAG_RE.sub(_keep, text).strip()
    return "\n\n".join(c for c in chunks if c), rest


def _summarize_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        text = _content_to_text(m.get("content"))
        item: dict[str, Any] = {"role": role, "content": text}
        for key in ("reasoning_content", "reasoning", "thinking"):
            if m.get(key):
                item[key] = _content_to_text(m.get(key))
        if m.get("tool_calls"):
            item["tool_calls"] = m.get("tool_calls")
        if m.get("name"):
            item["name"] = m.get("name")
        if m.get("tool_call_id"):
            item["tool_call_id"] = m.get("tool_call_id")
        out.append(item)
    return out


def _message_from_response_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            return msg
    msg = body.get("message")
    return msg if isinstance(msg, dict) else {}


def _collect_cot_parts(msg: dict[str, Any], content: str) -> tuple[str, str]:
    cot_parts: list[str] = []
    for key in (
        "reasoning_content",
        "reasoning",
        "thinking",
        "reasoning_text",
        "cot",
    ):
        raw = msg.get(key)
        if raw is None or raw == "":
            continue
        text = _content_to_text(raw).strip()
        if text:
            cot_parts.append(text)
    details = msg.get("reasoning_details")
    if isinstance(details, list):
        for block in details:
            if isinstance(block, dict):
                t = _content_to_text(block.get("text") or block.get("content") or "")
            else:
                t = str(block)
            t = t.strip()
            if t:
                cot_parts.append(t)
    tagged, rest = _split_think_tags(content)
    if tagged:
        cot_parts.append(tagged)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in cot_parts:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return "\n\n".join(uniq), rest if tagged else content


def extract_llm_output(resp_payload: Any) -> dict[str, Any] | None:
    if not isinstance(resp_payload, dict):
        return None
    body = resp_payload.get("body")
    msg = _message_from_response_body(body)
    content = _content_to_text(msg.get("content")) if msg else ""
    cot, content_clean = _collect_cot_parts(msg, content) if msg else ("", content)
    tool_calls = msg.get("tool_calls") if msg else None
    answer_text = resp_payload.get("answer_text")
    if answer_text is not None:
        answer_text = str(answer_text)
    if not content_clean and answer_text:
        content_clean = answer_text
    finish = resp_payload.get("finish_reason")
    if finish is None and isinstance(body, dict):
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            finish = choices[0].get("finish_reason")
    model = None
    if isinstance(body, dict):
        model = body.get("model")
    return {
        "role": str(msg.get("role") or "assistant") if msg else "assistant",
        "content": content_clean,
        "cot": cot,
        "tool_calls": tool_calls if tool_calls else None,
        "answer_text": answer_text,
        "finish_reason": finish,
        "elapsed_sec": resp_payload.get("elapsed_sec"),
        "error": resp_payload.get("error"),
        "model": model,
    }


def _pair_call(
    *,
    stage: str,
    name: str,
    req_path: Path,
    resp_path: Path | None,
) -> dict[str, Any] | None:
    payload = _safe_load_json(req_path)
    if not isinstance(payload, dict):
        return None
    output = None
    resp_file = ""
    if resp_path is not None and resp_path.is_file():
        resp_payload = _safe_load_json(resp_path)
        output = extract_llm_output(resp_payload)
        resp_file = resp_path.name
    return {
        "stage": stage,
        "name": name,
        "file": req_path.name,
        "response_file": resp_file,
        "model": payload.get("model") or (output or {}).get("model"),
        "temperature": payload.get("temperature"),
        "tool_choice": payload.get("tool_choice"),
        "messages": _summarize_messages(payload.get("messages")),
        "output": output,
        "raw": payload,
    }


def load_llm_calls(
    *,
    task_id: str,
    reqlog_root: Path,
) -> dict[str, Any]:
    """读取该 request_id 下全部 LLM 请求/响应（含 COT）。"""
    tid = str(task_id or "").strip()
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return {"ok": False, "error": "无效 task_id", "calls": []}

    root = Path(reqlog_root)
    req_dir = resolve_reqlog_dir(core_request_id=tid, reqlog_root=root)
    if req_dir is None:
        return {"ok": False, "error": "找不到该任务日志", "calls": []}

    calls: list[dict[str, Any]] = []

    find_llm = req_dir / "find" / "llm"
    if find_llm.is_dir():
        for path in sorted(find_llm.glob("*_request.json")):
            stem = path.name[: -len("_request.json")]
            resp = find_llm / f"{stem}_response.json"
            entry = _pair_call(
                stage="find",
                name=path.stem,
                req_path=path,
                resp_path=resp,
            )
            if entry:
                calls.append(entry)

    answer_req = req_dir / "answer" / "llm" / "request.json"
    if answer_req.is_file():
        entry = _pair_call(
            stage="answer",
            name="answer",
            req_path=answer_req,
            resp_path=req_dir / "answer" / "llm" / "response.json",
        )
        if entry:
            calls.append(entry)

    return {
        "ok": True,
        "task_id": tid,
        "core_request_id": tid,
        "reqlog_dir": str(req_dir),
        "calls": calls,
    }
