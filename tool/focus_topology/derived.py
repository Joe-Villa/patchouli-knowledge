"""Pre-parsed HOI4 focus corpus under ``database/derived/focus/{mod}/``.

Layout
------
database/derived/focus/
  registry.json                 # short / workshop id → folder
  by_id/<workshop_id_or_vanilla>/  → symlink or mirror of short folder
  <short>/
    manifest.json               # built_at, fingerprints, stats
    catalog.json                # exclusive_by_tag + non_exclusive + summary
    index.json                  # tree_id → meta (path, exclusive, tags, …)
    trees/<safe_id>.json        # full graph (nodes / prereq / mex / names)

Build once per mod (ingest); services and find-agent read these files instead of
re-parsing ``common/national_focus`` on every request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def repo_root_from_here() -> Path:
    # tool/focus_topology/derived.py → repo
    return Path(__file__).resolve().parents[2]


def focus_derived_root(repo: Path | None = None) -> Path:
    raw = os.environ.get("FOCUS_DERIVED_FOCUS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    root = repo or repo_root_from_here()
    return (root / "database" / "derived" / "focus").resolve()


def safe_tree_filename(tree_id: str) -> str:
    return _SAFE.sub("_", tree_id) + ".json"


def mod_dir(short: str, *, repo: Path | None = None) -> Path:
    return focus_derived_root(repo) / _SAFE.sub("_", short or "unknown")


def registry_path(repo: Path | None = None) -> Path:
    return focus_derived_root(repo) / "registry.json"


def load_registry(repo: Path | None = None) -> dict[str, Any]:
    path = registry_path(repo)
    if not path.is_file():
        return {"mods": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mods": {}}


def save_registry(data: dict[str, Any], *, repo: Path | None = None) -> Path:
    root = focus_derived_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    path = registry_path(repo)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def resolve_derived_dir(
    *,
    game_root: Path | None = None,
    mod_short: str | None = None,
    mod_id: str | None = None,
    repo: Path | None = None,
) -> Path | None:
    """Find derived folder for a mod (by short, id, or game_root folder name)."""
    root = focus_derived_root(repo)
    reg = load_registry(repo).get("mods") or {}

    for key in (mod_short, mod_id):
        if not key:
            continue
        entry = reg.get(str(key)) or reg.get(str(key).lower())
        if isinstance(entry, dict) and entry.get("dir"):
            p = root / str(entry["dir"])
            if (p / "manifest.json").is_file():
                return p

    if game_root is not None:
        name = Path(game_root).resolve().name
        by_id = root / "by_id" / name
        if (by_id / "manifest.json").is_file():
            return by_id.resolve()
        # folder itself may be the short name
        direct = root / name
        if (direct / "manifest.json").is_file():
            return direct
        for entry in reg.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id") or "") == name or str(entry.get("short") or "") == name:
                p = root / str(entry.get("dir") or "")
                if (p / "manifest.json").is_file():
                    return p
    return None


def fingerprint_focus_dir(focus_dir: Path) -> dict[str, Any]:
    """Cheap change detector: sorted path + size + mtime_ns."""
    files: list[dict[str, Any]] = []
    if focus_dir.is_dir():
        for path in sorted(focus_dir.glob("*.txt")):
            try:
                st = path.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": path.name,
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                }
            )
    blob = json.dumps(files, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return {"sha256": digest, "file_count": len(files), "files": files}


def load_manifest(derived: Path) -> dict[str, Any] | None:
    path = derived / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_catalog_json(derived: Path) -> dict[str, Any] | None:
    path = derived / "catalog.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("ok") else data


def load_index(derived: Path) -> dict[str, Any] | None:
    path = derived / "index.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_tree_graph(derived: Path, tree_id: str) -> dict[str, Any] | None:
    index = load_index(derived) or {}
    trees = index.get("trees") or {}
    meta = trees.get(tree_id) or trees.get(tree_id.lower())
    fname = None
    if isinstance(meta, dict):
        fname = meta.get("file")
    if not fname:
        fname = safe_tree_filename(tree_id)
    path = derived / "trees" / str(fname)
    if not path.is_file():
        # case-insensitive scan
        trees_dir = derived / "trees"
        if trees_dir.is_dir():
            want = tree_id.lower()
            for p in trees_dir.glob("*.json"):
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if str(raw.get("id") or "").lower() == want:
                    return raw
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_trees_for_tag(derived: Path, tag: str) -> list[str]:
    """Tree ids that are exclusive to tag, plus non-exclusive (optional later)."""
    tag_u = (tag or "").strip().upper()
    catalog = load_catalog_json(derived) or {}
    by_tag = catalog.get("exclusive_by_tag") or {}
    rows = by_tag.get(tag_u) or by_tag.get(tag) or []
    return [str(r.get("id")) for r in rows if r.get("id")]


def build_focus_derived(
    game_root: Path,
    *,
    mod_meta: dict[str, Any],
    lang: str = "simp_chinese",
    vanilla_root: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Parse full focus corpus → write derived JSON pack. Returns manifest summary."""
    from .catalog import catalog_focus_trees
    from .names import lookup_focus_names, resolve_loc_dbs
    from .topology import build_tree_graph, load_focus_corpus, prereq_children_index

    root = Path(game_root).resolve()
    focus_dir = root / "common" / "national_focus"
    if not focus_dir.is_dir():
        return {
            "ok": False,
            "error": "no_national_focus_dir",
            "path": str(focus_dir),
        }

    short = str(mod_meta.get("short") or mod_meta.get("id") or root.name)
    mid = str(mod_meta.get("id") or short)
    out = mod_dir(short, repo=repo)
    trees_dir = out / "trees"
    trees_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    fp = fingerprint_focus_dir(focus_dir)
    corp = load_focus_corpus(root)
    children = prereq_children_index(corp)
    catalog = catalog_focus_trees(root)
    if not catalog.get("ok"):
        return {"ok": False, "error": "catalog_failed", "detail": catalog}

    loc_dbs = resolve_loc_dbs(root, vanilla_root=vanilla_root)
    all_ids: list[str] = []
    for tree in corp.trees:
        all_ids.extend(tree.local_focus_ids)
        all_ids.extend(tree.shared_focus_refs)
    names = lookup_focus_names(
        loc_dbs, all_ids, lang=lang, fallback_lang="english"
    )

    index_trees: dict[str, Any] = {}
    written = 0
    for tree in corp.trees:
        graph = build_tree_graph(corp, tree, names=names, children=children)
        extra = [
            str(n.get("id"))
            for n in (graph.get("nodes") or [])
            if n.get("id") and str(n["id"]) not in names
        ]
        if extra:
            names.update(
                lookup_focus_names(
                    loc_dbs, extra, lang=lang, fallback_lang="english"
                )
            )
            for n in graph["nodes"]:
                nid = str(n.get("id") or "")
                if nid in names:
                    n["name"] = names[nid]

        fname = safe_tree_filename(tree.id)
        # disambiguate duplicate ids
        if tree.id in index_trees:
            stem = _SAFE.sub("_", tree.id)
            fname = f"{stem}__{written}.json"
        payload = {
            "id": tree.id,
            "path": tree.file,
            "start_line": tree.start_line,
            "mod": {"id": mid, "short": short, "name": mod_meta.get("name")},
            "lang": lang,
            "gate": graph.get("gate"),
            "stats": graph.get("stats"),
            "nodes": graph.get("nodes"),
            "prereq_edges": graph.get("prereq_edges"),
            "mex_edges": graph.get("mex_edges"),
            "missing_shared": graph.get("missing_shared"),
        }
        (trees_dir / fname).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index_trees[tree.id] = {
            "id": tree.id,
            "file": fname,
            "path": tree.file,
            "start_line": tree.start_line,
            "nodes": (graph.get("stats") or {}).get("nodes"),
            "prereq_edges": (graph.get("stats") or {}).get("prereq_edges"),
            "mex_edges": (graph.get("stats") or {}).get("mex_edges"),
        }
        written += 1

    # enrich index with catalog classification
    for row in catalog.get("trees") or []:
        tid = str(row.get("id") or "")
        if tid in index_trees:
            index_trees[tid]["category"] = row.get("category")
            index_trees[tid]["tag"] = row.get("tag")
            index_trees[tid]["tags"] = row.get("tags")
            index_trees[tid]["via"] = row.get("via")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    manifest = {
        "ok": True,
        "schema": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "mod": {"id": mid, "short": short, "name": mod_meta.get("name")},
        "game_root": str(root),
        "lang": lang,
        "loc_db": [str(p) for p in loc_dbs] or None,
        "fingerprint": {"sha256": fp["sha256"], "file_count": fp["file_count"]},
        "stats": {
            "trees": len(corp.trees),
            "nodes_indexed": len(corp.nodes),
            "exclusive": (catalog.get("stats") or {}).get("exclusive"),
            "non_exclusive": (catalog.get("stats") or {}).get("non_exclusive"),
            "build_ms": elapsed_ms,
        },
    }

    catalog_out = {
        "ok": True,
        "mod": manifest["mod"],
        "stats": catalog.get("stats"),
        "exclusive_by_tag": catalog.get("exclusive_by_tag"),
        "non_exclusive": catalog.get("non_exclusive"),
        "summary_text": catalog.get("summary_text"),
        "source": "derived",
    }
    index_out = {
        "ok": True,
        "mod": manifest["mod"],
        "tree_count": len(index_trees),
        "trees": index_trees,
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "catalog.json").write_text(
        json.dumps(catalog_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "index.json").write_text(
        json.dumps(index_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # by_id pointer (symlink when possible)
    by_id_root = focus_derived_root(repo) / "by_id"
    by_id_root.mkdir(parents=True, exist_ok=True)
    link = by_id_root / mid
    rel_target = os.path.relpath(out, start=by_id_root)
    try:
        if link.is_symlink():
            link.unlink()
        elif link.is_file():
            link.unlink()
        if not link.exists():
            link.symlink_to(rel_target, target_is_directory=True)
    except OSError:
        (by_id_root / f"{mid}.json").write_text(
            json.dumps({"dir": short}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    reg = load_registry(repo)
    mods = dict(reg.get("mods") or {})
    entry = {
        "id": mid,
        "short": short,
        "name": mod_meta.get("name"),
        "dir": short,
        "built_at": manifest["built_at"],
        "fingerprint": manifest["fingerprint"]["sha256"],
    }
    mods[short] = entry
    mods[mid] = entry
    mods[short.lower()] = entry
    save_registry({"mods": mods, "updated_at": manifest["built_at"]}, repo=repo)

    return {
        "ok": True,
        "derived": str(out),
        "manifest": manifest,
        "catalog_stats": catalog.get("stats"),
    }
