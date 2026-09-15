"""找侧 ReAct：工具循环 → EvidencePackage。"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_FIND_DIR = Path(__file__).resolve().parent


def _ensure_paths() -> None:
    # vendor 目录扁平导入：client / evidence / sandbox / tools …
    fd = str(_FIND_DIR)
    if fd in sys.path:
        sys.path.remove(fd)
    sys.path.insert(0, fd)


_ensure_paths()

from client import ChatMessage, LLMConfig, chat, load_llm_config  # noqa: E402
from evidence import (  # noqa: E402
    COMPUTATION_ENTITY_TYPE,
    EvidenceItem,
    EvidencePackage,
    GapItem,
    ResolvedEntity,
)
from read_block import read_block  # noqa: E402
from sandbox import SandboxSession  # noqa: E402

from find_prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from tools import (  # noqa: E402
    TOOL_SCHEMAS,
    ToolContext,
    dumps_tool_result,
    make_dispatcher,
)

log = logging.getLogger("patchouli.find.agent")

ProgressCallback = Callable[[dict[str, Any]], None]

DEFAULT_GAME = _FIND_DIR  # 调用方应显式传入 game_root；无默认全量树
DEFAULT_LOG_DIR = _FIND_DIR / "logs"
DEFAULT_MAX_ROUNDS = 12
DEFAULT_SUBMIT_GRACE = 3  # 进入强制提交后的额外轮次
DEFAULT_MAX_WALL_SEC = 180.0

_SUBMIT_TOOL = "submit_evidence_package"
_FORCE_SUBMIT_CHOICE = {
    "type": "function",
    "function": {"name": _SUBMIT_TOOL},
}


@dataclass
class FindResult:
    ok: bool
    package: EvidencePackage
    question: str
    rounds: int = 0
    tool_calls: int = 0
    elapsed_sec: float = 0.0
    stop_reason: str = ""
    session_id: str | None = None
    log_path: str | None = None
    error: str | None = None
    transcript: list[dict] = field(default_factory=list)


def find_evidence(
    question: str,
    *,
    game_root: Path | None = None,
    loc_db: Path | None = None,
    llm_config: LLMConfig | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    submit_grace: int = DEFAULT_SUBMIT_GRACE,
    max_wall_sec: float = DEFAULT_MAX_WALL_SEC,
    log_dir: Path | None = DEFAULT_LOG_DIR,
    lang: str = "simp_chinese",
    sessions_root: Path | None = None,
    request_log: Any = None,
    corpus_note: str | None = None,
    on_progress: ProgressCallback | None = None,
    progress_side_index: int = 0,
    progress_side_count: int = 1,
    progress_side_label: str | None = None,
    mods_roots: dict[str, Path] | None = None,
    visible_mod_ids: frozenset[str] | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    tool_schemas: list[dict] | None = None,
) -> FindResult:
    q = (question or "").strip()
    if not q:
        raise ValueError("question 为空")

    game = Path(game_root or DEFAULT_GAME).resolve()
    loc = Path(loc_db or (game / "localization.sqlite"))
    t0 = time.monotonic()
    session = SandboxSession.create(
        game_root=game,
        sessions_root=sessions_root,
        mods_roots=mods_roots,
    )
    visible = frozenset(visible_mod_ids or (mods_roots or {}).keys())
    ctx = ToolContext(
        game_root=game,
        loc_db=loc,
        sandbox=session,
        lang=lang,
        mods_root=session.mods_view,
        visible_mod_ids=visible,
    )
    dispatch = make_dispatcher(ctx)
    hard_cap = max_rounds + max(0, submit_grace)

    sys_content = (system_prompt if system_prompt is not None else SYSTEM_PROMPT)
    user_content = (
        user_prompt
        if user_prompt is not None
        else build_user_prompt(q, corpus_note=corpus_note)
    )
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=sys_content),
        ChatMessage(role="user", content=user_content),
    ]
    transcript: list[dict] = []
    llm_calls: list[dict] = []
    tool_calls_n = 0
    submitted: EvidencePackage | None = None
    stop_reason = ""
    error: str | None = None
    rounds_done = 0
    cfg = llm_config or load_llm_config()
    active_tools = tool_schemas if tool_schemas is not None else TOOL_SCHEMAS

    def _fire_progress(*, round_i: int, tool: str | None = None) -> None:
        if on_progress is None:
            return
        try:
            on_progress(
                {
                    "phase": "find",
                    "round": round_i,
                    "max_rounds": max_rounds,
                    "tool": tool,
                    "side_index": progress_side_index,
                    "side_count": progress_side_count,
                    "side_label": progress_side_label,
                }
            )
        except Exception:
            log.debug("on_progress failed", exc_info=True)

    try:
        for round_i in range(1, hard_cap + 1):
            if time.monotonic() - t0 > max_wall_sec:
                stop_reason = "wall_timeout"
                break

            force_submit = round_i >= max_rounds
            choice: Any = _FORCE_SUBMIT_CHOICE if force_submit else "auto"
            _fire_progress(round_i=min(round_i, max_rounds))

            api_messages = [m.to_api_dict() for m in messages]
            llm_request = {
                "model": cfg.model,
                "temperature": 0.15,
                "tools": active_tools,
                "tool_choice": choice,
                "messages": api_messages,
            }
            t_llm = time.monotonic()
            try:
                result = chat(
                    messages,
                    config=cfg,
                    temperature=0.15,
                    tools=active_tools,
                    tool_choice=choice,
                )
                llm_err = None
            except Exception as exc:
                log.exception("find LLM call failed")
                error = f"{type(exc).__name__}: {exc}"
                stop_reason = "llm_error"
                llm_err = error
                call_entry = {
                    "phase": "find",
                    "round": round_i,
                    "force_submit": force_submit,
                    "elapsed_sec": time.monotonic() - t_llm,
                    "finish_reason": None,
                    "error": llm_err,
                    "request": llm_request,
                    "response": None,
                }
                if request_log is not None:
                    call_entry = request_log.record_find_llm_call(
                        round_i=round_i,
                        request=llm_request,
                        response=None,
                        elapsed_sec=call_entry["elapsed_sec"],
                        error=llm_err,
                        force_submit=force_submit,
                    )
                llm_calls.append(call_entry)
                break

            llm_elapsed = time.monotonic() - t_llm
            call_entry = {
                "phase": "find",
                "round": round_i,
                "force_submit": force_submit,
                "elapsed_sec": llm_elapsed,
                "finish_reason": result.finish_reason,
                "error": None,
                "request": llm_request,
                "response": result.raw,
            }
            if request_log is not None:
                call_entry = request_log.record_find_llm_call(
                    round_i=round_i,
                    request=llm_request,
                    response=result.raw,
                    elapsed_sec=llm_elapsed,
                    force_submit=force_submit,
                    finish_reason=result.finish_reason,
                )
            llm_calls.append(call_entry)

            rounds_done = round_i
            asst = result.message
            messages.append(asst)
            transcript.append(
                {
                    "round": round_i,
                    "role": "assistant",
                    "force_submit": force_submit,
                    "content": asst.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in (asst.tool_calls or [])
                    ],
                    "finish_reason": result.finish_reason,
                    "llm_elapsed_sec": llm_elapsed,
                }
            )

            if not asst.tool_calls:
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "必须调用工具。若证据已够或已到截止轮次，立刻调用 "
                            f"{_SUBMIT_TOOL}（合法 JSON；items 只含 path+key；"
                            "why/notes 禁止未转义双引号）。"
                            if force_submit
                            else (
                                "你必须使用工具查找，并在结束时调用 "
                                f"{_SUBMIT_TOOL}。不要只输出文字。"
                            )
                        ),
                    )
                )
                if force_submit and round_i >= hard_cap:
                    stop_reason = "no_tool_calls"
                    break
                continue

            submit_payload: dict | None = None
            submit_parse_error: str | None = None
            non_submit_in_force = False

            for tc in asst.tool_calls:
                tool_calls_n += 1
                name = tc.name
                args, parse_err = _parse_tool_arguments(tc.arguments or "")

                # 强制提交阶段：拒绝一切非 submit 工具
                if force_submit and name != _SUBMIT_TOOL:
                    non_submit_in_force = True
                    _append_tool(
                        messages,
                        transcript,
                        round_i,
                        tc.id,
                        name,
                        args or {},
                        {
                            "ok": False,
                            "error": "submit_only_phase",
                            "hint": (
                                f"已进入强制提交阶段（round>={max_rounds}）。"
                                f"只允许调用 {_SUBMIT_TOOL}；"
                                "items 用 path+key，不要再 read/grep。"
                            ),
                        },
                    )
                    continue

                if args is None:
                    obs: Any = {
                        "ok": False,
                        "error": "invalid_json_arguments",
                        "detail": parse_err,
                        "hint": (
                            "arguments 必须是合法 JSON。"
                            "若是 submit：删掉 why/notes 里的英文双引号，改用「」或省略；"
                            "items 只保留 path/key/role。"
                        ),
                        "raw_preview": (tc.arguments or "")[:800],
                    }
                    _append_tool(
                        messages, transcript, round_i, tc.id, name, {}, obs
                    )
                    if name == _SUBMIT_TOOL:
                        submit_parse_error = parse_err or "invalid_json_arguments"
                    continue

                if name == _SUBMIT_TOOL:
                    submit_payload = args
                    obs = {"ok": True, "accepted": True, "message": "package received"}
                    _append_tool(
                        messages, transcript, round_i, tc.id, name, args, obs
                    )
                    continue

                fn = dispatch.get(name)
                if fn is None:
                    obs = {"ok": False, "error": f"unknown_tool:{name}"}
                else:
                    try:
                        obs = fn(args)
                    except Exception as exc:
                        log.exception("tool %s failed", name)
                        obs = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                _append_tool(messages, transcript, round_i, tc.id, name, args, obs)

            tool_names = [
                tc.name
                for tc in (asst.tool_calls or [])
                if tc.name and tc.name != _SUBMIT_TOOL
            ]
            if tool_names:
                _fire_progress(
                    round_i=min(round_i, max_rounds),
                    tool="+".join(tool_names[:3]),
                )

            if submit_payload is not None:
                try:
                    submitted = _hydrate_submit(
                        submit_payload,
                        question=q,
                        game=game,
                        mods_root=session.mods_view,
                        visible_mod_ids=visible,
                    )
                    stop_reason = "submitted"
                except Exception as exc:
                    error = f"hydrate_failed: {exc}"
                    stop_reason = "hydrate_error"
                    submitted = EvidencePackage(
                        coverage="empty",
                        question=q,
                        unresolved=[GapItem(reason=str(exc))],
                        notes="submit hydrate failed",
                    )
                break

            # 未成功提交：催促 / 纠错
            if force_submit:
                if submit_parse_error:
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                f"{_SUBMIT_TOOL} 的 JSON 解析失败（{submit_parse_error}）。"
                                "请立刻重新调用，只交："
                                '{"coverage":"...","items":[{"path":"...","key":"...","role":"primary"}],'
                                '"resolved_entities":[{"query":"...","key":"..."}],'
                                '"unresolved":[],"notes":null}。'
                                "禁止在字符串值里使用未转义的 \" ；why 可省略。"
                            ),
                        )
                    )
                elif non_submit_in_force:
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                f"禁止再调用查找工具。下一动作只能是 {_SUBMIT_TOOL}。"
                                "把已读到的最终块 path+key 放进 items；"
                                "不够就 coverage=partial/empty/ambiguous。"
                            ),
                        )
                    )
                else:
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                f"请立即调用 {_SUBMIT_TOOL} 结束。"
                                f"（强制提交阶段 {round_i}/{hard_cap}）"
                            ),
                        )
                    )
                if round_i >= hard_cap:
                    stop_reason = "max_rounds"
                    break
                continue

            if round_i >= max_rounds - 1:
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            f"已用 {round_i}/{max_rounds} 轮。下一动作必须调用 "
                            f"{_SUBMIT_TOOL}（哪怕 coverage=partial/empty/ambiguous）。"
                            "items 只含 path+key；why/notes 禁止未转义双引号。"
                        ),
                    )
                )
        else:
            stop_reason = stop_reason or "max_rounds"

        if submitted is None:
            submitted = EvidencePackage(
                coverage="empty",
                question=q,
                unresolved=[
                    GapItem(
                        reason=f"find 未提交证据包（stop={stop_reason}）",
                        query=q,
                    )
                ],
                notes=f"auto-empty stop_reason={stop_reason}",
            )

        elapsed = time.monotonic() - t0
        result_obj = FindResult(
            ok=stop_reason == "submitted" and error is None,
            package=submitted,
            question=q,
            rounds=rounds_done,
            tool_calls=tool_calls_n,
            elapsed_sec=elapsed,
            stop_reason=stop_reason,
            session_id=session.session_id,
            error=error,
            transcript=transcript,
        )

        if log_dir is not None or request_log is not None:
            detail = {
                "question": q,
                "ok": result_obj.ok,
                "stop_reason": stop_reason,
                "error": error,
                "elapsed_sec": elapsed,
                "rounds": result_obj.rounds,
                "tool_calls": tool_calls_n,
                "session_id": session.session_id,
                "max_rounds": max_rounds,
                "submit_grace": submit_grace,
                "package": submitted.to_dict(),
                "transcript": transcript,
                "llm_calls": llm_calls,
                "final_messages": [m.to_api_dict() for m in messages],
            }
            try:
                if request_log is not None:
                    result_obj.log_path = request_log.write_find_detail(detail)
                if log_dir is not None:
                    # 兼容旧路径：find/logs/find_*.json（现亦含全量）
                    legacy = _write_log(detail, Path(log_dir))
                    if result_obj.log_path is None:
                        result_obj.log_path = str(legacy)
            except OSError as e:
                log.warning("find log failed: %s", e)

        return result_obj
    finally:
        try:
            session.destroy()
        except Exception:
            pass


def _parse_tool_arguments(raw: str) -> tuple[dict | None, str | None]:
    """解析工具 arguments；对 submit 常见坏 JSON 做修复/骨架恢复。"""
    s = (raw or "").strip()
    if not s:
        return None, "empty_arguments"

    obj, err = _try_json_object(s)
    if obj is not None:
        return obj, None

    repaired = _repair_json_string(s)
    if repaired != s:
        obj, err2 = _try_json_object(repaired)
        if obj is not None:
            return obj, None
        err = err2 or err

    recovered = _recover_submit_skeleton(s)
    if recovered is not None:
        return recovered, None

    return None, err or "invalid_json"


def _try_json_object(s: str) -> tuple[dict | None, str | None]:
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        return None, f"{e.msg} at pos {e.pos}"
    if not isinstance(obj, dict):
        return None, "not_object"
    return obj, None


def _repair_json_string(s: str) -> str:
    """常见修复：弯引号、字符串值内未转义的直双引号。"""
    out = (
        s.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    # 在 JSON 字符串值内部，把未转义 " 改成 '
    return _escape_inner_quotes(out)


def _escape_inner_quotes(s: str) -> str:
    """状态机：键与结构上的引号保留；值内部的裸 " → '。"""
    chars: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if escape:
            chars.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string:
            chars.append(ch)
            escape = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                chars.append(ch)
                i += 1
                continue
            # 可能是字符串结束，或值内非法引号
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",}]:" :
                in_string = False
                chars.append(ch)
            else:
                chars.append("'")
            i += 1
            continue
        chars.append(ch)
        i += 1
    return "".join(chars)


def _recover_submit_skeleton(raw: str) -> dict | None:
    """从损坏的 submit arguments 里捞 coverage + path/key 列表。"""
    if "coverage" not in raw and "items" not in raw:
        return None
    cov_m = re.search(r'"coverage"\s*:\s*"(sufficient|partial|empty|ambiguous)"', raw)
    items: list[dict] = []
    for m in re.finditer(
        r'"path"\s*:\s*"([^"\\]+)"\s*,\s*"key"\s*:\s*"([^"\\]+)"',
        raw,
    ):
        items.append({"path": m.group(1), "key": m.group(2), "role": "primary"})
    if not items:
        for m in re.finditer(
            r'"key"\s*:\s*"([^"\\]+)"\s*,\s*"path"\s*:\s*"([^"\\]+)"',
            raw,
        ):
            items.append({"path": m.group(2), "key": m.group(1), "role": "primary"})
    if not cov_m and not items:
        return None
    out: dict[str, Any] = {
        "coverage": cov_m.group(1) if cov_m else ("partial" if items else "empty"),
        "items": items,
        "notes": "recovered_from_broken_submit_json",
    }
    # resolved_entities 尽力捞
    ents: list[dict] = []
    for m in re.finditer(
        r'"query"\s*:\s*"([^"\\]+)"\s*,\s*"key"\s*:\s*"([^"\\]+)"',
        raw,
    ):
        ents.append({"query": m.group(1), "key": m.group(2)})
    if ents:
        out["resolved_entities"] = ents
    return out


def _append_tool(
    messages: list[ChatMessage],
    transcript: list[dict],
    round_i: int,
    tool_call_id: str,
    name: str,
    args: dict,
    obs: Any,
) -> None:
    content = dumps_tool_result(obs)
    messages.append(
        ChatMessage(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
    )
    transcript.append(
        {
            "round": round_i,
            "role": "tool",
            "name": name,
            "tool_call_id": tool_call_id,
            "arguments": args,
            "result": content,
        }
    )


def _hydrate_submit(
    payload: dict,
    *,
    question: str,
    game: Path,
    mods_root: Path | None = None,
    visible_mod_ids: frozenset[str] | None = None,
) -> EvidencePackage:
    items_raw = payload.get("items") or []
    items: list[EvidenceItem] = []
    read_failures: list[GapItem] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        key = str(raw.get("key") or "").strip()
        depth = str(raw.get("depth") or "top")
        text = str(raw.get("text") or "")
        entity_type = raw.get("entity_type")
        meta = {
            "role": raw.get("role") or "support",
            "entity_type": entity_type,
            "why": raw.get("why"),
            "label": raw.get("label"),
        }
        # 计算结果 / 显式正文：直接入包（不必 path+key）
        if text.strip():
            if not path and str(entity_type or "") == COMPUTATION_ENTITY_TYPE:
                path = "run_code"
            if not key and str(entity_type or "") == COMPUTATION_ENTITY_TYPE:
                key = "computation_result"
            if str(entity_type or "") == COMPUTATION_ENTITY_TYPE and meta["role"] == "support":
                meta["role"] = "primary"
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path or "run_code",
                        "key": key or None,
                        "text": text,
                        "start_line": raw.get("start_line"),
                        "end_line": raw.get("end_line"),
                        **meta,
                    }
                )
            )
            continue
        if not path or not key:
            continue
        # pool：mods/<id>/... 用该模组根作 game_root
        read_root = game
        read_rel = path
        if path.startswith("mods/") or path.startswith("/mods/"):
            p = path.lstrip("/")
            rest = p[5:] if p.startswith("mods/") else p
            parts = rest.split("/", 1)
            mid = parts[0].strip()
            if not mid or mid not in (visible_mod_ids or frozenset()):
                read_failures.append(
                    GapItem(
                        reason=f"read_block 模组不在池内: {mid}",
                        key=key,
                        query=path,
                    )
                )
                continue
            if mods_root is None:
                read_failures.append(
                    GapItem(reason="mods_not_mounted", key=key, query=path)
                )
                continue
            read_root = (Path(mods_root) / mid).resolve()
            read_rel = parts[1] if len(parts) > 1 else ""
            if not read_rel:
                read_failures.append(
                    GapItem(reason="need_file_path_under_mod", key=key, query=path)
                )
                continue
        block = read_block(read_rel, key, depth_mode=depth, game_root=read_root)
        if not block.get("ok"):
            read_failures.append(
                GapItem(
                    reason=f"read_block 未取到正文: {block.get('error')}",
                    key=key,
                    query=path,
                )
            )
            continue
        if block.get("ambiguous"):
            hit = (block.get("hits") or [{}])[0]
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path if path.startswith("mods/") else (hit.get("path") or path),
                        "key": key,
                        "text": hit.get("text") or "",
                        "start_line": hit.get("start_line"),
                        "end_line": hit.get("end_line"),
                        **meta,
                    }
                )
            )
        else:
            items.append(
                EvidenceItem.from_dict(
                    {
                        "path": path if path.startswith("mods/") else (block.get("path") or path),
                        "key": key,
                        "text": block.get("text") or "",
                        "start_line": block.get("start_line"),
                        "end_line": block.get("end_line"),
                        **meta,
                    }
                )
            )

    notes = str(payload["notes"]) if payload.get("notes") is not None else None
    notes_s = (notes or "").strip()
    cov = str(payload.get("coverage") or "partial")
    text_items = [i for i in items if i.text.strip()]

    # 算过了却只写在 notes / path+key 全失败：回填为 computation，避免答侧 empty 拒答
    if not text_items and notes_s and cov in ("sufficient", "partial"):
        text_items = [
            EvidenceItem(
                path="run_code",
                key="computation_result",
                text=notes_s,
                role="primary",
                entity_type=COMPUTATION_ENTITY_TYPE,
                why="沙箱/聚合结果（由 notes 回填为答用证据）",
            )
        ]
        read_failures = []

    if cov == "sufficient" and not text_items:
        cov = "empty"

    resolved = [
        ResolvedEntity.from_dict(x)
        for x in (payload.get("resolved_entities") or [])
        if isinstance(x, dict)
    ]
    unresolved = [
        GapItem.from_dict(x)
        for x in (payload.get("unresolved") or [])
        if isinstance(x, dict)
    ]
    unresolved.extend(read_failures)
    not_expanded = [
        GapItem.from_dict(x)
        for x in (payload.get("not_expanded") or [])
        if isinstance(x, dict)
    ]

    return EvidencePackage(
        coverage=cov,  # type: ignore[arg-type]
        items=text_items,
        question=question,
        question_focus=(
            str(payload["question_focus"])
            if payload.get("question_focus") is not None
            else None
        ),
        resolved_entities=resolved,
        unresolved=unresolved,
        not_expanded=not_expanded,
        notes=notes,
    )


def _write_log(payload: dict, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    sid = payload.get("session_id") or uuid.uuid4().hex[:8]
    path = log_dir / f"find_{ts}_{sid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
