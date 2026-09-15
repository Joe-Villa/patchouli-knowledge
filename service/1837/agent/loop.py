"""1837 找侧：启用 common.agent 实例（工坊 community）。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from common.agent import (  # noqa: E402
    AgentInstance,
    EvidencePackage,
    FindResult,
    LLMConfig,
    hydrate_package,
    load_llm_config,
    run_find,
)
from agent.tools import TOOL_SCHEMAS, ToolContext, dumps_tool_result, make_dispatcher

log = logging.getLogger("workshop.agent.find")

_AGENT_DIR = Path(__file__).resolve().parent

DEFAULT_MAX_ROUNDS = 10
DEFAULT_SUBMIT_GRACE = 2
DEFAULT_MAX_WALL_SEC = 150.0


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_find_system(*, include_structure: bool = True) -> str:
    rules = _load_text(_AGENT_DIR / "rules" / "find_system.txt")
    prior = _load_text(_AGENT_DIR / "rules" / "prior_knowledge.txt")
    parts = [rules.strip(), "", "## prior_knowledge", prior.strip()]
    if include_structure:
        struct = _load_text(_AGENT_DIR / "derived" / "structure.md")
        parts.extend(["", "## structure.md", struct.strip()])
    return "\n".join(parts)


def build_instance(ctx: ToolContext) -> AgentInstance:
    dispatch_map = make_dispatcher(ctx)

    def _dispatch(name: str, args: dict[str, Any]) -> Any:
        return dispatch_map(name, args)

    return AgentInstance(
        name="1837-workshop",
        system_prompt=load_find_system(),
        tool_schemas=TOOL_SCHEMAS,
        dispatch=_dispatch,
        hydrate_submit=hydrate_package,
        dumps_tool_result=dumps_tool_result,
        max_rounds=DEFAULT_MAX_ROUNDS,
        submit_grace=DEFAULT_SUBMIT_GRACE,
        max_wall_sec=DEFAULT_MAX_WALL_SEC,
        temperature=0.2,
        reject_non_submit_when_forced=False,
        nudge_when_no_tool=False,
    )


def find_evidence(
    question: str,
    *,
    ctx: ToolContext,
    llm_config: LLMConfig | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    submit_grace: int = DEFAULT_SUBMIT_GRACE,
    max_wall_sec: float = DEFAULT_MAX_WALL_SEC,
    memory_prefix: str = "",
) -> FindResult:
    q = (question or "").strip()
    inst = build_instance(ctx)
    inst.max_rounds = max_rounds
    inst.submit_grace = submit_grace
    inst.max_wall_sec = max_wall_sec

    mem = (memory_prefix or "").strip()
    if mem and "【当前问题】" in mem:
        user_body = mem
    elif mem:
        user_body = mem + "\n\n" + q
    else:
        user_body = q

    result = run_find(
        inst,
        q,
        user_content=user_body,
        llm_config=llm_config or load_llm_config(),
    )
    result.recommend_items = list(getattr(ctx, "last_recommend_items", []) or [])
    return result


__all__ = [
    "FindResult",
    "EvidencePackage",
    "find_evidence",
    "load_find_system",
    "build_instance",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SUBMIT_GRACE",
    "DEFAULT_MAX_WALL_SEC",
]
