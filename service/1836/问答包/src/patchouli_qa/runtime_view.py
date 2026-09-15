"""把 data/ + derived/ 合成旧找侧期望的「game_root」视图。"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


DERIVED_FILES = (
    "localization.sqlite",
    "loc_entity_index.sqlite",
    "common_registry.sqlite",
    "events_registry.sqlite",
    "map_data_registry.sqlite",
    "hub_anchors.sqlite",
    "names_index.sqlite",
    "routing_table.json",
    "query_aliases.json",
    "localization_assoc.json",
    "structure.md",
)

# common/events/map_data：Vic3 削减树；tables：CSV/清单类语料（如汉化洋名）
DATA_LINKS = ("common", "events", "map_data", "tables")


@dataclass
class CorpusMount:
    """一次 invoke 挂载的语料。"""

    name: str
    data_root: Path
    derived_root: Path
    view_root: Path  # 合成后的 game-shaped 目录


def _link_or_copy(src: Path, dst: Path) -> None:
    """物化到视图内。

    不用指向视图外的 symlink：bwrap --ro-bind 后，沙箱内无法跟随出站链接。
    """
    if dst.exists() or dst.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = src.resolve()
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=False)
    else:
        shutil.copy2(src, dst)

def resolve_constraint_names(
    data_dir: Path, data_constraint: list[str] | None
) -> list[str]:
    """data_constraint：语料名（相对 data/）或指向 data 子目录的路径。"""
    if not data_constraint:
        raise ValueError("data_constraint 不能为空：请显式传入允许访问的语料名列表")
    names: list[str] = []
    for item in data_constraint:
        raw = str(item).strip()
        if not raw:
            continue
        p = Path(raw)
        if p.is_dir() and (p / "common").is_dir():
            names.append(p.name)
            continue
        # 相对 data/ 的名字
        cand = data_dir / raw
        if cand.is_dir():
            names.append(raw)
            continue
        # 允许传入 "data/vanilla"
        if raw.startswith("data/"):
            names.append(raw[5:])
            continue
        raise FileNotFoundError(f"data_constraint 项无法解析为语料目录: {raw}")
    if not names:
        raise ValueError("data_constraint 解析后为空")
    return names


def materialize_views(
    *,
    data_dir: Path,
    derived_dir: Path,
    names: list[str],
    views_root: Path,
) -> list[CorpusMount]:
    """为每个语料建 game-shaped 视图；返回顺序与 names 一致。第一个视为 /game 主树。"""
    sid = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    base = views_root / sid
    base.mkdir(parents=True, exist_ok=False)
    mounts: list[CorpusMount] = []
    for name in names:
        data_root = (data_dir / name).resolve()
        derived_root = (derived_dir / name).resolve()
        if not data_root.is_dir():
            raise FileNotFoundError(f"缺少 data/{name}")
        if not derived_root.is_dir():
            raise FileNotFoundError(f"缺少 derived/{name}")
        view = base / name
        view.mkdir(parents=True, exist_ok=False)
        for link_name in DATA_LINKS:
            src = data_root / link_name
            if src.exists():
                _link_or_copy(src, view / link_name)
        for fname in DERIVED_FILES:
            src = derived_root / fname
            if src.is_file():
                _link_or_copy(src, view / fname)
        # query_aliases：工具还会在若干默认路径找；保证视图内有一份
        mounts.append(
            CorpusMount(
                name=name,
                data_root=data_root,
                derived_root=derived_root,
                view_root=view,
            )
        )
    return mounts
