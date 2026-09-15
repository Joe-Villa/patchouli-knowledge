"""短期记忆门控（同步 HTTP；不依赖 httpx）。"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from service.memory import MemoryTurn

log = logging.getLogger("workshop.memory.gate")

_SYSTEM = """你是维多利亚 3 工坊社区问答的「追问上下文筛选器」。
给定「当前问题」与若干条「近期成功问答」（按时间从新到旧编号，从 1 开始）。
任务：只选出对理解或回答「当前问题」真正有帮助的记忆编号。

何谓「依赖上文」（必须选相关记忆）：
- 指代/省略：它、这个、那个、前者、后者、上面、刚才、同上
- 序数/列举续问：第一个、第二条、其中 X、还有呢、再问
- 明显续问同一模组/作者/统计，即使没代词

何谓「语义自足」（useful 必须为空）：
- 当前问题本身已含足够实体名，不读上文也能独立检索
- 与近期记忆主题无关的新问题

硬规则：
1. 宁可漏掉弱相关，不可把无关记忆塞进追问。
2. 严格只输出一行 JSON，不要 markdown：
   {"useful":[编号...],"reason":"一句中文原因"}
"""


@dataclass(frozen=True)
class GateResult:
    useful_indices: list[int]
    selected: list[MemoryTurn]
    reason: str
    raw_text: str
    error: str | None
    elapsed_sec: float
    model: str
    skipped: bool = False


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_useful(obj: dict[str, Any], n: int) -> tuple[list[int], str]:
    raw = obj.get("useful")
    reason = str(obj.get("reason") or "").strip()
    out: list[int] = []
    if isinstance(raw, list):
        for x in raw:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= n and i not in out:
                out.append(i)
    out.sort()
    return out, reason


class NoopMemoryGate:
    def select(self, question: str, candidates: list[MemoryTurn]) -> GateResult:
        return GateResult(
            useful_indices=[],
            selected=[],
            reason="noop",
            raw_text="",
            error=None,
            elapsed_sec=0.0,
            model="",
            skipped=True,
        )


class LlmMemoryGate:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout_sec: float = 20.0,
        answer_excerpt_chars: int = 180,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.model = model or "deepseek-chat"
        self.timeout_sec = float(timeout_sec)
        self.answer_excerpt_chars = int(answer_excerpt_chars)

    def _build_user(self, question: str, candidates: list[MemoryTurn]) -> str:
        lines = [f"当前问题：\n{(question or '').strip()}\n", "近期记忆（从新到旧）："]
        for i, t in enumerate(candidates, start=1):
            ans = (t.answer or "").strip()
            if len(ans) > self.answer_excerpt_chars:
                ans = ans[: self.answer_excerpt_chars - 1] + "…"
            lines.append(f"[{i}] 问：{(t.question or '').strip()}")
            lines.append(f"    答：{ans}")
        lines.append('\n只输出 JSON：{"useful":[...],"reason":"..."}')
        return "\n".join(lines)

    def select(self, question: str, candidates: list[MemoryTurn]) -> GateResult:
        if not candidates:
            return GateResult(
                useful_indices=[],
                selected=[],
                reason="no_candidates",
                raw_text="",
                error=None,
                elapsed_sec=0.0,
                model=self.model,
                skipped=True,
            )
        if not self.api_key:
            return GateResult(
                useful_indices=[],
                selected=[],
                reason="missing_api_key",
                raw_text="",
                error="missing_api_key",
                elapsed_sec=0.0,
                model=self.model,
                skipped=True,
            )

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": self._build_user(question, candidates)},
            ],
            "temperature": 0.0,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        t0 = time.time()
        raw = ""
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices") or []
            if not choices:
                raise ValueError(f"empty choices: {body!r}")
            raw = str((choices[0].get("message") or {}).get("content") or "").strip()
            obj = _extract_json_obj(raw)
            if obj is None:
                raise ValueError(f"gate response not json: {raw!r}")
            idxs, reason = _parse_useful(obj, len(candidates))
            selected = [candidates[i - 1] for i in idxs]
            return GateResult(
                useful_indices=idxs,
                selected=selected,
                reason=reason or "ok",
                raw_text=raw,
                error=None,
                elapsed_sec=time.time() - t0,
                model=self.model,
                skipped=False,
            )
        except Exception as exc:
            log.warning("memory gate failed: %s", exc)
            return GateResult(
                useful_indices=[],
                selected=[],
                reason="gate_failed_no_inject",
                raw_text=raw,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_sec=time.time() - t0,
                model=self.model,
                skipped=False,
            )
