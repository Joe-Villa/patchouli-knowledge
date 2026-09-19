"""Render focus trees as treesnap-layout PNGs.

One focus_tree → full PNG + per-component panels (prereq+mex connectivity;
components with ≤10 nodes pooled into one panel).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from paths import (
    REPO,
    tool_root,
    vanilla_root,
)

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_SMALL_LIMIT = 10


def _ensure_paths() -> None:
    for p in (tool_root(), tool_root() / "hoi4treesnap"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def derived_dir() -> Path:
    raw = os.environ.get("FOCUS_DERIVED", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO / "database" / "derived" / "1939").resolve()


def steam_game_dir() -> Path:
    raw = os.environ.get("FOCUS_STEAM_GAME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(
        os.path.expanduser(
            "~/.steam/debian-installation/steamapps/common/Hearts of Iron IV"
        )
    ).resolve()


def steam_mod_dir(workshop_id: str) -> Path:
    raw = os.environ.get("FOCUS_STEAM_MOD", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(
        os.path.expanduser(
            f"~/.steam/debian-installation/steamapps/workshop/content/394360/{workshop_id}"
        )
    ).resolve()


def cache_name(mod_short: str, tag: str, tree_id: str, *, suffix: str = "") -> str:
    parts = [_SAFE.sub("_", mod_short), _SAFE.sub("_", tag), _SAFE.sub("_", tree_id)]
    base = "_".join(parts)
    if suffix:
        base = f"{base}__{_SAFE.sub('_', suffix)}"
    return base + ".png"


def _full_image_api_url(*, mod: str, tag: str, tree_id: str, lang: str) -> str:
    from urllib.parse import urlencode

    q = urlencode(
        {"mod": mod, "tag": tag, "tree_id": tree_id, "lang": lang}
    )
    return f"/api/focus-tree-full?{q}"


def _render_tree_bundle(
    *,
    graph: dict[str, Any],
    short: str,
    tag_u: str,
    tid: str,
    out_dir: Path,
    game_spacing: tuple[int, int],
    path: Any,
    start_line: Any,
    gate: Any,
    mod_query: str,
    lang: str,
) -> dict[str, Any]:
    from layout_png import partition_focus_panels, render_tree_png  # noqa: WPS433

    graph = dict(graph)
    graph["id"] = tid

    # Preview path: panels only, no overlapping-component packer.
    # Full packed PNG is generated lazily on 「下载整图」.
    full_name = cache_name(short, tag_u, tid)
    panels_spec, part_meta = partition_focus_panels(
        graph, small_limit=_SMALL_LIMIT
    )
    panels_out: list[dict[str, Any]] = []

    for spec in panels_spec:
        pid = str(spec["panel_id"])
        fname = cache_name(short, tag_u, tid, suffix=pid)
        render_tree_png(
            spec["graph"],
            out_dir / fname,
            spacing=game_spacing,
            game_dir=steam_game_dir(),
            separate_components=False,
            pack_disconnected=spec.get("kind") == "small_pool",
        )
        panels_out.append(
            {
                "panel_id": pid,
                "kind": spec["kind"],
                "label": spec["label"],
                "node_count": spec["node_count"],
                "component_count": spec["component_count"],
                "stats": (spec["graph"].get("stats") or {}),
                "image": fname,
                "image_url": f"/renders/{fname}",
            }
        )

    st = graph.get("stats") or {}
    return {
        "id": tid,
        "path": path,
        "start_line": start_line,
        "gate": gate,
        "stats": st,
        "image": full_name,
        "image_url": _full_image_api_url(
            mod=mod_query, tag=tag_u, tree_id=tid, lang=lang
        ),
        "full_lazy": True,
        "panels": panels_out,
        "components": part_meta,
    }


def render_focus_trees_png(
    *,
    mod: str,
    tag: str,
    tree_id: str | None = None,
    lang: str = "simp_chinese",
    max_trees: int = 40,
    force: bool = False,
) -> dict[str, Any]:
    del force  # reserved; always re-render for now
    _ensure_paths()
    from focus_topology import (  # noqa: WPS433
        build_tree_graph,
        load_focus_corpus,
        match_trees_for_tag,
    )
    from layout_png import (  # noqa: WPS433
        effective_spacing,
        parse_focus_spacing,
    )

    # reuse catalog resolution from render.py
    from render import resolve_mod_root  # noqa: WPS433

    tag_u = (tag or "").strip().upper()
    if not tag_u or len(tag_u) > 8:
        return {"ok": False, "error": "invalid_tag", "hint": "三字母 tag，如 PRC / GER"}

    try:
        game_root, meta = resolve_mod_root(mod)
    except KeyError as e:
        return {"ok": False, "error": "unknown_mod", "detail": str(e)}
    except FileNotFoundError as e:
        return {"ok": False, "error": "mod_missing", "detail": str(e)}

    focus_dir = game_root / "common" / "national_focus"
    if not focus_dir.is_dir():
        return {
            "ok": False,
            "error": "no_national_focus_dir",
            "path": str(focus_dir),
        }

    short = str(meta.get("short") or meta.get("id") or mod)
    game_spacing = parse_focus_spacing(steam_game_dir())
    spacing = effective_spacing(game_spacing)
    out_dir = derived_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    from focus_topology.derived import (  # noqa: WPS433
        list_trees_for_tag,
        load_index,
        load_tree_graph,
        resolve_derived_dir,
    )
    from focus_topology.names import (  # noqa: WPS433
        apply_names_to_graph,
        lookup_focus_names,
        resolve_loc_dbs,
    )

    loc_dbs = resolve_loc_dbs(game_root, vanilla_root=vanilla_root())

    def _hydrate_names(graph: dict[str, Any]) -> dict[str, Any]:
        """中文 loc → 英文 loc → key。"""
        ids = [
            str(n.get("id"))
            for n in (graph.get("nodes") or [])
            if n.get("id")
        ]
        names = lookup_focus_names(
            loc_dbs, ids, lang=lang, fallback_lang="english"
        )
        return apply_names_to_graph(graph, names)

    focus_pack = resolve_derived_dir(
        game_root=game_root,
        mod_short=short,
        mod_id=str(meta.get("id") or ""),
        repo=REPO,
    )
    trees_out: list[dict[str, Any]] = []
    source = "live"

    if focus_pack is not None:
        index = load_index(focus_pack) or {}
        index_trees = index.get("trees") or {}
        if tree_id:
            want_ids = [tree_id]
        else:
            want_ids = list_trees_for_tag(focus_pack, tag_u)
            # also allow non-exclusive matches by id filter only when tag=_
            if not want_ids and tag_u in {"_", "NON", "ANY"}:
                want_ids = list(index_trees.keys())[:max_trees]
        for tid in want_ids[:max_trees]:
            # case-insensitive lookup in index
            meta_t = index_trees.get(tid)
            if meta_t is None:
                for k, v in index_trees.items():
                    if str(k).lower() == tid.lower():
                        meta_t = v
                        tid = str(k)
                        break
            graph = load_tree_graph(focus_pack, tid)
            if not graph:
                continue
            graph = _hydrate_names(graph)
            trees_out.append(
                _render_tree_bundle(
                    graph=graph,
                    short=short,
                    tag_u=tag_u,
                    tid=tid,
                    out_dir=out_dir,
                    game_spacing=game_spacing,
                    path=graph.get("path") or (meta_t or {}).get("path"),
                    start_line=graph.get("start_line")
                    or (meta_t or {}).get("start_line"),
                    gate=graph.get("gate"),
                    mod_query=short,
                    lang=lang,
                )
            )
        if trees_out:
            source = "derived"
            return {
                "ok": True,
                "mod": meta,
                "tag": tag_u,
                "lang": lang,
                "loc_db": [str(p) for p in loc_dbs] or None,
                "game_root": str(game_root),
                "derived": str(out_dir),
                "focus_pack": str(focus_pack),
                "source": source,
                "spacing": {
                    "game_x": game_spacing[0],
                    "game_y": game_spacing[1],
                    "x": spacing[0],
                    "y": spacing[1],
                },
                "matched_tree_count": len(trees_out),
                "scanned_tree_count": len(index_trees),
                "nodes_indexed": None,
                "trees": trees_out,
            }

    corp = load_focus_corpus(game_root)
    matched = match_trees_for_tag(
        corp, tag_u, tree_id=tree_id, max_trees=max_trees
    )

    all_ids: list[str] = []
    for tree in matched:
        all_ids.extend(tree.local_focus_ids)
        all_ids.extend(tree.shared_focus_refs)

    names = lookup_focus_names(
        loc_dbs, all_ids, lang=lang, fallback_lang="english"
    )

    for tree in matched:
        graph = build_tree_graph(corp, tree, names=names)
        extra_ids = [
            str(n.get("id"))
            for n in (graph.get("nodes") or [])
            if n.get("id") and str(n["id"]) not in names
        ]
        if extra_ids:
            names.update(
                lookup_focus_names(
                    loc_dbs, extra_ids, lang=lang, fallback_lang="english"
                )
            )
            apply_names_to_graph(graph, names)
        trees_out.append(
            _render_tree_bundle(
                graph=graph,
                short=short,
                tag_u=tag_u,
                tid=tree.id,
                out_dir=out_dir,
                game_spacing=game_spacing,
                path=tree.file,
                start_line=tree.start_line,
                gate=graph["gate"],
                mod_query=short,
                lang=lang,
            )
        )

    return {
        "ok": True,
        "mod": meta,
        "tag": tag_u,
        "lang": lang,
        "loc_db": [str(p) for p in loc_dbs] or None,
        "game_root": str(game_root),
        "derived": str(out_dir),
        "source": "live",
        "spacing": {
            "game_x": game_spacing[0],
            "game_y": game_spacing[1],
            "x": spacing[0],
            "y": spacing[1],
        },
        "matched_tree_count": len(trees_out),
        "scanned_tree_count": len(corp.trees),
        "nodes_indexed": len(corp.nodes),
        "trees": trees_out,
    }


def ensure_full_tree_png(
    *,
    mod: str,
    tag: str,
    tree_id: str,
    lang: str = "simp_chinese",
    force: bool = False,
) -> dict[str, Any]:
    """Render the packed full-tree PNG (separate_components=True).

    Used only by 「下载整图」; preview/panels never call this path.
    """
    _ensure_paths()
    from focus_topology import (  # noqa: WPS433
        build_tree_graph,
        load_focus_corpus,
        match_trees_for_tag,
    )
    from layout_png import parse_focus_spacing, render_tree_png  # noqa: WPS433
    from render import resolve_mod_root  # noqa: WPS433

    tid = (tree_id or "").strip()
    if not tid:
        return {"ok": False, "error": "missing_tree_id"}

    tag_u = (tag or "").strip().upper()
    if not tag_u or len(tag_u) > 8:
        return {"ok": False, "error": "invalid_tag"}

    try:
        game_root, meta = resolve_mod_root(mod)
    except KeyError as e:
        return {"ok": False, "error": "unknown_mod", "detail": str(e)}
    except FileNotFoundError as e:
        return {"ok": False, "error": "mod_missing", "detail": str(e)}

    short = str(meta.get("short") or meta.get("id") or mod)
    out_dir = derived_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    full_name = cache_name(short, tag_u, tid)
    out_path = out_dir / full_name
    if out_path.is_file() and not force:
        return {
            "ok": True,
            "cached": True,
            "image": full_name,
            "path": str(out_path),
            "mod": meta,
            "tag": tag_u,
            "tree_id": tid,
        }

    from focus_topology.derived import (  # noqa: WPS433
        load_index,
        load_tree_graph,
        resolve_derived_dir,
    )
    from focus_topology.names import (  # noqa: WPS433
        apply_names_to_graph,
        lookup_focus_names,
        resolve_loc_dbs,
    )

    loc_dbs = resolve_loc_dbs(game_root, vanilla_root=vanilla_root())
    game_spacing = parse_focus_spacing(steam_game_dir())
    graph: dict[str, Any] | None = None

    focus_pack = resolve_derived_dir(
        game_root=game_root,
        mod_short=short,
        mod_id=str(meta.get("id") or ""),
        repo=REPO,
    )
    if focus_pack is not None:
        index = load_index(focus_pack) or {}
        index_trees = index.get("trees") or {}
        real_id = tid
        if tid not in index_trees:
            for k in index_trees:
                if str(k).lower() == tid.lower():
                    real_id = str(k)
                    break
        graph = load_tree_graph(focus_pack, real_id)
        if graph:
            tid = real_id
            ids = [
                str(n.get("id"))
                for n in (graph.get("nodes") or [])
                if n.get("id")
            ]
            names = lookup_focus_names(
                loc_dbs, ids, lang=lang, fallback_lang="english"
            )
            apply_names_to_graph(graph, names)

    if graph is None:
        corp = load_focus_corpus(game_root)
        matched = match_trees_for_tag(
            corp, tag_u, tree_id=tid, max_trees=1
        )
        if not matched:
            return {"ok": False, "error": "tree_not_found", "tree_id": tid}
        tree = matched[0]
        tid = tree.id
        ids = list(tree.local_focus_ids) + list(tree.shared_focus_refs)
        names = lookup_focus_names(
            loc_dbs, ids, lang=lang, fallback_lang="english"
        )
        graph = build_tree_graph(corp, tree, names=names)
        extra_ids = [
            str(n.get("id"))
            for n in (graph.get("nodes") or [])
            if n.get("id") and str(n["id"]) not in names
        ]
        if extra_ids:
            names.update(
                lookup_focus_names(
                    loc_dbs, extra_ids, lang=lang, fallback_lang="english"
                )
            )
            apply_names_to_graph(graph, names)

    graph = dict(graph)
    graph["id"] = tid
    full_name = cache_name(short, tag_u, tid)
    out_path = out_dir / full_name
    render_tree_png(
        graph,
        out_path,
        spacing=game_spacing,
        game_dir=steam_game_dir(),
        separate_components=True,
    )
    return {
        "ok": True,
        "cached": False,
        "image": full_name,
        "path": str(out_path),
        "mod": meta,
        "tag": tag_u,
        "tree_id": tid,
    }
