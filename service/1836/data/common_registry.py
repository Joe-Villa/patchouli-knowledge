"""Shim: loads ingest/common_registry/common_registry.py (runtime helpers for read_block)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DATA = Path(__file__).resolve().parent
_IMPL = Path(__file__).resolve().parents[3] / "ingest" / "common_registry" / "common_registry.py"
_INGEST = _IMPL.parent.parent
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import setup  # noqa: E402

setup()
if str(_DATA) not in sys.path:
    sys.path.insert(0, str(_DATA))

_spec = importlib.util.spec_from_file_location("patchouli_ingest_common_registry", _IMPL)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["patchouli_ingest_common_registry"] = _mod
_spec.loader.exec_module(_mod)
for _k in dir(_mod):
    if not _k.startswith("_") or _k in {"_KEY", "_depths_before"}:
        globals()[_k] = getattr(_mod, _k)
