"""Web 式短记忆（1837 进程内；不调 1836）。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("workshop.memory")

WEB_MAX_TURNS = 30


@dataclass(frozen=True)
class MemoryTurn:
    task_id: str
    logical_user: str
    logical_address: str
    question: str
    answer: str
    finished_at: float

    def to_log_dict(self, *, answer_max: int = 240) -> dict[str, Any]:
        ans = (self.answer or "").strip()
        if len(ans) > answer_max:
            ans = ans[: answer_max - 1] + "…"
        return {
            "task_id": self.task_id,
            "finished_at": self.finished_at,
            "question": (self.question or "").strip(),
            "answer_excerpt": ans,
        }


def memory_key(logical_user: str, logical_address: str) -> tuple[str, str]:
    return (str(logical_user), str(logical_address))


def web_memory_address(device_id: str, conversation_id: str) -> str:
    """与 1836 Web 同构：web:conv:{device}:{conversation}。"""
    d = (device_id or "").strip() or "anon"
    c = (conversation_id or "").strip() or "default"
    return f"web:conv:{d}:{c}"


def select_chain(
    turns_newest_first: list[MemoryTurn],
    *,
    now: float,
    gap_sec: float = 0.0,
    max_turns: int = WEB_MAX_TURNS,
) -> list[MemoryTurn]:
    if max_turns <= 0 or not turns_newest_first:
        return []
    out: list[MemoryTurn] = []
    anchor = float(now)
    gap = float(gap_sec)
    use_gap = gap > 0
    for t in turns_newest_first:
        if len(out) >= max_turns:
            break
        if use_gap and anchor - float(t.finished_at) > gap:
            break
        out.append(t)
        anchor = float(t.finished_at)
    return out


def _clip(text: str, max_chars: int) -> str:
    s = (text or "").strip().replace("\r\n", "\n")
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def format_injection(
    question: str,
    selected: list[MemoryTurn],
    *,
    answer_max_chars: int = 240,
    question_max_chars: int = 200,
    inject_max_chars: int = 3500,
) -> str:
    q = (question or "").strip()
    if not selected:
        return q
    chronological = sorted(selected, key=lambda t: float(t.finished_at))
    header = [
        "【本对话·可选参考】",
        "以下为同一网页对话中的成功问答（可能隔了一段时间）。",
        "仅当对理解或回答「当前问题」确有帮助时才可参考；无关条目必须完全忽略，禁止被带偏。",
        "",
    ]
    footer = ["【当前问题】", q]
    footer_len = sum(len(x) + 1 for x in footer)
    header_len = sum(len(x) + 1 for x in header)
    budget = max(200, int(inject_max_chars) - header_len - footer_len)

    kept: list[MemoryTurn] = []
    used = 0
    for t in reversed(chronological):
        block = (
            f"问：{_clip(t.question, question_max_chars)}\n"
            f"答：{_clip(t.answer, answer_max_chars)}\n"
        )
        cost = len(block) + 8
        if kept and used + cost > budget:
            break
        if not kept and cost > budget:
            kept.append(t)
            break
        kept.append(t)
        used += cost
    kept.reverse()

    lines = list(header)
    for i, t in enumerate(kept, start=1):
        lines.append(f"{i}) 问：{_clip(t.question, question_max_chars)}")
        lines.append(f"   答：{_clip(t.answer, answer_max_chars)}")
        lines.append("")
    lines.extend(footer)
    return "\n".join(lines)


class ShortMemoryStore:
    def __init__(
        self,
        *,
        gap_sec: float = 0.0,
        max_turns: int = WEB_MAX_TURNS,
        store_max_turns: int = 80,
    ) -> None:
        self.gap_sec = float(gap_sec)
        self.max_turns = int(max_turns)
        self.store_max_turns = max(int(store_max_turns), max(self.max_turns * 3, 30))
        self._by_key: dict[tuple[str, str], list[MemoryTurn]] = {}

    def candidates(
        self,
        logical_user: str,
        logical_address: str,
        *,
        now: float | None = None,
    ) -> list[MemoryTurn]:
        now = time.time() if now is None else now
        key = memory_key(logical_user, logical_address)
        turns = self._by_key.get(key) or []
        return select_chain(
            turns,
            now=now,
            gap_sec=self.gap_sec,
            max_turns=self.max_turns,
        )

    def record_success(
        self,
        *,
        task_id: str,
        logical_user: str,
        logical_address: str,
        question: str,
        answer: str,
        finished_at: float | None = None,
    ) -> MemoryTurn:
        turn = MemoryTurn(
            task_id=task_id,
            logical_user=str(logical_user),
            logical_address=str(logical_address),
            question=(question or "").strip(),
            answer=(answer or "").strip(),
            finished_at=float(finished_at if finished_at is not None else time.time()),
        )
        key = memory_key(turn.logical_user, turn.logical_address)
        lst = self._by_key.setdefault(key, [])
        lst.append(turn)
        lst.sort(key=lambda t: t.finished_at, reverse=True)
        if len(lst) > self.store_max_turns:
            del lst[self.store_max_turns :]
        return turn
