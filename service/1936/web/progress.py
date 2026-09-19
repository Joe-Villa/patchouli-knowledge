"""找侧/答侧进度条映射（与旧网页协议一致）。"""

from __future__ import annotations

from typing import Any, Literal

ProgressPhase = Literal["find", "answer", "corpus"]


def progress_pct(
    *,
    phase: str,
    round_i: int = 0,
    max_rounds: int = 12,
) -> int:
    max_rounds = max(1, int(max_rounds))
    round_i = max(0, int(round_i))
    p = (phase or "find").strip().lower()
    if p == "answer":
        return 95
    if p == "corpus":
        return 3
    frac = min(1.0, round_i / max_rounds)
    return int(90.0 * frac)


def progress_label(
    *,
    phase: str,
    round_i: int = 0,
    max_rounds: int = 12,
    tool: str | None = None,
) -> str:
    p = (phase or "find").strip().lower()
    if p == "answer":
        return "正在归纳回答…"
    if p == "corpus":
        return "选择资料库…"
    tool_s = f"（{tool}）" if tool else ""
    return f"检索资料 {round_i}/{max_rounds}{tool_s}"


def progress_extra(ev: dict[str, Any]) -> dict[str, Any]:
    phase = str(ev.get("phase") or "find")
    round_i = int(ev.get("round") or 0)
    max_rounds = int(ev.get("max_rounds") or 12)
    tool = ev.get("tool")
    tool_s = str(tool) if tool else None
    return {
        "phase": phase,
        "round": round_i,
        "max_rounds": max_rounds,
        "pct": progress_pct(phase=phase, round_i=round_i, max_rounds=max_rounds),
        "tool": tool_s,
        "milestone": True,
    }
