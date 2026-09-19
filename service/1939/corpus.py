"""Resolve mod → game_root (same reduced corpus layout as 1936) and focus-tree overview."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from paths import (
    mod_start_path,
    mods_catalog_path,
    mods_root,
    tool_root,
    vanilla_root,
)


def _ensure_tool_path() -> None:
    root = str(tool_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def load_catalog() -> dict[str, Any]:
    path = mods_catalog_path()
    if not path.is_file():
        return {"mods": []}
    return json.loads(path.read_text(encoding="utf-8"))


def list_mods() -> list[dict[str, Any]]:
    """Vanilla first, then mods by start year; label = ``(2020) Name``."""
    _ensure_tool_path()
    from hoi4_mod_start import (  # noqa: WPS433
        enrich_mod_entry,
        load_mod_start_index,
        sort_mod_entries,
    )

    index = load_mod_start_index(mod_start_path())
    vanilla = enrich_mod_entry(
        {
            "id": "vanilla",
            "short": "vanilla",
            "name": "Vanilla (本体)",
            "category": "base",
        },
        index,
        is_vanilla=True,
    )
    mods = [
        enrich_mod_entry(m, index)
        for m in load_catalog().get("mods") or []
        if m.get("id")
    ]
    return [vanilla, *sort_mod_entries(mods)]


def resolve_mod_root(choice: str) -> tuple[Path, dict[str, Any]]:
    """Return (game_root, meta). choice: vanilla | workshop id | short/alias."""
    raw = (choice or "").strip()
    if not raw or raw.lower() in {"vanilla", "van", "base"}:
        root = vanilla_root()
        if not root.is_dir():
            raise FileNotFoundError(f"vanilla root missing: {root}")
        return root, {"id": "vanilla", "short": "vanilla", "name": "Vanilla (本体)"}

    catalog = load_catalog()
    mods = catalog.get("mods") or []
    key = raw.lower()
    hit: dict[str, Any] | None = None
    for m in mods:
        mid = str(m.get("id") or "")
        short = str(m.get("short") or "")
        aliases = [str(a).lower() for a in (m.get("aliases") or [])]
        if key == mid.lower() or key == short.lower() or key in aliases:
            hit = m
            break
        name = str(m.get("name") or "").lower()
        if key == name:
            hit = m
            break

    if hit is None and raw.isdigit():
        hit = {"id": raw, "short": raw, "name": raw}

    if hit is None:
        raise KeyError(f"unknown mod: {choice}")

    mid = str(hit["id"])
    root = mods_root() / mid
    if not root.is_dir():
        raise FileNotFoundError(f"mod root missing: {root}")
    return root, {
        "id": mid,
        "short": hit.get("short") or mid,
        "name": hit.get("name") or mid,
    }


def focus_tree_overview(*, mod: str, lang: str = "simp_chinese") -> dict[str, Any]:
    """Parse all focus_tree in the selected corpus; exclusive vs non-exclusive."""
    _ensure_tool_path()
    from focus_topology import catalog_focus_trees  # noqa: WPS433
    from focus_topology.derived import (  # noqa: WPS433
        load_catalog_json,
        resolve_derived_dir,
    )
    from focus_topology.names import (  # noqa: WPS433
        format_tag_label,
        lookup_tag_names,
        resolve_loc_db,
    )
    from paths import REPO  # noqa: WPS433

    try:
        game_root, meta = resolve_mod_root(mod)
    except KeyError as e:
        return {"ok": False, "error": "unknown_mod", "detail": str(e)}
    except FileNotFoundError as e:
        return {"ok": False, "error": "mod_missing", "detail": str(e)}

    short = str(meta.get("short") or meta.get("id") or mod)
    focus_pack = resolve_derived_dir(
        game_root=game_root,
        mod_short=short,
        mod_id=str(meta.get("id") or ""),
        repo=REPO,
    )
    result = None
    source = "live"
    if focus_pack is not None:
        cached = load_catalog_json(focus_pack)
        if cached and cached.get("ok"):
            result = cached
            source = "derived"
    if result is None:
        result = catalog_focus_trees(game_root)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "catalog_failed",
            "detail": result,
            "mod": meta,
            "game_root": str(game_root),
        }

    loc_db = resolve_loc_db(game_root, vanilla_root=vanilla_root())
    tag_set: list[str] = []
    by_tag = result.get("exclusive_by_tag") or {}
    for tag in by_tag:
        tag_set.append(str(tag))
    for t in result.get("non_exclusive") or []:
        for x in t.get("tags") or []:
            tag_set.append(str(x))
    tag_names = lookup_tag_names(loc_db, tag_set, lang=lang)

    def _label(tag: str) -> str:
        return format_tag_label(tag, tag_names.get(tag) or tag_names.get(tag.upper()))

    exclusive_tags: list[dict[str, Any]] = []
    for tag, trees in by_tag.items():
        exclusive_tags.append(
            {
                "tag": tag,
                "label": _label(str(tag)),
                "tree_count": len(trees),
                "trees": [t.get("id") for t in trees],
                "tree_details": [
                    {
                        "id": t.get("id"),
                        "path": t.get("path"),
                        "start_line": t.get("start_line"),
                        "nodes": t.get("nodes"),
                        "local_focus_count": t.get("local_focus_count"),
                        "via": t.get("via"),
                    }
                    for t in trees
                ],
            }
        )
    # 严格按 TAG 字母序；非纯字母（含数字等）沉底
    def _tag_sort_key(row: dict[str, Any]) -> tuple[int, str]:
        tag = str(row["tag"])
        return (0 if tag.isalpha() else 1, tag.upper())

    exclusive_tags.sort(key=_tag_sort_key)

    non_rows = result.get("non_exclusive") or []
    non_exclusive = {
        "count": len(non_rows),
        "trees": [
            {
                "id": t.get("id"),
                "path": t.get("path"),
                "start_line": t.get("start_line"),
                "tags": t.get("tags") or [],
                "tag_labels": [_label(str(x)) for x in (t.get("tags") or [])],
                "via": t.get("via"),
                "nodes": t.get("nodes"),
                "local_focus_count": t.get("local_focus_count"),
            }
            for t in non_rows
        ],
    }

    stats = result.get("stats") or {}
    overview_stats = {
        "total_trees": stats.get("total", 0),
        "exclusive_trees": stats.get("exclusive", 0),
        "non_exclusive_trees": stats.get("non_exclusive", 0),
        "tags_with_exclusive": len(exclusive_tags),
        "duplicate_tree_ids": stats.get("duplicate_tree_ids") or [],
    }

    # rebuild summary with name(tag)
    lines = [
        f"focus_tree_catalog: {overview_stats['total_trees']} trees "
        f"(专属 {overview_stats['exclusive_trees']}, "
        f"非专属 {overview_stats['non_exclusive_trees']})",
        "",
        "=== 专属（恰好一 TAG）===",
    ]
    for row in exclusive_tags:
        ids = ", ".join(str(x) for x in row["trees"])
        lines.append(f"[{row['label']}] ({row['tree_count']}) {ids}")
    lines.append("")
    lines.append("=== 非专属 ===")
    if not non_exclusive["trees"]:
        lines.append("(none)")
    else:
        for t in non_exclusive["trees"]:
            labels = t.get("tag_labels") or []
            extra = f" tags={labels}" if labels else f" ({t.get('via')})"
            lines.append(
                f"{t['id']}  非专属  ← {t['path']}:{t.get('start_line')}{extra}"
            )
    summary = "\n".join(lines)

    return {
        "ok": True,
        "mod": meta,
        "game_root": str(game_root),
        "lang": lang,
        "source": source,
        "focus_pack": str(focus_pack) if focus_pack else None,
        "stats": overview_stats,
        "exclusive_tags": exclusive_tags,
        "non_exclusive": non_exclusive,
        "summary_text": summary,
    }
