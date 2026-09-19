#!/usr/bin/env python3
"""HOI4 模组削减：与 vanilla 相同 N=16 tree/纯文本/索引，额外保留 descriptor + thumbnail。

产出：database/data/steam/394360/mods/<workshop_id>/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import repo_root, setup  # noqa: E402

setup()

# 用 1936 的 catalog（pathsetup 默认挂的是 1836）
_HOI4_DATA = repo_root() / "service" / "1936" / "data"
if str(_HOI4_DATA) not in sys.path:
    sys.path.insert(0, str(_HOI4_DATA))

from build_reduced_hoi4 import build  # noqa: E402
from localization_db import (  # noqa: E402
    merge_localization_into_db,
    resolve_loc_root,
)
from mod_catalog import ModCatalog  # noqa: E402

DEFAULT_CATALOG = _HOI4_DATA / "mods_catalog.json"
DEFAULT_MODS_OUT = repo_root() / "database" / "data" / "steam" / "394360" / "mods"
DEFAULT_WORKSHOP = Path.home() / (
    ".steam/debian-installation/steamapps/workshop/content/394360"
)

THUMB_NAMES = (
    "thumbnail.png",
    "thumbnail.jpg",
    "thumbnail.jpeg",
    "thumbnail.gif",
)

_NAME_RE = re.compile(r'^name\s*=\s*"(.*)"\s*$', re.M)
_REPLACE_RE = re.compile(r'^replace_path\s*=\s*"(.*)"\s*$', re.M)
_REMOTE_RE = re.compile(r'^remote_file_id\s*=\s*"?(\d+)"?\s*$', re.M)


def _parse_descriptor(src_mod: Path) -> dict:
    """读 descriptor.mod → name / replace_paths / remote_file_id。"""
    info: dict = {"name": None, "replace_paths": [], "remote_file_id": None}
    cand = src_mod / "descriptor.mod"
    if not cand.is_file():
        mods = sorted(src_mod.glob("*.mod"))
        cand = mods[0] if mods else None
    if cand is None or not cand.is_file():
        return info
    text = cand.read_text(encoding="utf-8", errors="replace")
    m = _NAME_RE.search(text)
    if m:
        info["name"] = m.group(1)
    info["replace_paths"] = _REPLACE_RE.findall(text)
    m = _REMOTE_RE.search(text)
    if m:
        info["remote_file_id"] = m.group(1)
    info["descriptor"] = cand.name
    return info


def _copy_descriptor(src_mod: Path, dst: Path) -> dict:
    copied: list[str] = []
    for name in ("descriptor.mod",):
        src = src_mod / name
        if src.is_file():
            shutil.copy2(src, dst / name)
            copied.append(name)
    for src in sorted(src_mod.glob("*.mod")):
        if src.name == "descriptor.mod":
            continue
        shutil.copy2(src, dst / src.name)
        copied.append(src.name)
    return {"copied": bool(copied), "files": copied}


def _copy_thumbnail(src_mod: Path, dst: Path) -> dict:
    for name in THUMB_NAMES:
        cand = src_mod / name
        if cand.is_file():
            shutil.copy2(cand, dst / name)
            return {"copied": True, "path": name, "from": "root"}
    return {"copied": False}


def _merge_loc_packs(
    dst: Path,
    pack_ids: list[str] | tuple[str, ...],
    *,
    workshop_root: Path,
) -> list[dict]:
    """把社区汉化包 localisation 合并进 dst/localization.sqlite。"""
    db = dst / "localization.sqlite"
    results: list[dict] = []
    if not pack_ids:
        return results
    if not db.is_file():
        return [
            {
                "pack_id": pid,
                "ok": False,
                "error": "localization.sqlite_missing",
            }
            for pid in pack_ids
        ]
    for pid in pack_ids:
        pack_dir = Path(workshop_root) / str(pid)
        loc_root = resolve_loc_root(pack_dir)
        if loc_root is None:
            results.append(
                {
                    "pack_id": pid,
                    "ok": False,
                    "error": f"missing_loc:{pack_dir}",
                }
            )
            continue
        stats = merge_localization_into_db(
            db,
            loc_root,
            langs=("simp_chinese",),
            source_prefix=f"pack:{pid}",
        )
        results.append(
            {
                "pack_id": pid,
                "ok": True,
                "files_parsed": stats.files_parsed,
                "entries_stored": stats.entries_stored,
                "by_lang": dict(stats.by_lang or {}),
            }
        )
    return results


def build_mod(
    src_mod: Path,
    dst: Path,
    *,
    mod_id: str | None = None,
    short: str | None = None,
    name: str | None = None,
    loc_packs: list[str] | tuple[str, ...] | None = None,
    workshop_root: Path | None = None,
) -> dict:
    src_mod = Path(src_mod).resolve()
    dst = Path(dst).resolve()
    if not src_mod.is_dir():
        raise SystemExit(f"模组目录不存在: {src_mod}")

    mid = str(mod_id or src_mod.name)
    desc = _parse_descriptor(src_mod)
    if not name:
        name = desc.get("name") or None

    manifest = build(src_mod, dst)

    desc_info = _copy_descriptor(src_mod, dst)
    thumb_info = _copy_thumbnail(src_mod, dst)
    replace_paths = list(desc.get("replace_paths") or [])

    pack_ids = list(loc_packs or [])
    loc_pack_info: list[dict] = []
    if pack_ids:
        ws = Path(workshop_root or DEFAULT_WORKSHOP).resolve()
        loc_pack_info = _merge_loc_packs(dst, pack_ids, workshop_root=ws)

    mod_summary = {
        "schema": "patchouli.reduced_mod_hoi4.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workshop_id": mid,
        "short": short,
        "name": name,
        "source_mod": str(src_mod),
        "dest": str(dst),
        "descriptor": desc_info,
        "thumbnail": thumb_info,
        "replace_paths": replace_paths,
        "loc_packs": loc_pack_info,
        "reduced_manifest_schema": manifest.get("schema"),
        "counts": manifest.get("counts"),
    }
    (dst / "_mod.json").write_text(
        json.dumps(mod_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    tree_manifest = dst / "_tree" / "manifest.json"
    if tree_manifest.is_file():
        try:
            m = json.loads(tree_manifest.read_text(encoding="utf-8"))
            m["mod"] = {
                "workshop_id": mid,
                "short": short,
                "name": name,
                "descriptor": desc_info,
                "thumbnail": thumb_info,
                "replace_paths": replace_paths,
                "loc_packs": loc_pack_info,
            }
            tree_manifest.write_text(
                json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (json.JSONDecodeError, OSError):
            pass

    return mod_summary


def build_all(
    catalog: ModCatalog,
    *,
    mods_out: Path,
    only_ids: set[str] | None = None,
) -> list[dict]:
    mods_out = Path(mods_out).resolve()
    mods_out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    index: list[dict] = []

    for info in catalog.mods:
        if only_ids is not None and info.id not in only_ids:
            continue
        src = catalog.workshop_mod_dir(info.id)
        dst = mods_out / info.id
        if not src.is_dir():
            entry = {
                "workshop_id": info.id,
                "short": info.short,
                "name": info.name,
                "ok": False,
                "error": f"missing_source:{src}",
            }
            results.append(entry)
            index.append(entry)
            print(f"[SKIP] {info.id} {info.short}: source missing")
            continue
        print(f"[BUILD] {info.id} {info.short} ← {src}")
        summary = build_mod(
            src,
            dst,
            mod_id=info.id,
            short=info.short,
            name=info.name,
            loc_packs=info.loc_packs,
            workshop_root=catalog.workshop_root,
        )
        entry = {
            "workshop_id": info.id,
            "short": info.short,
            "name": info.name,
            "ok": True,
            "dest": str(dst),
            "counts": summary.get("counts"),
            "thumbnail": summary.get("thumbnail"),
            "descriptor": summary.get("descriptor"),
            "replace_paths_n": len(summary.get("replace_paths") or []),
        }
        results.append(summary)
        index.append(entry)
        c = summary.get("counts") or {}
        print(
            f"  ok text={c.get('text_copied')} loc={c.get('localization_entries')} "
            f"thumb={summary.get('thumbnail', {}).get('copied')}"
        )

    index_path = mods_out / "index.json"
    if only_ids is not None and index_path.is_file():
        try:
            prev = json.loads(index_path.read_text(encoding="utf-8"))
            by_id = {
                str(e.get("workshop_id")): e
                for e in (prev.get("entries") or [])
                if isinstance(e, dict) and e.get("workshop_id")
            }
            for e in index:
                by_id[str(e["workshop_id"])] = e
            ordered: list[dict] = []
            seen: set[str] = set()
            for info in catalog.mods:
                if info.id in by_id:
                    ordered.append(by_id[info.id])
                    seen.add(info.id)
            for wid, e in by_id.items():
                if wid not in seen:
                    ordered.append(e)
            index = ordered
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    index_path.write_text(
        json.dumps(
            {
                "schema": "patchouli.mods_index.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mods_out": str(mods_out),
                "entries": index,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 HOI4 削减 mods/<id>")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_MODS_OUT)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--workshop-root", type=Path, default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    catalog = ModCatalog.load(args.catalog)
    if args.workshop_root is not None:
        catalog.workshop_root = Path(args.workshop_root).resolve()
    elif catalog.workshop_app != 394360:
        # catalog 未写根时兜底 HOI4 工坊
        catalog.workshop_root = DEFAULT_WORKSHOP.resolve()

    if args.id:
        only: set[str] | None = set(args.id)
    elif args.all:
        only = None
    else:
        parser.error("请指定 --all 或至少一个 --id")

    build_all(catalog, mods_out=args.out, only_ids=only)


if __name__ == "__main__":
    main()
