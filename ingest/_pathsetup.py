"""Put ingest blocks + runtime data on sys.path for flat imports."""

from __future__ import annotations

import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parent
_REPO = _INGEST.parent
_DATA = _REPO / "service" / "1836" / "data"

_READY = False


def setup() -> Path:
    """Insert ingest/* and service/1836/data. Return repo root."""
    global _READY
    if _READY:
        return _REPO
    dirs = [_INGEST / "_lib", _DATA]
    for child in sorted(_INGEST.iterdir()):
        if child.is_dir() and child.name not in {"_lib", "__pycache__"}:
            dirs.append(child)
    for d in reversed(dirs):
        s = str(d)
        if s not in sys.path:
            sys.path.insert(0, s)
    _READY = True
    return _REPO


def data_dir() -> Path:
    setup()
    return _DATA


def repo_root() -> Path:
    setup()
    return _REPO
