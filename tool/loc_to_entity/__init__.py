"""Domain tool: loc_to_entity. Implementation in tool/_script_find/tools.py."""
from __future__ import annotations
import sys
from pathlib import Path

_p = str(Path(__file__).resolve().parents[1] / "_script_find")
if _p not in sys.path:
    sys.path.insert(0, _p)
import tools as _tools

# re-export dispatcher pieces; call sites still use find.tools shim for now
TOOL_NAME = "loc_to_entity"
