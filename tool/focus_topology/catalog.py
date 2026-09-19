"""HOI4 国策树总览：把语料内全部 focus_tree 二分成「专属单 TAG」与「非专属」。

尽量覆盖、不追求 100%：country 里唯一 tag/original_tag → 专属；
default/generic → 非专属；否则启发；仍不唯一 → 非专属。
每棵树必属且只属一类。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .topology import (
    FocusCorpus,
    FocusTree,
    collect_tree_node_ids,
    load_focus_corpus,
    prereq_children_index,
)

# 启发时不当作国家 tag 的词（树 id / 文件名碎片）
_NOISE = {
    "ROOT",
    "FROM",
    "PREV",
    "THIS",
    "OWNER",
    "CONTROLLER",
    "CAPITAL",
    "WAR",
    "GOD",
    "GAW",
    "TFR",
    "ZZZ",
    "NATO",
    "TREE",
    "FOCUS",
    "MAIN",
    "POST",
    "LOST",
    "VOID",
    "FAIL",
    "INITIAL",
    "STARTING",
    "CIVIL",
    "THE",
    "AND",
    "FOR",
    "NEW",
    "OLD",
    "EU",
    "CW",
    "VICTORY",
    "SHARED",
    "JOINT",
    "GENERIC",
    "DEFAULT",
    "RECOVERY",
    "MINORS",
    "CENTRAL",
    "ASIA",
    "SCANDINAVIAN",
    "AMERICAN",
    "AFRICAN",
    "SEA",
    "GOE",
    "WTT",
    "AAT",
    "BBA",
    "NSB",
    "LAR",
    "TOA",
    "MTG",
    "DOD",
    "TFV",
    "WUW",
    "TSR",
    "TAOG",
    "BALTIC",
    "KUMUL",
    "SUEZ",
    "SOUTH",
    "CHINA",
}


def _norm_tag(t: str) -> str:
    t = t.strip()
    if not t or t.upper() in _NOISE:
        return ""
    # 三字母统一大写；更长的保留原样（USB / FAF 等）
    if re.fullmatch(r"[A-Za-z]{3}", t):
        return t.upper()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,7}", t):
        return t.upper() if t.isupper() or len(t) <= 4 else t
    return ""


def _tags_from_country(tree: FocusTree) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tree.tags:
        t = _norm_tag(raw)
        if not t:
            continue
        key = t.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _file_tag_hint(fname: str) -> str | None:
    stem = Path(fname).stem
    low = stem.lower()
    if any(k in low for k in ("shared", "joint", "minors", "generic", "default")):
        return None
    # TFR_national_focus_TAG[_rest]
    m = re.match(r"TFR_national_focus_([A-Za-z0-9]+)", stem, re.I)
    if m:
        return _norm_tag(m.group(1)) or None
    # KR: "GER focus (German Empire)"
    m = re.match(r"^([A-Za-z][A-Za-z0-9]{1,5})\s+focus\b", stem, re.I)
    if m:
        return _norm_tag(m.group(1)) or None
    # vanilla-ish short filename germany.txt — 不可靠，跳过
    return None


def _id_tag_hint(tid: str) -> str | None:
    if tid.lower() in ("generic_focus",):
        return None
    m = re.match(r"^([A-Za-z][A-Za-z0-9]{1,5})_", tid)
    if m:
        pref = m.group(1)
        if pref.lower() in (
            "china",
            "generic",
            "baltic",
            "horn",
            "habsburg",
            "congo",
            "austro",
            "free",
            "warlord",
            "shared",
            "saadabad",
            "abdacom",
            "brittany",
            "macedonia",
            "grove",
            "central",
            "kumul",
            "south",
            "suez",
        ):
            return None
        return _norm_tag(pref) or None
    if re.fullmatch(r"[A-Za-z]{2,6}", tid):
        return _norm_tag(tid) or None
    return None


def _focus_prefix_hint(tree: FocusTree, corp: FocusCorpus) -> str | None:
    locals_ = [nid for nid in tree.local_focus_ids if nid in corp.nodes]
    if len(locals_) < 5:
        return None
    counts: dict[str, int] = defaultdict(int)
    for nid in locals_:
        if "_" not in nid:
            continue
        pref = nid.split("_", 1)[0]
        nt = _norm_tag(pref)
        if not nt or len(nt) != 3:
            continue
        counts[nt] += 1
    if not counts:
        return None
    top, n = max(counts.items(), key=lambda x: x[1])
    if n >= (len(locals_) + 1) // 2:
        return top
    return None


def _heuristic_tags(tree: FocusTree, corp: FocusCorpus) -> tuple[list[str], str]:
    fname = Path(tree.file).name
    # PRC 文件内的 china_* / PRC_* 动态树
    if "PRC" in fname.upper() and (
        tree.id.lower().startswith("china_") or tree.id.upper().startswith("PRC_")
    ):
        return ["PRC"], "heuristic_prc_file"

    it = _id_tag_hint(tree.id)
    ft = _file_tag_hint(fname)
    pt = _focus_prefix_hint(tree, corp)
    cands: list[str] = []
    for x in (it, ft, pt):
        if x:
            cands.append(x)
    uniq: list[str] = []
    seen: set[str] = set()
    for c in cands:
        k = c.upper()
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    if len(uniq) == 1:
        return uniq, "heuristic"
    if len(uniq) > 1:
        if it and ft and it.upper() == ft.upper():
            return [it], "heuristic_agree"
        if ft and pt and ft.upper() == pt.upper():
            return [ft], "heuristic_agree"
        if it and pt and it.upper() == pt.upper():
            return [it], "heuristic_agree"
        # 多信号冲突：不强行专属
        return uniq, "heuristic_multi"
    return [], "none"


def _classify_tree(
    tree: FocusTree,
    corp: FocusCorpus,
    *,
    children: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    present, _missing = collect_tree_node_ids(corp, tree, children=children)
    node_count = len(present)
    country_tags = _tags_from_country(tree)
    if tree.default or tree.id == "generic_focus":
        return {
            "id": tree.id,
            "category": "非专属",
            "tag": None,
            "tags": country_tags,
            "via": "default_or_generic",
            "path": tree.file,
            "start_line": tree.start_line,
            "nodes": node_count,
            "local_focus_count": len(tree.local_focus_ids),
            "shared_focus_count": len(tree.shared_focus_refs),
            "country": tree.country_snippet,
            "default": tree.default,
        }

    if len(country_tags) == 1:
        via, tags = "country", country_tags
    elif len(country_tags) > 1:
        return {
            "id": tree.id,
            "category": "非专属",
            "tag": None,
            "tags": country_tags,
            "via": "multi_tag",
            "path": tree.file,
            "start_line": tree.start_line,
            "nodes": node_count,
            "local_focus_count": len(tree.local_focus_ids),
            "shared_focus_count": len(tree.shared_focus_refs),
            "country": tree.country_snippet,
            "default": tree.default,
        }
    else:
        tags, via = _heuristic_tags(tree, corp)

    if len(tags) == 1 and via != "heuristic_multi":
        return {
            "id": tree.id,
            "category": "专属",
            "tag": tags[0],
            "tags": tags,
            "via": via,
            "path": tree.file,
            "start_line": tree.start_line,
            "nodes": node_count,
            "local_focus_count": len(tree.local_focus_ids),
            "shared_focus_count": len(tree.shared_focus_refs),
            "country": tree.country_snippet,
            "default": tree.default,
        }

    return {
        "id": tree.id,
        "category": "非专属",
        "tag": None,
        "tags": tags,
        "via": via if tags else "unresolved",
        "path": tree.file,
        "start_line": tree.start_line,
        "nodes": node_count,
        "local_focus_count": len(tree.local_focus_ids),
        "shared_focus_count": len(tree.shared_focus_refs),
        "country": tree.country_snippet,
        "default": tree.default,
    }


def _summary_text(
    *,
    total: int,
    exclusive: list[dict[str, Any]],
    non_exclusive: list[dict[str, Any]],
    by_tag: dict[str, list[dict[str, Any]]],
) -> str:
    lines: list[str] = [
        f"focus_tree_catalog: {total} trees "
        f"(专属 {len(exclusive)}, 非专属 {len(non_exclusive)})",
        "",
        "=== 专属（恰好一 TAG）===",
    ]
    for tag in sorted(by_tag.keys(), key=str):
        rows = by_tag[tag]
        ids = ", ".join(r["id"] for r in rows)
        lines.append(f"[{tag}] ({len(rows)}) {ids}")
    lines.append("")
    lines.append("=== 非专属 ===")
    if not non_exclusive:
        lines.append("(none)")
    else:
        for r in non_exclusive:
            extra = ""
            if r.get("tags"):
                extra = f" tags={r['tags']}"
            elif r.get("via"):
                extra = f" ({r['via']})"
            lines.append(f"{r['id']}  非专属  ← {r['path']}:{r['start_line']}{extra}")
    return "\n".join(lines)


def catalog_focus_trees(
    game_root: str | Path,
    *,
    category: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """扫描 game_root/common/national_focus，二分全部 focus_tree。

    category: 可选 ``专属`` / ``非专属`` / ``exclusive`` / ``non_exclusive``
    tag: 可选，只保留专属该 TAG 的树（非专属在指定 tag 时默认不返回）
    """
    root = Path(game_root).resolve()
    focus_dir = root / "common" / "national_focus"
    if not focus_dir.is_dir():
        return {
            "ok": False,
            "error": "no_national_focus_dir",
            "path": str(focus_dir),
        }

    corp = load_focus_corpus(root)
    children = prereq_children_index(corp)
    all_rows = [_classify_tree(t, corp, children=children) for t in corp.trees]

    id_counts: dict[str, int] = defaultdict(int)
    for r in all_rows:
        id_counts[r["id"]] += 1
    dup_ids = sorted(i for i, c in id_counts.items() if c > 1)

    all_excl = sum(1 for r in all_rows if r["category"] == "专属")
    all_non = sum(1 for r in all_rows if r["category"] == "非专属")
    total_all = len(all_rows)

    rows = list(all_rows)
    cat_filter = (category or "").strip().lower()
    if cat_filter in ("专属", "exclusive", "excl"):
        rows = [r for r in rows if r["category"] == "专属"]
    elif cat_filter in ("非专属", "non_exclusive", "non-exclusive", "nonexcl"):
        rows = [r for r in rows if r["category"] == "非专属"]

    tag_f = (tag or "").strip()
    if tag_f:
        tu = tag_f.upper()
        rows = [
            r
            for r in rows
            if r["category"] == "专属" and str(r.get("tag") or "").upper() == tu
        ]

    exclusive = [r for r in rows if r["category"] == "专属"]
    non_exclusive = [r for r in rows if r["category"] == "非专属"]
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in exclusive:
        by_tag[str(r["tag"])].append(r)

    exclusive.sort(key=lambda r: (str(r.get("tag") or ""), r["id"]))
    non_exclusive.sort(key=lambda r: r["id"])
    by_tag_out = {
        k: sorted(v, key=lambda r: r["id"])
        for k, v in sorted(by_tag.items(), key=lambda x: x[0])
    }

    summary = _summary_text(
        total=len(rows),
        exclusive=exclusive,
        non_exclusive=non_exclusive,
        by_tag=by_tag_out,
    )
    if cat_filter or tag_f:
        summary = (
            f"(filtered category={category or '-'} tag={tag or '-'}; "
            f"corpus totals: {total_all} trees, 专属 {all_excl}, 非专属 {all_non})\n"
            + summary
        )

    return {
        "ok": True,
        "stats": {
            "total": total_all,
            "exclusive": all_excl,
            "non_exclusive": all_non,
            "returned": len(rows),
            "duplicate_tree_ids": dup_ids,
        },
        "exclusive_by_tag": by_tag_out,
        "non_exclusive": non_exclusive,
        "trees": rows,
        "summary_text": summary,
        "text": summary,
    }
