"""沙箱会话（vendor 别名，供 agent 以 ``from sandbox import SandboxSession`` 导入）。"""

from sandbox_session import SandboxSession, RunResult  # noqa: F401

__all__ = ["SandboxSession", "RunResult"]
