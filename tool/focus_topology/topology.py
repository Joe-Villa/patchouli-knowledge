"""HOI4 国策拓扑：把 focus 点连成 prerequisite 边，再聚成树/分支。

只做确定性解析，不渲染贴图。供找侧 focus_topology tool 使用。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_FOCUS_DIR = "common/national_focus"
_BLOCK_START = re.compile(r"(?m)^([ \t]*)(focus_tree|focus|shared_focus)\s*=\s*\{")
_ID = re.compile(r"(?m)^[ \t]*id\s*=\s*([^\s#]+)")
# country 挂树条件里的 tag / original_tag（分类与匹配共用）
_COUNTRY_TAG = re.compile(r"\b(?:tag|original_tag)\s*=\s*([A-Za-z0-9_]+)")
_DEFAULT = re.compile(r"(?m)^[ \t]*default\s*=\s*(yes|no)")
_SCALAR_FOCUS = re.compile(r"(?m)^[ \t]*shared_focus\s*=\s*([^\s{#]+)")
_XY = re.compile(r"(?m)^[ \t]*([xy])\s*=\s*(-?\d+)")
_COST = re.compile(r"(?m)^[ \t]*cost\s*=\s*([0-9.]+)")
_REL = re.compile(r"(?m)^[ \t]*relative_position_id\s*=\s*([^\s#]+)")
_PREREQ_BLOCK = re.compile(r"prerequisite\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
_MEX_BLOCK = re.compile(r"mutually_exclusive\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
_FOCUS_REF = re.compile(r"focus\s*=\s*([^\s#}+]+)")
_ALLOW_BRANCH = re.compile(r"allow_branch\s*=\s*\{")
_FLAG = re.compile(r"has_country_flag\s*=\s*([^\s#}]+)")
_GFLAG = re.compile(r"has_global_flag\s*=\s*([^\s#}]+)")
_COMMENT_LINE = re.compile(r"#.*$")


def _strip_line_comments(text: str) -> str:
    return "\n".join(_COMMENT_LINE.sub("", line) for line in text.splitlines())


def _prepare(text: str) -> str:
    try:
        from common_registry import prepare_content  # type: ignore

        return prepare_content(text)
    except Exception:
        return _strip_line_comments(text)


def _matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _offset_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _condense_trigger(body: str, *, limit: int = 160) -> str | None:
    m = _ALLOW_BRANCH.search(body)
    if not m:
        return None
    brace = body.find("{", m.start())
    if brace < 0:
        return None
    end = _matching_brace(body, brace)
    if end < 0:
        return None
    raw = " ".join(body[brace + 1 : end].split())
    if len(raw) > limit:
        return raw[:limit] + "…"
    return raw or None


@dataclass
class FocusNode:
    id: str
    kind: str  # focus | shared_focus
    file: str
    start_line: int
    x: int | None = None
    y: int | None = None
    cost: float | None = None
    relative_position_id: str | None = None
    prerequisites: list[list[str]] = field(default_factory=list)
    mutually_exclusive: list[str] = field(default_factory=list)
    allow_branch: str | None = None

    def prereq_parents(self) -> set[str]:
        out: set[str] = set()
        for group in self.prerequisites:
            out.update(group)
        return out


@dataclass
class FocusTree:
    id: str
    file: str
    start_line: int
    tags: list[str] = field(default_factory=list)
    country_flags: list[str] = field(default_factory=list)
    global_flags: list[str] = field(default_factory=list)
    country_snippet: str = ""
    shared_focus_refs: list[str] = field(default_factory=list)
    local_focus_ids: list[str] = field(default_factory=list)
    default: bool = False


def _top_level_xy(body: str) -> dict[str, int]:
    """Focus layout x/y only — ignore nested x/y (division templates etc.).

    Treesnap reads direct focus fields; scanning the whole body lets the last
    nested ``y = 1`` inside completion_reward overwrite ``y = 4``.
    """
    matches = list(_XY.finditer(body))
    if not matches:
        return {}

    def _indent(m: re.Match[str]) -> int:
        line_start = body.rfind("\n", 0, m.start()) + 1
        return m.start() - line_start

    min_indent = min(_indent(m) for m in matches)
    xy: dict[str, int] = {}
    for m in matches:
        if _indent(m) != min_indent:
            continue
        key = m.group(1)
        # first top-level wins (layout coords appear once at this indent)
        if key not in xy:
            xy[key] = int(m.group(2))
    return xy


def _parse_focus_body(
    body: str,
    *,
    kind: str,
    file: str,
    start_line: int,
) -> FocusNode | None:
    mid = _ID.search(body)
    if not mid:
        return None
    fid = mid.group(1)
    xy = _top_level_xy(body)
    cost_m = _COST.search(body)
    rel_m = _REL.search(body)
    prereqs: list[list[str]] = []
    for m in _PREREQ_BLOCK.finditer(body):
        refs = _FOCUS_REF.findall(m.group(1))
        if refs:
            prereqs.append(refs)
    mex: list[str] = []
    for m in _MEX_BLOCK.finditer(body):
        mex.extend(_FOCUS_REF.findall(m.group(1)))
    # de-dupe mex keep order
    seen: set[str] = set()
    mex_u: list[str] = []
    for x in mex:
        if x not in seen:
            seen.add(x)
            mex_u.append(x)
    return FocusNode(
        id=fid,
        kind=kind,
        file=file,
        start_line=start_line,
        x=xy.get("x"),
        y=xy.get("y"),
        cost=float(cost_m.group(1)) if cost_m else None,
        relative_position_id=rel_m.group(1) if rel_m else None,
        prerequisites=prereqs,
        mutually_exclusive=mex_u,
        allow_branch=_condense_trigger(body),
    )


def _parse_tree_body(
    body: str,
    *,
    file: str,
    start_line: int,
    local_focus_ids: list[str] | None = None,
) -> FocusTree | None:
    mid = _ID.search(body)
    if not mid:
        return None
    tid = mid.group(1)
    cm = re.search(r"(?m)^[ \t]*country\s*=\s*\{", body)
    country_snip = ""
    tags: list[str] = []
    cflags: list[str] = []
    gflags: list[str] = []
    if cm:
        brace = body.find("{", cm.start())
        end = _matching_brace(body, brace) if brace >= 0 else -1
        if end > brace:
            country_snip = " ".join(body[brace + 1 : end].split())[:240]
            cblock = body[brace : end + 1]
            tags = _COUNTRY_TAG.findall(cblock)
            cflags = _FLAG.findall(cblock)
            gflags = _GFLAG.findall(cblock)
    dm = _DEFAULT.search(body)
    is_default = bool(dm and dm.group(1) == "yes")

    shared_refs = _SCALAR_FOCUS.findall(body)
    seen: set[str] = set()
    refs_u: list[str] = []
    for r in shared_refs:
        if r not in seen:
            seen.add(r)
            refs_u.append(r)

    return FocusTree(
        id=tid,
        file=file,
        start_line=start_line,
        tags=list(dict.fromkeys(tags)),
        country_flags=list(dict.fromkeys(cflags)),
        global_flags=list(dict.fromkeys(gflags)),
        country_snippet=country_snip,
        shared_focus_refs=refs_u,
        local_focus_ids=list(dict.fromkeys(local_focus_ids or [])),
        default=is_default,
    )


@dataclass
class FocusCorpus:
    game_root: Path
    trees: list[FocusTree] = field(default_factory=list)
    nodes: dict[str, FocusNode] = field(default_factory=dict)
    # id -> files declaring it (debug)
    node_files: dict[str, list[str]] = field(default_factory=dict)


def load_focus_corpus(game_root: Path) -> FocusCorpus:
    root = Path(game_root).resolve()
    focus_dir = root / _FOCUS_DIR
    corp = FocusCorpus(game_root=root)
    if not focus_dir.is_dir():
        return corp

    for path in sorted(focus_dir.glob("*.txt")):
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        text = _prepare(raw)
        rel = path.relative_to(root).as_posix()

        depth = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
                i += 1
                continue
            if ch == "}":
                depth -= 1
                i += 1
                continue
            if depth == 0:
                m = _BLOCK_START.match(text, i)
                if m and m.start() == i:
                    kind = m.group(2)
                    brace = m.end() - 1
                    end = _matching_brace(text, brace)
                    if end < 0:
                        break
                    body = text[brace : end + 1]
                    start_line = _offset_line(text, m.start())
                    if kind == "focus_tree":
                        nested_ids = _index_nested_nodes(
                            corp, body, file=rel, base_line=start_line
                        )
                        tree = _parse_tree_body(
                            body,
                            file=rel,
                            start_line=start_line,
                            local_focus_ids=nested_ids,
                        )
                        if tree:
                            corp.trees.append(tree)
                    elif kind in ("focus", "shared_focus"):
                        node = _parse_focus_body(
                            body, kind=kind, file=rel, start_line=start_line
                        )
                        if node:
                            _put_node(corp, node)
                    i = end + 1
                    continue
            i += 1
    return corp


def _put_node(corp: FocusCorpus, node: FocusNode) -> None:
    # later file wins for same id (mod overwrite order is filename-sorted here)
    corp.nodes[node.id] = node
    corp.node_files.setdefault(node.id, []).append(node.file)


def _index_nested_nodes(
    corp: FocusCorpus, tree_body: str, *, file: str, base_line: int
) -> list[str]:
    """Index focus/shared_focus blocks nested inside a focus_tree body."""
    found: list[str] = []
    depth = 0
    i = 0
    while i < len(tree_body):
        ch = tree_body[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            i += 1
            continue
        if depth == 1:
            m = _BLOCK_START.match(tree_body, i)
            if m and m.start() == i:
                kind = m.group(2)
                if kind in ("focus", "shared_focus"):
                    brace = m.end() - 1
                    end = _matching_brace(tree_body, brace)
                    if end < 0:
                        break
                    body = tree_body[brace : end + 1]
                    start_line = base_line + tree_body[: m.start()].count("\n")
                    node = _parse_focus_body(
                        body, kind=kind, file=file, start_line=start_line
                    )
                    if node:
                        _put_node(corp, node)
                        found.append(node.id)
                    i = end + 1
                    continue
        i += 1
    return list(dict.fromkeys(found))


def prereq_children_index(corp: FocusCorpus) -> dict[str, list[str]]:
    """parent focus id → children that list it in any prerequisite group."""
    children: dict[str, list[str]] = defaultdict(list)
    for nid, node in corp.nodes.items():
        for p in node.prereq_parents():
            children[p].append(nid)
    return children


def relative_children_index(corp: FocusCorpus) -> dict[str, list[str]]:
    """parent focus id → nodes that set ``relative_position_id = parent``.

    Shared branches attached via ``shared_focus = <entry>`` often chain only
    through relative layout (no prerequisite). ParadoxArt expands membership
    this way; we do the same in addition to prereq descent.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for nid, node in corp.nodes.items():
        rel = node.relative_position_id
        if rel:
            children[rel].append(nid)
    return children


def collect_tree_node_ids(
    corp: FocusCorpus,
    tree: FocusTree,
    *,
    children: dict[str, list[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve all focuses that belong to a focus_tree for display/topology.

    Seeds = nested local focuses + ``shared_focus = <id>`` entry points on the
    tree. Membership then expands by:

    - prerequisite children (downward) from every included node — covers
      TNO/TFR-style shared chains that only wire prerequisites
    - ``relative_position_id`` children (downward) **only from the shared
      attachment subgraph** (``shared_focus =`` refs, nested ``shared_focus``
      blocks, and nodes reached from them) — ParadoxArt-style layout-only
      shared branches, without pulling unrelated shared nodes that merely
      use a local focus as a relative anchor
    - ``relative_position_id`` ancestors (upward) for absolute layout

    ``children`` is an optional precomputed prereq index; relative-position
    children are always derived from ``corp``.

    Returns (present_ids, missing_seed_ids).
    """
    seeds: list[str] = list(tree.local_focus_ids)
    for ref in tree.shared_focus_refs:
        if ref not in seeds:
            seeds.append(ref)

    missing = [nid for nid in seeds if nid not in corp.nodes]
    present: list[str] = [nid for nid in seeds if nid in corp.nodes]
    if not present:
        return present, missing

    # Shared-attachment frontier: scalar shared_focus= refs + nested shared_focus {}.
    shared_origin: set[str] = {
        ref for ref in tree.shared_focus_refs if ref in corp.nodes
    }
    for nid in tree.local_focus_ids:
        node = corp.nodes.get(nid)
        if node is not None and node.kind == "shared_focus":
            shared_origin.add(nid)

    prereq_map = children if children is not None else prereq_children_index(corp)
    rel_map = relative_children_index(corp)
    seen: set[str] = set(present)
    queue = list(present)
    qi = 0
    while qi < len(queue):
        nid = queue[qi]
        qi += 1
        for child in prereq_map.get(nid, []):
            if child in seen or child not in corp.nodes:
                continue
            seen.add(child)
            queue.append(child)
            present.append(child)
            if nid in shared_origin:
                shared_origin.add(child)
        # Rel-descent only along the shared attachment subgraph.
        if nid in shared_origin:
            for child in rel_map.get(nid, []):
                if child in seen or child not in corp.nodes:
                    continue
                seen.add(child)
                shared_origin.add(child)
                queue.append(child)
                present.append(child)

    # Ancestors for relative layout (shared chains often only list entry points).
    qi = 0
    while qi < len(queue):
        nid = queue[qi]
        qi += 1
        node = corp.nodes.get(nid)
        if not node:
            continue
        rel = node.relative_position_id
        if rel and rel not in seen and rel in corp.nodes:
            seen.add(rel)
            queue.append(rel)
            present.append(rel)

    return present, missing


def _tag_matches_tree(tree: FocusTree, tag: str, *, nodes: dict[str, FocusNode]) -> bool:
    t = tag.strip().upper()
    if not t:
        return False
    # 1) country 块显式 tag：以此为准（同文件多棵树时避免文件名误伤）
    if tree.tags:
        return any(x.upper() == t for x in tree.tags)

    # 2) 无显式 tag 时：树 id / 文件名 / 国策 id 前缀
    #    覆盖如 PRC_taiwan_war_tree（country 仅 factor=0、无 tag=）
    tid = tree.id.upper()
    if tid == t or tid.startswith(t + "_") or f"_{t}_" in f"_{tid}_":
        return True
    fname = Path(tree.file).stem.upper()
    # 要求 _TAG_ 或 _TAG 结尾，避免短 tag 误伤
    padded = f"_{fname}_"
    if f"_{t}_" in padded or fname.endswith(f"_{t}") or fname == t:
        return True
    locals_ = [nid for nid in tree.local_focus_ids if nid in nodes]
    if len(locals_) >= 3:
        pref = sum(1 for nid in locals_ if nid.upper().startswith(t + "_"))
        if pref >= (len(locals_) + 1) // 2:
            return True
    return False


def build_tree_graph(
    corp: FocusCorpus,
    tree: FocusTree,
    *,
    names: dict[str, str] | None = None,
    children: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """完整可渲染图：nodes + prerequisite 边 + mutually_exclusive 边。"""
    name_map = names or {}
    present, missing = collect_tree_node_ids(corp, tree, children=children)
    focus_nodes = {nid: corp.nodes[nid] for nid in present}

    prereq_edges: list[list[str]] = []
    seen_e: set[tuple[str, str]] = set()
    for nid, node in focus_nodes.items():
        for p in node.prereq_parents():
            if p not in focus_nodes:
                continue
            key = (p, nid)
            if key in seen_e:
                continue
            seen_e.add(key)
            prereq_edges.append([p, nid])

    mex_edges: list[list[str]] = []
    seen_m: set[tuple[str, str]] = set()
    for nid, node in focus_nodes.items():
        for other in node.mutually_exclusive:
            if other not in focus_nodes:
                continue
            a, b = sorted((nid, other))
            key = (a, b)
            if key in seen_m:
                continue
            seen_m.add(key)
            mex_edges.append([a, b])

    nodes_out: list[dict[str, Any]] = []
    for nid in present:
        node = focus_nodes[nid]
        item: dict[str, Any] = {
            "id": nid,
            "kind": node.kind,
            "x": node.x,
            "y": node.y,
            "cost": node.cost,
            "relative_position_id": node.relative_position_id,
            "mutually_exclusive": list(node.mutually_exclusive),
            "prerequisites": [list(g) for g in node.prerequisites],
        }
        label = name_map.get(nid) or nid
        item["name"] = label
        if node.allow_branch:
            item["allow_branch"] = node.allow_branch
        nodes_out.append(item)

    return {
        "id": tree.id,
        "path": tree.file,
        "start_line": tree.start_line,
        "gate": {
            "tags": tree.tags,
            "country_flags": tree.country_flags,
            "global_flags": tree.global_flags,
            "country_snippet": tree.country_snippet,
        },
        "stats": {
            "nodes": len(present),
            "missing_shared": len(missing),
            "prereq_edges": len(prereq_edges),
            "mex_edges": len(mex_edges),
        },
        "nodes": nodes_out,
        "prereq_edges": prereq_edges,
        "mex_edges": mex_edges,
        "missing_shared": missing[:30],
    }


def match_trees_for_tag(
    corp: FocusCorpus,
    tag: str,
    *,
    tree_id: str | None = None,
    max_trees: int = 40,
) -> list[FocusTree]:
    tag_u = (tag or "").strip().upper()
    if not tag_u:
        return []
    matched = [
        t
        for t in corp.trees
        if _tag_matches_tree(t, tag_u, nodes=corp.nodes)
        and (not tree_id or t.id == tree_id or t.id.lower() == tree_id.lower())
    ]
    if tree_id and not matched:
        matched = [t for t in corp.trees if t.id == tree_id]
    return matched[:max_trees]


def _union_find_groups(pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    nodes: set[str] = set()
    for a, b in pairs:
        nodes.add(a)
        nodes.add(b)
        union(a, b)
    buckets: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        buckets[find(n)].append(n)
    return [sorted(v) for v in buckets.values() if len(v) >= 2]


def build_tree_topology(
    corp: FocusCorpus,
    tree: FocusTree,
    *,
    max_children_listed: int = 8,
    outline_depth: int = 2,
    children_index: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    present, missing = collect_tree_node_ids(
        corp, tree, children=children_index
    )
    nodes = {nid: corp.nodes[nid] for nid in present}

    # edges: parent -> child (prerequisite)
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, set[str]] = defaultdict(set)
    for nid, node in nodes.items():
        for p in node.prereq_parents():
            parents[nid].add(p)
            if p in nodes:
                children[p].append(nid)

    for p, kids in children.items():
        children[p] = list(dict.fromkeys(kids))

    roots = sorted(
        [nid for nid in present if not nodes[nid].prereq_parents()],
        key=lambda n: (nodes[n].y is None, nodes[n].y or 0, nodes[n].x or 0, n),
    )
    local_id_set = set(tree.local_focus_ids)
    shared_ref_set = set(tree.shared_focus_refs)
    local_roots = [nid for nid in roots if nid in local_id_set]
    attached_shared_roots = [nid for nid in roots if nid in shared_ref_set]
    # 既是本地定义又被 shared 引用的极少见；其余根
    other_roots = [
        nid
        for nid in roots
        if nid not in local_id_set and nid not in shared_ref_set
    ]

    # major forks: mutually exclusive siblings sharing same parent set
    mex_pairs: list[tuple[str, str]] = []
    for nid, node in nodes.items():
        for other in node.mutually_exclusive:
            if other in nodes:
                a, b = sorted((nid, other))
                mex_pairs.append((a, b))
    mex_groups = _union_find_groups(mex_pairs)

    forks: list[dict[str, Any]] = []
    for group in mex_groups:
        parent_sets = [frozenset(nodes[n].prereq_parents()) for n in group]
        # only treat as a "route fork" if they share identical prereq set
        if len(set(parent_sets)) == 1:
            shared_parent = sorted(parent_sets[0])
            forks.append(
                {
                    "after": shared_parent,
                    "choices": group,
                    "kind": "mutually_exclusive",
                }
            )

    # prefer forks near roots / early (empty or root parents first)
    def fork_key(f: dict[str, Any]) -> tuple:
        after = f["after"]
        return (0 if not after else 1, len(after), tuple(after))

    forks.sort(key=fork_key)

    def outline_from(root: str, depth: int) -> dict[str, Any]:
        node = nodes[root]
        item: dict[str, Any] = {"id": root}
        if node.allow_branch:
            item["allow_branch"] = node.allow_branch
        if depth <= 0:
            kids = children.get(root) or []
            if kids:
                item["child_count"] = len(kids)
            return item
        kids = children.get(root) or []
        if not kids:
            return item
        shown = kids[:max_children_listed]
        item["children"] = [outline_from(c, depth - 1) for c in shown]
        if len(kids) > max_children_listed:
            item["children_omitted"] = len(kids) - max_children_listed
        return item

    outline = [outline_from(r, outline_depth) for r in local_roots or roots]

    # compact text for LLM
    lines: list[str] = []
    gate_bits: list[str] = []
    if tree.tags:
        gate_bits.append("tag=" + ",".join(tree.tags))
    if tree.country_flags:
        gate_bits.append("has_country_flag=" + ",".join(tree.country_flags))
    if tree.global_flags:
        gate_bits.append("has_global_flag=" + ",".join(tree.global_flags))
    lines.append(f"tree {tree.id} @ {tree.file}:{tree.start_line}")
    if gate_bits:
        lines.append("  gate: " + "; ".join(gate_bits))
    elif tree.country_snippet:
        lines.append(f"  country: {tree.country_snippet}")
    lines.append(
        f"  nodes: {len(present)} present"
        + (f", {len(missing)} missing_shared" if missing else "")
    )
    lines.append(
        "  local_entry_focuses (本树本地入口，无 prerequisite): "
        + (", ".join(local_roots) if local_roots else "(none)")
    )
    if attached_shared_roots:
        lines.append(
            "  attached_shared_focus (挂接共享线，通常不是派系路线入口): "
            + ", ".join(attached_shared_roots)
        )
    if other_roots:
        lines.append("  other_roots: " + ", ".join(other_roots))
    if forks:
        lines.append(
            "  route_forks (mutually_exclusive 同父分叉；问「几条路线」优先看紧接 local_entry 的分叉，"
            "不要把树上每一处互斥都算成一条完整路线):"
        )
        for f in forks[:12]:
            after = f["after"]
            after_s = " ∩ ".join(after) if after else "(tree root level)"
            mark = ""
            if after and set(after) <= set(local_roots):
                mark = "  << 主入口后的路线分叉"
            elif after and any(a in local_roots for a in after):
                mark = "  << 贴近入口"
            lines.append(
                f"    after [{after_s}]: " + " | ".join(f["choices"]) + mark
            )
        if len(forks) > 12:
            lines.append(f"    … {len(forks) - 12} more forks")
    lines.append("  outline from local entries (depth-limited):")
    for r in local_roots or roots:
        lines.extend(_format_outline_lines(outline_from(r, outline_depth), indent=2))

    return {
        "id": tree.id,
        "path": tree.file,
        "start_line": tree.start_line,
        "gate": {
            "tags": tree.tags,
            "country_flags": tree.country_flags,
            "global_flags": tree.global_flags,
            "country_snippet": tree.country_snippet,
        },
        "stats": {
            "nodes": len(present),
            "missing_shared": len(missing),
            "roots": len(roots),
            "local_roots": len(local_roots),
            "attached_shared_roots": len(attached_shared_roots),
            "forks": len(forks),
            "edges": sum(len(v) for v in children.values()),
        },
        "roots": roots,
        "local_roots": local_roots,
        "attached_shared_roots": attached_shared_roots,
        "forks": forks,
        "outline": outline,
        "missing_shared": missing[:30],
        "summary_text": "\n".join(lines),
    }


def _format_outline_lines(node: dict[str, Any], *, indent: int) -> list[str]:
    pad = "  " * indent
    extra = []
    if node.get("allow_branch"):
        extra.append("allow_branch")
    if node.get("child_count"):
        extra.append(f"…{node['child_count']} children")
    suffix = f" ({', '.join(extra)})" if extra else ""
    out = [f"{pad}- {node['id']}{suffix}"]
    for ch in node.get("children") or []:
        out.extend(_format_outline_lines(ch, indent=indent + 1))
    if node.get("children_omitted"):
        out.append(f"{pad}  … +{node['children_omitted']} more")
    return out


def focus_topology_for_tag(
    game_root: Path,
    tag: str,
    *,
    tree_id: str | None = None,
    outline_depth: int = 2,
    max_trees: int = 20,
) -> dict[str, Any]:
    tag_u = (tag or "").strip().upper()
    if not tag_u or len(tag_u) > 8:
        return {"ok": False, "error": "invalid_tag", "hint": "三字母 tag，如 PRC / GER"}

    focus_dir = Path(game_root) / _FOCUS_DIR
    if not focus_dir.is_dir():
        return {
            "ok": False,
            "error": "no_national_focus_dir",
            "path": str(focus_dir),
        }

    corp = load_focus_corpus(game_root)
    matched = match_trees_for_tag(
        corp, tag_u, tree_id=tree_id, max_trees=max_trees
    )
    children = prereq_children_index(corp)
    topologies = [
        build_tree_topology(
            corp, t, outline_depth=outline_depth, children_index=children
        )
        for t in matched
    ]

    # global text
    header = [
        f"TAG={tag_u} focus topology",
        f"matched_trees={len(topologies)} (scanned_files_trees={len(corp.trees)}, nodes_indexed={len(corp.nodes)})",
        "",
    ]
    body = "\n\n".join(t["summary_text"] for t in topologies) if topologies else "(no trees)"
    summary = "\n".join(header) + body

    return {
        "ok": True,
        "tag": tag_u,
        "tree_id_filter": tree_id,
        "matched_tree_count": len(topologies),
        "trees": topologies,
        "summary_text": summary,
        # 便于直接作 computation 证据
        "text": summary,
    }
