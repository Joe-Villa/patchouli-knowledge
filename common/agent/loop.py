"""通用找侧 ReAct 壳。

实例（AgentInstance）注入：提示词、工具 schema、dispatch、submit hydrate。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .evidence import EvidencePackage, hydrate_package
from .llm import LLMConfig, chat, load_llm_config, message_content

log = logging.getLogger("common.agent.loop")

ProgressCallback = Callable[[dict[str, Any]], None]
DispatchFn = Callable[[str, dict[str, Any]], Any]
HydrateFn = Callable[[dict[str, Any]], Any]
DumpsFn = Callable[[Any], str]


def _default_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:28_000]


@dataclass
class AgentInstance:
    """一个服务对通用壳的配置（启用哪些 tool / 读什么提示）。"""

    name: str
    system_prompt: str
    tool_schemas: list[dict[str, Any]]
    dispatch: DispatchFn
    hydrate_submit: HydrateFn = hydrate_package
    dumps_tool_result: DumpsFn = _default_dumps
    submit_tool: str = "submit_evidence_package"
    max_rounds: int = 12
    submit_grace: int = 3
    max_wall_sec: float = 180.0
    temperature: float = 0.2
    # 进入强制提交后拒绝非 submit 工具（1836）；1837 仅靠 tool_choice
    reject_non_submit_when_forced: bool = False
    nudge_when_no_tool: bool = True


@dataclass
class FindResult:
    ok: bool
    package: Any
    question: str
    rounds: int = 0
    tool_calls: int = 0
    elapsed_sec: float = 0.0
    stop_reason: str = ""
    error: str | None = None
    transcript: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    # 服务扩展（如 1837 recommend_mods 产物）
    recommend_items: list[dict] = field(default_factory=list)


def run_find(
    instance: AgentInstance,
    question: str,
    *,
    user_content: str | None = None,
    llm_config: LLMConfig | None = None,
    on_progress: ProgressCallback | None = None,
) -> FindResult:
    q = (question or "").strip()
    if not q and not (user_content or "").strip():
        return FindResult(
            ok=False,
            package=EvidencePackage(coverage="empty", notes="empty question"),
            question="",
            stop_reason="empty",
            error="empty question",
        )

    t0 = time.monotonic()
    cfg = llm_config or load_llm_config()
    max_rounds = instance.max_rounds
    submit_grace = max(0, instance.submit_grace)
    hard_cap = max_rounds + submit_grace
    submit_name = instance.submit_tool
    force_choice = {"type": "function", "function": {"name": submit_name}}

    user_body = (user_content if user_content is not None else q).strip()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instance.system_prompt},
        {"role": "user", "content": user_body},
    ]
    transcript: list[dict] = []
    tool_calls_n = 0
    submitted: Any | None = None
    stop_reason = ""
    error: str | None = None
    rounds_done = 0

    def _progress(**kwargs: Any) -> None:
        if on_progress is None:
            return
        try:
            on_progress({"phase": "find", **kwargs})
        except Exception:
            log.debug("on_progress failed", exc_info=True)

    for round_i in range(1, hard_cap + 1):
        rounds_done = round_i
        if time.monotonic() - t0 > instance.max_wall_sec:
            stop_reason = "wall_time"
            break

        force = round_i > max_rounds
        _progress(round=min(round_i, max_rounds), max_rounds=max_rounds, instance=instance.name)

        try:
            resp = chat(
                messages,
                config=cfg,
                tools=instance.tool_schemas,
                tool_choice=force_choice if force else "auto",
                temperature=instance.temperature,
            )
        except Exception as exc:
            error = str(exc)
            stop_reason = "llm_error"
            log.warning("[%s] find llm error: %s", instance.name, exc)
            break

        content, tool_calls = message_content(resp)
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        transcript.append(
            {"round": round_i, "assistant": content, "tools": len(tool_calls), "force": force}
        )

        if not tool_calls:
            if instance.nudge_when_no_tool and round_i < hard_cap:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"必须调用工具。若证据已够，立刻调用 {submit_name}。"
                            if force
                            else f"你必须使用工具查找，并在结束时调用 {submit_name}。"
                        ),
                    }
                )
                continue
            stop_reason = "no_tool"
            break

        got_submit = False
        for tc in tool_calls:
            tool_calls_n += 1
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            name = str(fn.get("name") or "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tc_id = str(tc.get("id") or f"call_{tool_calls_n}")

            if force and instance.reject_non_submit_when_forced and name != submit_name:
                result: Any = {
                    "ok": False,
                    "error": "submit_only_phase",
                    "hint": f"只允许调用 {submit_name}",
                }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": instance.dumps_tool_result(result),
                    }
                )
                continue

            if name == submit_name:
                try:
                    submitted = instance.hydrate_submit(args)
                    result = {"ok": True, "submitted": True, "accepted": True}
                    got_submit = True
                    stop_reason = "submitted"
                except Exception as exc:
                    error = f"hydrate_failed: {exc}"
                    stop_reason = "hydrate_error"
                    submitted = EvidencePackage(
                        coverage="empty", notes=f"submit hydrate failed: {exc}"
                    )
                    result = {"ok": False, "error": error}
                    got_submit = True
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": instance.dumps_tool_result(result),
                    }
                )
                continue

            try:
                result = instance.dispatch(name, args)
            except Exception as exc:
                log.exception("[%s] tool %s failed", instance.name, name)
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": instance.dumps_tool_result(result),
                }
            )
            # 兼容：dispatch 自行返回 submitted
            if isinstance(result, dict) and result.get("submitted") and result.get("package") is not None:
                try:
                    submitted = instance.hydrate_submit(result.get("package") or {})
                    got_submit = True
                    stop_reason = "submitted"
                except Exception as exc:
                    error = f"hydrate_failed: {exc}"
                    stop_reason = "hydrate_error"

        if got_submit and submitted is not None:
            break
    else:
        stop_reason = stop_reason or "max_rounds"

    if submitted is None:
        submitted = EvidencePackage(
            coverage="empty",
            notes=f"find stopped: {stop_reason or 'unknown'}; error={error or ''}",
        )
        ok = False
    else:
        ok = stop_reason == "submitted" and error is None

    return FindResult(
        ok=ok,
        package=submitted,
        question=q or user_body[:200],
        rounds=rounds_done,
        tool_calls=tool_calls_n,
        elapsed_sec=time.monotonic() - t0,
        stop_reason=stop_reason,
        error=error,
        transcript=transcript,
        messages=messages,
    )
