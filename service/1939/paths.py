"""1939 HOI4 focus-tree viewer paths (same reduced data as 1936)."""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parent.parent

_DEFAULT_STEAM = REPO / "database" / "data" / "steam" / "394360"
_DEFAULT_CATALOG = REPO / "service" / "1936" / "data" / "mods_catalog.json"


def vanilla_root() -> Path:
    raw = os.environ.get("FOCUS_GAME_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_DEFAULT_STEAM / "vanilla").resolve()


def mods_root() -> Path:
    raw = os.environ.get("FOCUS_MODS_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_DEFAULT_STEAM / "mods").resolve()


def mods_catalog_path() -> Path:
    raw = os.environ.get("FOCUS_MODS_CATALOG", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_CATALOG.resolve()


def mod_start_path() -> Path:
    raw = (
        os.environ.get("FOCUS_MOD_START", "").strip()
        or os.environ.get("HOI4_MOD_START", "").strip()
    )
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO / "database" / "derived" / "hoi4" / "mod_start.json").resolve()


def tool_root() -> Path:
    return (REPO / "tool").resolve()
