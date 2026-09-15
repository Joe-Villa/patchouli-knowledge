"""控制台配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# service/1453/console → 1453 → service → 仓库根
_ROOT = Path(__file__).resolve().parent.parent
_REPO = _ROOT.parent.parent
load_dotenv(_ROOT / ".env")

# 约定大于配置：各服务日志固定在仓库 log/<端口>/
_LOG_1836 = _REPO / "log" / "1836"
_LOG_1837 = _REPO / "log" / "1837"
_LOG_1453 = _REPO / "log" / "1453"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class ConsoleSettings:
    host: str
    port: int
    password: str
    data_dir: Path
    framework_log_dir: Path
    proxy_store_path: Path
    reqlog_dir: Path
    recommend_log_dir: Path
    log_limit: int
    session_secret: str


def load_console_settings() -> ConsoleSettings:
    # 控制台自身日志写在 log/1453；读写 1836/1837 也走约定路径
    log_raw = _env("FRAMEWORK_LOG_DIR", str(_LOG_1453))
    proxy_raw = _env("PROXY_STORE_PATH", str(_ROOT / "runtime" / "proxy_tables.json"))
    data_raw = _env("CONSOLE_DATA_DIR", str(_ROOT / "console_data"))
    req_raw = _env("REQLOG_DIR", str(_LOG_1836))
    rec_raw = _env("RECOMMEND_LOG_DIR", str(_LOG_1837))
    pwd = _env("CONSOLE_PASSWORD", "QWER10uiop")
    secret = _env("CONSOLE_SESSION_SECRET") or pwd
    return ConsoleSettings(
        host=_env("CONSOLE_HOST", "0.0.0.0"),
        port=max(1, int(_env("CONSOLE_PORT", "1453"))),
        password=pwd,
        data_dir=Path(data_raw).expanduser().resolve(),
        framework_log_dir=Path(log_raw).expanduser().resolve(),
        proxy_store_path=Path(proxy_raw).expanduser().resolve(),
        reqlog_dir=Path(req_raw).expanduser().resolve(),
        recommend_log_dir=Path(rec_raw).expanduser().resolve(),
        log_limit=max(10, int(_env("CONSOLE_LOG_LIMIT", "200"))),
        session_secret=secret,
    )
