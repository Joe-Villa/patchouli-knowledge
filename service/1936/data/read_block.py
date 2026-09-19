"""Shim: loads tool/read_block/block.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DATA = Path(__file__).resolve().parent
_RB = Path(__file__).resolve().parents[3] / "tool" / "read_block"
if str(_DATA) not in sys.path:
    sys.path.insert(0, str(_DATA))

_spec = importlib.util.spec_from_file_location("patchouli_tool_read_block", _RB / "block.py")
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["patchouli_tool_read_block"] = _mod
_spec.loader.exec_module(_mod)
for _k in dir(_mod):
    if not _k.startswith("_"):
        globals()[_k] = getattr(_mod, _k)
read_block = _mod.read_block
