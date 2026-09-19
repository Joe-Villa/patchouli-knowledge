"""Patchouli 请求包：纯文本问题 → LLM 综述回答（不含 QQ）。

程序调用::

    from patchouli import RequestConfig, RequestEngine, run_request

    cfg = RequestConfig.defaults()          # 或 RequestConfig.from_file("request.toml")
    result = run_request("专制乌托邦对奴隶制什么态度？", cfg)
    print(result.answer)

    # 或持有配置、多次独立请求：
    engine = RequestEngine(cfg)
    r1 = engine.ask("…")
    r2 = engine.ask("…")  # 与 r1 无共享状态
"""

from .config import RequestConfig
from .request import RequestEngine, RequestResult, run_request

__all__ = [
    "RequestConfig",
    "RequestEngine",
    "RequestResult",
    "run_request",
]

__version__ = "0.3.0"
