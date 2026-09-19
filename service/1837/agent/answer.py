"""答侧：据证据包生成自然语言答复。"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.evidence import EvidencePackage
from agent.llm import LLMConfig, chat, load_llm_config, message_content

log = logging.getLogger("workshop.agent.answer")

_AGENT_DIR = Path(__file__).resolve().parent


def load_answer_system() -> str:
    ans = (_AGENT_DIR / "rules" / "answer_system.txt").read_text(encoding="utf-8")
    prior = (_AGENT_DIR / "rules" / "prior_knowledge.txt").read_text(encoding="utf-8")
    return ans.strip() + "\n\n" + prior.strip()


def synthesize_answer(
    question: str,
    package: EvidencePackage,
    *,
    llm_config: LLMConfig | None = None,
    memory_prefix: str = "",
) -> str:
    cfg = llm_config or load_llm_config()
    mem = (memory_prefix or "").strip()
    user_parts = []
    if mem and "【当前问题】" in mem:
        user_parts.append(mem)
    else:
        if mem:
            user_parts.append(mem)
        user_parts.append(f"【当前问题】\n{(question or '').strip()}")
    user_parts.append("【证据包】\n" + package.to_prompt_block())
    user = "\n\n".join(user_parts)
    try:
        resp = chat(
            [
                {"role": "system", "content": load_answer_system()},
                {"role": "user", "content": user},
            ],
            config=cfg,
            temperature=0.3,
        )
        content, _ = message_content(resp)
        text = (content or "").strip()
        if text:
            return text
    except Exception as exc:
        log.warning("answer llm failed: %s", exc)
    # 降级：直接拼证据
    lines = [f"（答侧降级）关于：{(question or '').strip()}", "", package.to_prompt_block()]
    return "\n".join(lines)
