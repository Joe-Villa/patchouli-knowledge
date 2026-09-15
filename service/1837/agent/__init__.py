"""工坊社区 Agent：找侧 ReAct → 证据 → 答侧。"""

__all__ = ["invoke"]


def __getattr__(name: str):
    if name == "invoke":
        from agent.pipeline import invoke

        return invoke
    raise AttributeError(name)
