"""读取 config/config.toml + .env。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EngineConfig:
    max_rounds: int = 12
    submit_grace: int = 3
    max_wall_sec: float = 180.0
    lang: str = "simp_chinese"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    llm_timeout_sec: float = 90.0
    sessions_root: Path | None = None
    skip_answer: bool = False


_TOML_LINE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*(?:#.*)?$"
)


def _parse_toml_lite(text: str) -> dict[str, str]:
    """极简 TOML：只支持顶层 key = value（字符串/数字/布尔）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("["):
            continue
        m = _TOML_LINE.match(line)
        if not m:
            continue
        out[m.group(1)] = m.group(2).strip()
    return out


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_dotenv(path: Path) -> None:
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


def load_engine_config(config_toml: Path, env_file: Path, root: Path) -> EngineConfig:
    parse_dotenv(env_file)
    raw: dict[str, str] = {}
    if config_toml.is_file():
        raw = _parse_toml_lite(config_toml.read_text(encoding="utf-8"))

    def get_int(key: str, default: int) -> int:
        if key not in raw:
            return default
        return int(_unquote(raw[key]))

    def get_float(key: str, default: float) -> float:
        if key not in raw:
            return default
        return float(_unquote(raw[key]))

    def get_str(key: str, default: str | None = None) -> str | None:
        if key not in raw:
            return default
        return _unquote(raw[key])

    def get_bool(key: str, default: bool) -> bool:
        if key not in raw:
            return default
        v = _unquote(raw[key]).lower()
        return v in ("1", "true", "yes", "on")

    sessions = get_str("sessions_root")
    sessions_path = (
        (root / sessions).resolve()
        if sessions
        else (root / "sessions").resolve()
    )

    api_key = get_str("api_key") or (os.getenv("DEEPSEEK_API_KEY") or "").strip() or None
    return EngineConfig(
        max_rounds=get_int("max_rounds", 12),
        submit_grace=get_int("submit_grace", 3),
        max_wall_sec=get_float("max_wall_sec", 180.0),
        lang=get_str("lang", "simp_chinese") or "simp_chinese",
        model=get_str("model") or (os.getenv("DEEPSEEK_MODEL") or None),
        base_url=get_str("base_url") or (os.getenv("DEEPSEEK_BASE_URL") or None),
        api_key=api_key,
        llm_timeout_sec=get_float("llm_timeout_sec", 90.0),
        sessions_root=sessions_path,
        skip_answer=get_bool("skip_answer", False),
    )
