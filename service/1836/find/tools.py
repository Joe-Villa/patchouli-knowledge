"""Shim: loads tool/_script_find/tools.py under a unique module name."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_DATA = _ROOT / "service" / "1836" / "data"
_SF = _ROOT / "tool" / "_script_find"
_TOOL = _ROOT / "tool"

for p in (str(_TOOL), str(_DATA), str(_SF)):
    if p not in sys.path:
        sys.path.insert(0, p)

_impl = _SF / "tools.py"
_spec = importlib.util.spec_from_file_location("patchouli_script_find_tools", _impl)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["patchouli_script_find_tools"] = _mod
_spec.loader.exec_module(_mod)

# re-export public names expected by find.agent
for _k in dir(_mod):
    if _k.startswith("_") and _k not in ("_tool",):
        continue
    globals()[_k] = getattr(_mod, _k)

TOOL_SCHEMAS = _mod.TOOL_SCHEMAS
make_dispatcher = _mod.make_dispatcher
ToolContext = _mod.ToolContext
dumps_tool_result = _mod.dumps_tool_result
DEFAULT_GAME = _mod.DEFAULT_GAME
DEFAULT_DATA = _mod.DEFAULT_DATA
