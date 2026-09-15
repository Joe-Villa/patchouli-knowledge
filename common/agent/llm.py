"""DeepSeek / OpenAI 兼容 Chat（dict messages）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_sec: float = 90.0


def load_llm_config() -> LLMConfig:
    key = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY") or "").strip()
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    timeout = float(os.environ.get("LLM_TIMEOUT_SEC") or 90)
    return LLMConfig(api_key=key, base_url=base, model=model, timeout_sec=timeout)


def chat(
    messages: list[dict[str, Any]],
    *,
    config: LLMConfig | None = None,
    tools: list[dict] | None = None,
    tool_choice: Any = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    cfg = config or load_llm_config()
    if not cfg.api_key:
        raise RuntimeError("missing DEEPSEEK_API_KEY")
    url = f"{cfg.base_url}/chat/completions"
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {err[:500]}") from e


def message_content(resp: dict[str, Any]) -> tuple[str | None, list[dict]]:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    tool_calls = msg.get("tool_calls") or []
    return content, list(tool_calls or [])
