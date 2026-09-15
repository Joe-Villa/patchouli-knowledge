"""通用 Agent 壳：找侧 ReAct 循环 + 证据协议 + LLM 调用。

服务（1836/1837）只提供实例：system、tools、dispatch、hydrate。
"""

from .evidence import EvidenceItem, EvidencePackage, hydrate_package
from .llm import LLMConfig, chat, load_llm_config, message_content
from .loop import AgentInstance, FindResult, run_find

__all__ = [
    "AgentInstance",
    "EvidenceItem",
    "EvidencePackage",
    "FindResult",
    "LLMConfig",
    "chat",
    "hydrate_package",
    "load_llm_config",
    "message_content",
    "run_find",
]
