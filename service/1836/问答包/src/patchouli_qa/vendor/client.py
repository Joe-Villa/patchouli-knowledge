"""DeepSeek（OpenAI 兼容）聊天客户端。标准库实现，支持 tool calling。"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("patchouli.llm.client")

_ROOT = Path(__file__).resolve().parent
# 不在 import 时偷读仓库外 .env；由 PatchouliQA.init / config.load 显式加载


def parse_dotenv(path: Path) -> None:
    """加载 dotenv 到 os.environ（已存在的键不覆盖）。"""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


# 兼容旧名
_parse_dotenv = parse_dotenv


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_sec: float = 90.0


def load_llm_config() -> LLMConfig:
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    return LLMConfig(
        api_key=key,
        base_url=(os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip(
            "/"
        ),
        model=(os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip(),
        timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC") or "90"),
    )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from model


@dataclass
class ChatMessage:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        elif self.role == "assistant" and self.tool_calls:
            d["content"] = None
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass
class ChatResult:
    message: ChatMessage
    finish_reason: str | None = None
    raw: dict = field(default_factory=dict)


def chat_completions(
    messages: list[dict[str, Any]] | list[ChatMessage],
    *,
    config: LLMConfig | None = None,
    temperature: float = 0.2,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> str:
    """兼容旧接口：只返回 assistant 文本 content。"""
    result = chat(messages, config=config, temperature=temperature, tools=tools, tool_choice=tool_choice)
    return (result.message.content or "").strip()


def chat(
    messages: list[dict[str, Any]] | list[ChatMessage],
    *,
    config: LLMConfig | None = None,
    temperature: float = 0.2,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> ChatResult:
    cfg = config or load_llm_config()
    if not cfg.api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    api_messages: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            api_messages.append(m.to_api_dict())
        else:
            api_messages.append(m)

    url = f"{cfg.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": api_messages,
        "stream": False,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        log.error("deepseek HTTP %s: %s", e.code, detail[:500])
        raise RuntimeError(f"DeepSeek HTTP {e.code}: {detail[:300]}") from e

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"empty deepseek response: {body!r}")
    ch0 = choices[0]
    msg = ch0.get("message") or {}
    tool_calls_raw = msg.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    for tc in tool_calls_raw:
        fn = tc.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=str(tc.get("id") or ""),
                name=str(fn.get("name") or ""),
                arguments=str(fn.get("arguments") or "{}"),
            )
        )
    content = msg.get("content")
    return ChatResult(
        message=ChatMessage(
            role=str(msg.get("role") or "assistant"),
            content=None if content is None else str(content),
            tool_calls=tool_calls or None,
        ),
        finish_reason=ch0.get("finish_reason"),
        raw=body,
    )
