#!/usr/bin/env python3
"""模组削减：与 vanilla 相同 N=13 tree/纯文本/索引，额外保留 metadata + thumbnail。

产出：核心模块/data/mods/<workshop_id>/
  N=13 纯文本树（localization 不落 yml）
  localization.sqlite + 各 registry
  .metadata/metadata.json
  thumbnail.png（若存在）
  _mod.json                   （模组侧摘要）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))
from _pathsetup import setup  # noqa: E402

setup()

from build_reduced_game import build  # noqa: E402
from mod_catalog import DEFAULT_CATALOG, DEFAULT_MODS_OUT, ModCatalog  # noqa: E402

THUMB_NAMES = ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg")


def _copy_metadata(src_mod: Path, dst: Path) -> dict:
    """复制 .metadata/（若有）；返回摘要。"""
    src_meta = src_mod / ".metadata"
    info: dict = {"copied": False, "files": []}
    if not src_meta.is_dir():
        # 少数模组把 metadata.json 放在根
        root_meta = src_mod / "metadata.json"
        if root_meta.is_file():
            dest = dst / ".metadata"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_meta, dest / "metadata.json")
            info = {"copied": True, "files": [".metadata/metadata.json"], "from": "root"}
        return info

    dest = dst / ".metadata"
    dest.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for p in sorted(src_meta.iterdir()):
        if p.is_file():
            shutil.copy2(p, dest / p.name)
            files.append(f".metadata/{p.name}")
    info = {"copied": bool(files), "files": files, "from": ".metadata"}
    return info


def _copy_thumbnail(src_mod: Path, dst: Path) -> dict:
    """优先模组根 thumbnail；否则 .metadata 内。"""
    for name in THUMB_NAMES:
        cand = src_mod / name
        if cand.is_file():
            shutil.copy2(cand, dst / name)
            return {"copied": True, "path": name, "from": "root"}
    meta = src_mod / ".metadata"
    if meta.is_dir():
        for name in THUMB_NAMES:
            cand = meta / name
            if cand.is_file():
                # 也落到模组削减根，方便一眼看到
                shutil.copy2(cand, dst / name)
                return {"copied": True, "path": name, "from": ".metadata"}
    return {"copied": False}


def build_mod(
    src_mod: Path,
    dst: Path,
    *,
    mod_id: str | None = None,
    short: str | None = None,
    name: str | None = None,
) -> dict:
    src_mod = Path(src_mod).resolve()
    dst = Path(dst).resolve()
    if not src_mod.is_dir():
        raise SystemExit(f"模组目录不存在: {src_mod}")

    mid = str(mod_id or src_mod.name)

    # 复用 vanilla 削减管线（会 soft_remove dst）
    manifest = build(src_mod, dst)

    meta_info = _copy_metadata(src_mod, dst)
    thumb_info = _copy_thumbnail(src_mod, dst)

    replace_paths: list[str] = []
    meta_json = dst / ".metadata" / "metadata.json"
    if meta_json.is_file():
        try:
            raw = json.loads(meta_json.read_text(encoding="utf-8"))
            rp = raw.get("replace_paths")
            if rp is None and isinstance(raw.get("game_custom_data"), dict):
                rp = raw["game_custom_data"].get("replace_paths")
            if isinstance(rp, list):
                replace_paths = [str(x) for x in rp]
            if not name:
                name = str(raw.get("name") or "") or None
        except (json.JSONDecodeError, OSError):
            pass

    mod_summary = {
        "schema": "patchouli.reduced_mod.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workshop_id": mid,
        "short": short,
        "name": name,
        "source_mod": str(src_mod),
        "dest": str(dst),
        "metadata": meta_info,
        "thumbnail": thumb_info,
        "replace_paths": replace_paths,
        "reduced_manifest_schema": manifest.get("schema"),
        "counts": manifest.get("counts"),
    }
    (dst / "_mod.json").write_text(
        json.dumps(mod_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 在 tree manifest 上挂模组字段（不破坏原 schema 阅读）
    tree_manifest = dst / "_tree" / "manifest.json"
    if tree_manifest.is_file():
        try:
            m = json.loads(tree_manifest.read_text(encoding="utf-8"))
            m["mod"] = {
                "workshop_id": mid,
                "short": short,
                "name": name,
                "metadata": meta_info,
                "thumbnail": thumb_info,
                "replace_paths": replace_paths,
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
        )
        entry = {
            "workshop_id": info.id,
            "short": info.short,
            "name": info.name,
            "ok": True,
            "dest": str(dst),
            "counts": summary.get("counts"),
            "thumbnail": summary.get("thumbnail"),
            "metadata": summary.get("metadata"),
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
    # --id 局部构建时合并进已有 index，避免把其它模组条目冲掉
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
            # 按 catalog 顺序输出；catalog 外残留条目附在末尾
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
    parser = argparse.ArgumentParser(description="构建削减 data/mods/<id>")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="mods_catalog.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_MODS_OUT,
        help="削减模组输出根目录",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="只构建指定 workshop id（可重复）",
    )
    parser.add_argument(
        "--workshop-root",
        type=Path,
        default=None,
        help="覆盖 catalog 中的工坊根目录",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="构建 catalog 内全部模组",
    )
    args = parser.parse_args()

    catalog = ModCatalog.load(args.catalog)
    if args.workshop_root is not None:
        catalog.workshop_root = Path(args.workshop_root).resolve()

    only: set[str] | None
    if args.id:
        only = set(args.id)
    elif args.all:
        only = None
    else:
        parser.error("请指定 --all 或至少一个 --id")

    build_all(catalog, mods_out=args.out, only_ids=only)


if __name__ == "__main__":
    main()
