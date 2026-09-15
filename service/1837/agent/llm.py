"""兼容导出：LLM 以 common.agent 为准。"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from common.agent.llm import LLMConfig, chat, load_llm_config, message_content

__all__ = ["LLMConfig", "chat", "load_llm_config", "message_content"]
