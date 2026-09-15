"""从框架 JSONL 汇总问答；按需读取核心 reqlog 的 LLM 输入。"""

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

TERMINAL = frozenset({"completed", "rate_limited", "busy"})
_DAY_FILE_RE = re.compile(r"^(\d{8})\.jsonl$")


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


def _shorten(token: str, *, keep: int = 8) -> str:
    t = (token or "").strip()
    if len(t) <= keep:
        return t
    return t[:keep] + "…"


def _label_from_real_key(real_key: str) -> str:
    label = str(real_key)
    if label.startswith("qq:"):
        return f"QQ {label[3:]}"
    if label.startswith("web:"):
        rest = label[4:]
        if rest.startswith("conv:"):
            parts = rest.split(":")
            if len(parts) >= 3:
                return f"Web {_shorten(parts[1])} · {_shorten(parts[-1])}"
        return f"Web {_shorten(rest)}"
    return label


def _load_proxy_inverse(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    users = data.get("users") or {}
    out: dict[str, str] = {}
    for real_key, logical in users.items():
        out[str(logical)] = _label_from_real_key(str(real_key))
    return out


def _conversation_from_address(
    addresses: dict[str, str], logical_address: str
) -> str:
    addr = str(logical_address or "").strip()
    if not addr:
        return ""
    for real_key, logical in addresses.items():
        if logical != addr:
            continue
        parts = str(real_key).split(":")
        if len(parts) >= 4 and parts[0] == "web" and parts[1] == "conv":
            return parts[-1]
        return ""
    return ""


def _load_addresses(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in (data.get("addresses") or {}).items()}


def _fmt_bj(ts: float) -> str:
    return datetime.fromtimestamp(ts, BJ).strftime("%m-%d %H:%M:%S")


def _date_bj(ts: float) -> str:
    return datetime.fromtimestamp(ts, BJ).strftime("%Y-%m-%d")


def _excerpt(content: str, *, max_len: int = 160) -> str:
    text = (content or "").replace("\r\n", "\n").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _status_zh(status: str, extra: dict[str, Any]) -> str:
    mapping = {
        "completed": "完成",
        "rate_limited": "频率过高",
        "busy": "忙",
    }
    if status == "completed" and extra.get("instant"):
        kind = extra.get("precheck")
        if kind == "too_short":
            return "预检·过短"
        if kind == "friend_add":
            return "预检·好友"
        if extra.get("topic_gate") == "reject" or extra.get("topic_gate_reject"):
            return "主题门控"
        return "即时回复"
    return mapping.get(status, status)


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


def list_log_dates(log_dir: Path) -> list[str]:
    """有框架日志的日期，降序，形如 YYYY-MM-DD。"""
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []
    days: list[str] = []
    for p in log_dir.glob("????????.jsonl"):
        m = _DAY_FILE_RE.match(p.name)
        if m:
            days.append(m.group(1))
    days.sort(reverse=True)
    return [date_display(d) for d in days]


def _day_paths(
    log_dir: Path, *, date_yyyymmdd: str | None, days: int
) -> list[Path]:
    if date_yyyymmdd:
        p = log_dir / f"{date_yyyymmdd}.jsonl"
        return [p] if p.is_file() else []
    if days <= 0:
        return sorted(log_dir.glob("????????.jsonl"), reverse=True)
    now = datetime.now(BJ)
    out: list[Path] = []
    for i in range(days):
        day = (now - timedelta(days=i)).strftime("%Y%m%d")
        p = log_dir / f"{day}.jsonl"
        if p.is_file():
            out.append(p)
    return out


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
    core_log_dir: str,
    reqlog_root: Path | None,
) -> Path | None:
    """定位一次请求的 reqlog 目录。优先相对挂载根，再试绝对路径。"""
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
        if _reqlog_dir_exists(p):
            return p
    return None


def load_recent_tasks(
    log_dir: Path,
    proxy_path: Path,
    *,
    limit: int = 80,
    days: int = 3,
    date: str | None = None,
    reqlog_root: Path | None = None,
) -> tuple[list[TaskRow], int]:
    """返回 (rows, total_matched)。date 为 YYYY-MM-DD 时只扫该日；否则扫近 days 天。"""
    log_dir = Path(log_dir)
    inv = _load_proxy_inverse(proxy_path)
    addresses = _load_addresses(proxy_path)
    date_key = _normalize_date(date)
    day_paths = _day_paths(log_dir, date_yyyymmdd=date_key, days=days)

    requests: dict[str, dict[str, Any]] = {}
    replies: dict[str, dict[str, Any]] = {}

    for path in day_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("kind")
            task_id = str(ev.get("task_id") or "")
            if not task_id:
                continue
            if kind == "request":
                prev = requests.get(task_id)
                if prev is None or float(ev.get("ts_unix") or 0) >= float(
                    prev.get("ts_unix") or 0
                ):
                    requests[task_id] = ev
            elif kind == "reply":
                status = str(ev.get("status") or "")
                if status not in TERMINAL:
                    continue
                prev = replies.get(task_id)
                if prev is None or float(ev.get("ts_unix") or 0) >= float(
                    prev.get("ts_unix") or 0
                ):
                    replies[task_id] = ev

    rows: list[TaskRow] = []
    for task_id, req in requests.items():
        rep = replies.get(task_id)
        if rep is None:
            continue
        ts = float(req.get("ts_unix") or rep.get("ts_unix") or 0)
        logical_user = str(req.get("logical_user") or rep.get("logical_user") or "")
        source = str(req.get("source") or "").strip() or "—"
        conversation_id = str(req.get("conversation_id") or "").strip()
        if not conversation_id:
            conversation_id = _conversation_from_address(
                addresses, str(req.get("logical_address") or "")
            )
        user_label = inv.get(logical_user, "")
        if not user_label:
            if source == "web" and logical_user:
                user_label = f"Web · {logical_user}"
            elif source == "qq" and logical_user:
                user_label = f"QQ · {logical_user}"
            else:
                user_label = logical_user or "未知"
        status = str(rep.get("status") or "")
        extra = rep.get("extra") if isinstance(rep.get("extra"), dict) else {}
        full = str(rep.get("content") or "").replace("\r\n", "\n").strip()
        core_rid = str(extra.get("core_request_id") or "").strip()
        core_dir = str(extra.get("core_log_dir") or "").strip()
        rows.append(
            TaskRow(
                task_id=task_id,
                time_bj=_fmt_bj(ts),
                ts_unix=ts,
                user_label=user_label,
                question=str(req.get("question") or "").strip(),
                status=status,
                status_zh=_status_zh(status, extra),
                result_full=full,
                result_excerpt=_excerpt(full),
                source=source,
                conversation_id=conversation_id,
                elapsed_sec=rep.get("elapsed_sec"),
                core_request_id=core_rid,
                core_log_dir=core_dir,
                has_llm=False,
                date_bj=_date_bj(ts),
            )
        )

    rows.sort(key=lambda r: r.ts_unix, reverse=True)
    total = len(rows)
    sliced = rows[:limit]
    for row in sliced:
        if not (row.core_request_id or row.core_log_dir):
            continue
        row.has_llm = (
            resolve_reqlog_dir(
                core_request_id=row.core_request_id,
                core_log_dir=row.core_log_dir,
                reqlog_root=reqlog_root,
            )
            is not None
        )
    return sliced, total


def _read_task_bundle(log_dir: Path, task_id: str) -> dict[str, Any] | None:
    path = Path(log_dir) / "by_id" / f"{task_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _core_ids_from_bundle(bundle: dict[str, Any]) -> tuple[str, str]:
    rid = ""
    log_dir = ""
    for ev in reversed(list(bundle.get("events") or [])):
        if not isinstance(ev, dict) or ev.get("kind") != "reply":
            continue
        extra = ev.get("extra") if isinstance(ev.get("extra"), dict) else {}
        rid = str(extra.get("core_request_id") or "").strip() or rid
        log_dir = str(extra.get("core_log_dir") or "").strip() or log_dir
        if rid or log_dir:
            break
    return rid, log_dir


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
    """从 content 中拆出 <think>/<thinking>/<reasoning> 块 → (cot, remainder)。"""
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
        # 输入侧若带 COT 字段也透出
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
    """返回 (cot_text, content_without_think_tags)。"""
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
    # 少数厂商把细节放在数组里
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
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in cot_parts:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return "\n\n".join(uniq), rest if tagged else content


def extract_llm_output(resp_payload: Any) -> dict[str, Any] | None:
    """从 reqlog 的 *_response.json / response.json 抽出助手输出与 COT。"""
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
    # 若 body 缺失但有 answer_text，仍展示
    if not content_clean and answer_text:
        content_clean = answer_text
    if not any([cot, content_clean, tool_calls, resp_payload.get("error"), answer_text]):
        # 空响应也返回骨架，便于前端标明「无输出」
        pass
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
    framework_log_dir: Path,
    task_id: str,
    reqlog_root: Path | None,
) -> dict[str, Any]:
    """读取该 task 对应的全部 LLM 请求输入与响应输出（含 COT）。"""
    tid = str(task_id or "").strip()
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return {"ok": False, "error": "无效 task_id", "calls": []}

    bundle = _read_task_bundle(framework_log_dir, tid)
    if bundle is None:
        return {"ok": False, "error": "找不到该任务日志", "calls": []}

    core_rid, core_dir = _core_ids_from_bundle(bundle)
    req_dir = resolve_reqlog_dir(
        core_request_id=core_rid,
        core_log_dir=core_dir,
        reqlog_root=reqlog_root,
    )
    if req_dir is None:
        return {
            "ok": True,
            "task_id": tid,
            "core_request_id": core_rid,
            "calls": [],
            "note": "无核心 reqlog（预检/门控/失败或未挂载）",
        }

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
        "core_request_id": core_rid or req_dir.name,
        "reqlog_dir": str(req_dir),
        "calls": calls,
    }
