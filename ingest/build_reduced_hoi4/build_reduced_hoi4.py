#!/usr/bin/env python3
"""HOI4 原版削减：Steam 根目录 → database/data/steam/394360/vanilla。

- N=16 文件夹；只复制纯文本；非纯文本直接跳过（不进 tree）
- localisation → localization.sqlite（yml 不落盘）
- common_registry + HOI4 events_registry + loc_entity_index（无 map/hub）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import repo_root, setup  # noqa: E402

setup()

from common_registry import build_common_registry, write_registry  # noqa: E402
from events_registry import write_events_registry  # noqa: E402
from events_registry_hoi4 import build_events_registry_hoi4  # noqa: E402
from loc_entity_index import build_loc_entity_rows, write_loc_entity_index  # noqa: E402
from localization_db import DEFAULT_LANGS, build_localization_db  # noqa: E402

N_FOLDERS = [
    "common",
    "country_metadata",
    "dlc",
    "dlc_metadata",
    "events",
    "gfx",
    "history",
    "integrated_dlc",
    "interface",
    "localisation",
    "map",
    "music",
    "portraits",
    "sound",
    "tutorial",
    "script",
]

TREE_DIRNAME = "_tree"
LOC_FOLDER = "localisation"
LOC_DB_NAME = "localization.sqlite"
COMMON_REGISTRY_DB = "common_registry.sqlite"
COMMON_REGISTRY_SUMMARY = "common_registry_summary.json"
EVENTS_REGISTRY_DB = "events_registry.sqlite"
EVENTS_REGISTRY_SUMMARY = "events_registry_summary.json"
LOC_ENTITY_INDEX_DB = "loc_entity_index.sqlite"
LOC_ENTITY_INDEX_SUMMARY = "loc_entity_index_summary.json"

NO_EXT = ""
TEXT_SUFFIXES = frozenset(
    {
        NO_EXT,
        ".txt",
        ".yml",
        ".md",
        ".csv",
        ".gui",
        ".json",
        ".asset",
        ".settings",
        ".dlc",
        ".font",
        ".layout",
        ".shortcuts",
        ".meta",
        ".lines",
        ".compound",
        ".editordata",
        ".shader",
        ".fxh",
        ".skin",
        ".particle2",
        ".terrain",
        ".vertical_borders",
        ".heightmap",
        ".map",
        ".lua",
        ".gfx",
        ".sfx",
    }
)

IGNORE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyc",
        ".pyo",
        ".pyd",
        ".pyw",
        ".bak",
        ".tmp",
        ".swp",
        ".swo",
        ".orig",
        ".rej",
        ".ds_store",
    }
)


def default_game_dir() -> Path:
    env = os.environ.get("HOI4_GAME_DIR", "").strip()
    if env:
        return Path(env)

    home = Path.home()
    candidates = [
        home / ".steam/steam/steamapps/common/Hearts of Iron IV",
        home / ".steam/debian-installation/steamapps/common/Hearts of Iron IV",
        home / ".local/share/Steam/steamapps/common/Hearts of Iron IV",
        home
        / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/common/Hearts of Iron IV",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def default_out_dir() -> Path:
    return repo_root() / "database" / "data" / "steam" / "394360" / "vanilla"


def file_suffix(name: str) -> str:
    if "." not in name:
        return NO_EXT
    ext = name.rsplit(".", 1)[-1]
    if not ext or ext == name:
        return NO_EXT
    return f".{ext.lower()}"


def is_text_suffix(suffix: str) -> bool:
    return suffix in TEXT_SUFFIXES


def is_ignored_suffix(suffix: str) -> bool:
    return suffix in IGNORE_SUFFIXES


def soft_remove(path: Path) -> None:
    if not path.exists():
        return
    if shutil.which("gio"):
        subprocess.run(["gio", "trash", str(path)], check=True)
        return
    trash = Path.home() / ".local/share/Trash/files"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / path.name
    if dest.exists():
        dest = trash / f"{path.name}_{os.getpid()}"
    shutil.move(str(path), str(dest))


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build(src_game: Path, dst_game: Path) -> dict:
    if not src_game.is_dir():
        raise SystemExit(f"源 game 不存在: {src_game}")

    if dst_game.exists():
        soft_remove(dst_game)

    tree_root = dst_game / TREE_DIRNAME
    by_folder_dir = tree_root / "by_folder"
    by_suffix_dir = tree_root / "by_suffix"
    by_folder_dir.mkdir(parents=True, exist_ok=True)
    by_suffix_dir.mkdir(parents=True, exist_ok=True)

    all_paths: list[str] = []
    text_paths: list[str] = []
    loc_tree_paths: list[str] = []
    ignored_paths: list[str] = []
    folder_stats: list[dict] = []
    global_suffix_counter: Counter[str] = Counter()
    ignored_suffix_counter: Counter[str] = Counter()
    suffix_index: dict[str, list[str]] = {}

    copied = 0
    skipped_binary = 0
    loc_in_tree_not_copied = 0
    ignored = 0
    missing_folders: list[str] = []

    for folder_name in N_FOLDERS:
        src_folder = src_game / folder_name
        if not src_folder.is_dir():
            missing_folders.append(folder_name)
            folder_stats.append(
                {
                    "folder": folder_name,
                    "missing": True,
                    "total": 0,
                    "text_copied": 0,
                    "text_loc_db_only": 0,
                    "binary_skipped": 0,
                    "ignored": 0,
                }
            )
            write_lines(by_folder_dir / f"{folder_name}.paths.txt", [])
            write_lines(by_folder_dir / f"{folder_name}.text.txt", [])
            write_lines(by_folder_dir / f"{folder_name}.ignored.txt", [])
            continue

        folder_paths: list[str] = []
        folder_text: list[str] = []
        folder_ignored: list[str] = []
        folder_loc_only = 0
        folder_copied = 0
        folder_binary_skipped = 0
        suffix_counter: Counter[str] = Counter()
        is_loc_folder = folder_name == LOC_FOLDER

        for root, _dirs, files in os.walk(src_folder, followlinks=False):
            root_path = Path(root)
            for fname in files:
                src_file = root_path / fname
                rel = src_file.relative_to(src_game).as_posix()
                suffix = file_suffix(fname)
                suffix_key = suffix if suffix else "(无后缀)"

                if is_ignored_suffix(suffix):
                    ignored += 1
                    folder_ignored.append(rel)
                    ignored_paths.append(rel)
                    ignored_suffix_counter[suffix_key] += 1
                    continue

                if not is_text_suffix(suffix):
                    # 非纯文本：不进 tree、不复制
                    skipped_binary += 1
                    folder_binary_skipped += 1
                    continue

                suffix_counter[suffix_key] += 1
                global_suffix_counter[suffix_key] += 1
                suffix_index.setdefault(suffix_key, []).append(rel)

                folder_paths.append(rel)
                all_paths.append(rel)
                folder_text.append(rel)
                text_paths.append(rel)

                if is_loc_folder:
                    loc_tree_paths.append(rel)
                    loc_in_tree_not_copied += 1
                    folder_loc_only += 1
                else:
                    dst_file = dst_game / rel
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    copied += 1
                    folder_copied += 1

        folder_paths.sort()
        folder_text.sort()
        folder_ignored.sort()
        write_lines(by_folder_dir / f"{folder_name}.paths.txt", folder_paths)
        write_lines(by_folder_dir / f"{folder_name}.text.txt", folder_text)
        write_lines(by_folder_dir / f"{folder_name}.ignored.txt", folder_ignored)

        folder_stats.append(
            {
                "folder": folder_name,
                "missing": False,
                "total": len(folder_paths),
                "text_copied": folder_copied,
                "text_loc_db_only": folder_loc_only,
                "binary_skipped": folder_binary_skipped,
                "ignored": len(folder_ignored),
                "by_suffix": [
                    {"suffix": k, "count": v}
                    for k, v in sorted(
                        suffix_counter.items(), key=lambda x: (-x[1], x[0])
                    )
                ],
            }
        )

    loc_root = src_game / LOC_FOLDER
    loc_db_path = dst_game / LOC_DB_NAME
    loc_stats = None
    if loc_root.is_dir():
        loc_stats = build_localization_db(
            loc_root, loc_db_path, langs=DEFAULT_LANGS
        )
    else:
        missing_folders.append("localisation(source)")

    common_reg_section: dict | None = None
    dst_common = dst_game / "common"
    if dst_common.is_dir():
        reg_result = build_common_registry(dst_common)
        write_registry(
            reg_result,
            dst_game / COMMON_REGISTRY_DB,
            dst_game / COMMON_REGISTRY_SUMMARY,
        )
        registered = [f.folder for f in reg_result.folders if f.status == "registered"]
        skipped = [
            {"folder": f.folder, "reason": f.reason}
            for f in reg_result.folders
            if f.status == "skipped"
        ]
        common_reg_section = {
            "db": COMMON_REGISTRY_DB,
            "summary": COMMON_REGISTRY_SUMMARY,
            "schema": "entries(key, file, type); folders(status/reason); skipped_items",
            "in_tree": False,
            "entry_count": len(reg_result.entries),
            "registered_folders": sorted(registered),
            "skipped_folders": skipped,
            "skipped_items": len(reg_result.skipped_items),
        }

    events_reg_section: dict | None = None
    dst_events = dst_game / "events"
    if dst_events.is_dir():
        ev_result = build_events_registry_hoi4(dst_events)
        write_events_registry(
            ev_result,
            dst_game / EVENTS_REGISTRY_DB,
            dst_game / EVENTS_REGISTRY_SUMMARY,
        )
        events_reg_section = {
            "db": EVENTS_REGISTRY_DB,
            "summary": EVENTS_REGISTRY_SUMMARY,
            "schema": "entries(key, namespace, number, file); rejected; files",
            "in_tree": False,
            "completeness": "authoritative_hoi4_id",
            "entry_count": len(ev_result.entries),
            "rejected_count": len(ev_result.rejected),
            "file_count": len(ev_result.files),
            "namespaces_declared": len(ev_result.namespaces_declared),
        }

    loc_entity_section: dict | None = None
    if (dst_game / COMMON_REGISTRY_DB).is_file():
        le_rows = build_loc_entity_rows(
            common_db=dst_game / COMMON_REGISTRY_DB,
            events_db=dst_game / EVENTS_REGISTRY_DB,
            map_db=dst_game / "map_data_registry.sqlite",  # 故意不存在
            loc_db=dst_game / LOC_DB_NAME,
        )
        write_loc_entity_index(
            le_rows,
            dst_game / LOC_ENTITY_INDEX_DB,
            dst_game / LOC_ENTITY_INDEX_SUMMARY,
        )
        loc_entity_section = {
            "db": LOC_ENTITY_INDEX_DB,
            "summary": LOC_ENTITY_INDEX_SUMMARY,
            "schema": "loc_entity(loc_key, entity_kind, entity_key, relation)",
            "entry_count": len(le_rows),
        }

    all_paths.sort()
    text_paths.sort()
    loc_tree_paths.sort()
    ignored_paths.sort()
    write_lines(tree_root / "all.paths.txt", all_paths)
    write_lines(tree_root / "text.paths.txt", text_paths)
    write_lines(tree_root / "localization.paths.txt", loc_tree_paths)
    write_lines(tree_root / "ignored.paths.txt", ignored_paths)

    for suffix_key, paths in suffix_index.items():
        paths.sort()
        safe = "_no_ext" if suffix_key == "(无后缀)" else suffix_key.lstrip(".")
        write_lines(by_suffix_dir / f"{safe}.paths.txt", paths)

    loc_section: dict = {
        "db": LOC_DB_NAME,
        "source_folder": LOC_FOLDER,
        "langs": list(DEFAULT_LANGS),
        "schema": "localization(key, lang, value, source_file) PRIMARY KEY(key, lang)",
        "in_tree": True,
        "yml_copied": False,
        "paths_list": f"{TREE_DIRNAME}/localization.paths.txt",
    }
    if loc_stats is not None:
        loc_section.update(
            {
                "files_parsed": loc_stats.files_parsed,
                "files_skipped": loc_stats.files_skipped,
                "entries_read": loc_stats.entries_read,
                "entries_stored": loc_stats.entries_stored,
                "by_lang": loc_stats.by_lang,
            }
        )

    counts = {
        "total_files_in_tree": len(all_paths),
        "text_copied": copied,
        "text_loc_db_only": loc_in_tree_not_copied,
        "binary_skipped": skipped_binary,
        "ignored": ignored,
        "localization_entries": (loc_stats.entries_stored if loc_stats else 0),
    }
    if common_reg_section is not None:
        counts["common_registry_entries"] = common_reg_section["entry_count"]
    if events_reg_section is not None:
        counts["events_registry_entries"] = events_reg_section["entry_count"]
    if loc_entity_section is not None:
        counts["loc_entity_index_entries"] = loc_entity_section["entry_count"]

    lookup = {
        "all_paths": f"{TREE_DIRNAME}/all.paths.txt",
        "text_paths": f"{TREE_DIRNAME}/text.paths.txt",
        "localization_paths": f"{TREE_DIRNAME}/localization.paths.txt",
        "ignored_paths": f"{TREE_DIRNAME}/ignored.paths.txt",
        "by_folder": (
            f"{TREE_DIRNAME}/by_folder/<folder>.paths.txt|.text.txt|.ignored.txt"
        ),
        "by_suffix": f"{TREE_DIRNAME}/by_suffix/<ext>.paths.txt",
        "localization_db": LOC_DB_NAME,
        "common_registry_db": COMMON_REGISTRY_DB,
        "events_registry_db": EVENTS_REGISTRY_DB,
        "loc_entity_index_db": LOC_ENTITY_INDEX_DB,
        "notes": [
            "HOI4 N=16；仅纯文本进 tree 并复制（loc 除外）",
            "非纯文本直接跳过，不记录路径",
            "源目录 localisation；库文件仍名 localization.sqlite",
            "无 map_data_registry / hub_anchors",
            "events_registry：HOI4 id = ns.n",
            f"{TREE_DIRNAME}/ 本身不出现在路径列表中",
        ],
    }

    manifest = {
        "schema": "patchouli.reduced_hoi4.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_game": str(src_game),
        "dest_game": str(dst_game),
        "scope": "n16_text_only_no_binary_tree",
        "active_folders": list(N_FOLDERS),
        "n_folders": list(N_FOLDERS),
        "localization": loc_section,
        "common_registry": common_reg_section,
        "events_registry": events_reg_section,
        "loc_entity_index": loc_entity_section,
        "missing_folders": missing_folders,
        "tree_dirname": TREE_DIRNAME,
        "tree_excludes_self": True,
        "text_suffixes": sorted(s if s else "(无后缀)" for s in TEXT_SUFFIXES),
        "ignore_suffixes": sorted(IGNORE_SUFFIXES),
        "counts": counts,
        "folders": folder_stats,
        "lookup": lookup,
        "global_by_suffix": [
            {"suffix": k, "count": v}
            for k, v in sorted(
                global_suffix_counter.items(), key=lambda x: (-x[1], x[0])
            )
        ],
        "ignored_by_suffix": [
            {"suffix": k, "count": v}
            for k, v in sorted(
                ignored_suffix_counter.items(), key=lambda x: (-x[1], x[0])
            )
        ],
    }

    (tree_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="构建 HOI4 削减 vanilla（N=16 纯文本 + loc sqlite）"
    )
    parser.add_argument("--game-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    src = args.game_dir or default_game_dir()
    out = args.out or default_out_dir()
    manifest = build(src, out)
    c = manifest["counts"]
    print(f"source={manifest['source_game']}")
    print(f"dest={manifest['dest_game']}")
    print(f"folders={manifest['n_folders']}")
    print(
        f"tree_total={c['total_files_in_tree']} text_copied={c['text_copied']} "
        f"loc_db_only={c['text_loc_db_only']} binary_skipped={c['binary_skipped']} "
        f"ignored={c['ignored']} loc_entries={c['localization_entries']}"
    )
    if common_reg_section := manifest.get("common_registry"):
        print(f"common_registry={common_reg_section['entry_count']}")
    if events_reg_section := manifest.get("events_registry"):
        print(f"events_registry={events_reg_section['entry_count']}")
    loc = manifest["localization"]
    print(
        f"loc_db={loc.get('db')} files={loc.get('files_parsed')} "
        f"by_lang={loc.get('by_lang')}"
    )


if __name__ == "__main__":
    main()
