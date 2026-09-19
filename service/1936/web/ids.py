"""网页 device_id / conversation_id 校验。"""

from __future__ import annotations

import re

_WEB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def normalize_web_token(value: str, *, what: str = "token") -> str:
    s = str(value or "").strip()
    if not _WEB_TOKEN_RE.fullmatch(s):
        raise ValueError(f"invalid {what}")
    return s


def normalize_device_id(device_id: str) -> str:
    return normalize_web_token(device_id, what="device_id")


def normalize_conversation_id(conversation_id: str) -> str:
    return normalize_web_token(conversation_id, what="conversation_id")
