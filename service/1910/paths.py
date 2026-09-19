"""Interwar 官网路径。运行时只读 derived；ingest 另读模组 / vanilla。"""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parent.parent

DEFAULT_MOD = Path(
    "/home/liulingda/.steam/debian-installation/steamapps/workshop/content/529340/3346844497"
)
DEFAULT_VANILLA = Path(
    "/home/liulingda/.steam/debian-installation/steamapps/common/Victoria 3/game"
)
DEFAULT_VANILLA_ALT = Path("/home/liulingda/桌面/vic3modder/vic3/game")
DEFAULT_EDITOR = Path("/home/liulingda/桌面/vic3modder/vic3市场分析/地图编辑器")
DEFAULT_VIC3KIT = Path("/home/liulingda/桌面/vic3modder/vic3kit/src")
DEFAULT_WORKSHOP_DB = (
    REPO
    / "database"
    / "data"
    / "steam"
    / "529340"
    / "community"
    / "workshop_details"
    / "workshop_details_all.sqlite"
)
WORKSHOP_ID = "3346844497"
WORKSHOP_CREATOR = "76561199065985283"


def derived_dir() -> Path:
    raw = os.environ.get("INTERWAR_DERIVED", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO / "database" / "derived" / "1910").resolve()


def mod_root() -> Path:
    raw = os.environ.get("INTERWAR_MOD", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_MOD


def vanilla_root() -> Path:
    raw = os.environ.get("INTERWAR_VANILLA", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if DEFAULT_VANILLA.is_dir():
        return DEFAULT_VANILLA
    return DEFAULT_VANILLA_ALT


def editor_root() -> Path:
    raw = os.environ.get("INTERWAR_EDITOR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_EDITOR


def vic3kit_src() -> Path:
    raw = os.environ.get("INTERWAR_VIC3KIT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_VIC3KIT


def workshop_db() -> Path:
    raw = os.environ.get("WORKSHOP_DB", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_WORKSHOP_DB
