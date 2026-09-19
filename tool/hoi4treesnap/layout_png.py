"""Treesnap-compatible focus-tree PNG (names only, game x/y layout).

Uses nationalfocusview ``focus_spacing`` as the base grid, then:
1. Enlarges cell pitch so plaques cannot cover neighbors (names-only plaques
   are wider than in-game icons).
2. Splits same-cell stacks (exclusive / allow_branch routes that share x,y).
3. Packs prereq-disconnected components side-by-side (prefer empty over squeeze).
4. Scales pitch further until AABBs clear — never scrambles UDLR order.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_DEFAULT_SPACING = (96, 130)
_ITEM_W, _ITEM_H = 168, 72
_GAP_X = 40  # clear air between plaque edges on X
_GAP_Y = 64  # prefer tall/empty over vertically stuck rows
_COMPONENT_GUTTER = 4.0  # grid units between prereq-disconnected branches
_MARGIN = (80, 60)

_BG = (28, 36, 42, 255)
_PLAQUE = (55, 72, 58, 255)
_PLAQUE_EDGE = (120, 150, 110, 255)
_TEXT = (236, 240, 230, 255)
_LINE = (90, 120, 100, 255)
_MEX = (180, 120, 70, 255)

_FOCUS_SPACING_RE = re.compile(
    r'name\s*=\s*"focus_spacing"\s*position\s*=\s*\{\s*x\s*=\s*(-?\d+)\s*y\s*=\s*(-?\d+)',
    re.I | re.S,
)


def parse_focus_spacing(game_dir: Path | None) -> tuple[int, int]:
    if not game_dir:
        return _DEFAULT_SPACING
    gui = Path(game_dir) / "interface" / "nationalfocusview.gui"
    if not gui.is_file():
        return _DEFAULT_SPACING
    try:
        text = gui.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _DEFAULT_SPACING
    m = _FOCUS_SPACING_RE.search(text)
    if not m:
        return _DEFAULT_SPACING
    return int(m.group(1)), int(m.group(2))


def effective_spacing(game_spacing: tuple[int, int]) -> tuple[int, int]:
    """Pitch must fit the plaque; game spacing is for small icons."""
    gx, gy = game_spacing
    return max(gx, _ITEM_W + _GAP_X), max(gy, _ITEM_H + _GAP_Y)


def resolve_absolute_positions(
    nodes: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    """Resolve relative_position_id chains → absolute grid coords (treesnap)."""
    abs_xy: dict[str, tuple[int, int]] = {}
    for nid, n in nodes.items():
        rel = n.get("relative_position_id")
        if not rel or rel not in nodes:
            abs_xy[nid] = (int(n.get("x") or 0), int(n.get("y") or 0))

    guard = 0
    while len(abs_xy) < len(nodes) and guard < len(nodes) + 5:
        guard += 1
        progressed = False
        for nid, n in nodes.items():
            if nid in abs_xy:
                continue
            rel = n.get("relative_position_id")
            if rel in abs_xy:
                px, py = abs_xy[rel]
                abs_xy[nid] = (px + int(n.get("x") or 0), py + int(n.get("y") or 0))
                progressed = True
        if not progressed:
            for nid, n in nodes.items():
                if nid not in abs_xy:
                    abs_xy[nid] = (int(n.get("x") or 0), int(n.get("y") or 0))
            break

    if not abs_xy:
        return {}
    # Always origin-rebase. Panel slices keep full-tree absolute coords; without
    # this, far-right components render with a huge empty left margin and the
    # pan/zoom viewport looks blank (only the title is visible).
    min_x = min(x for x, _ in abs_xy.values())
    min_y = min(y for _, y in abs_xy.values())
    if min_x or min_y:
        abs_xy = {k: (x - min_x, y - min_y) for k, (x, y) in abs_xy.items()}
    return abs_xy


def split_same_cell(
    abs_xy: dict[str, tuple[int, int]] | dict[str, tuple[float, float]],
    *,
    max_rounds: int = 8,
) -> dict[str, tuple[float, float]]:
    """Exclusive branches often share one grid cell — fan them sideways.

    One pass is not enough when adjacent 3-way exclusives fan into the same
    neighbor cell (TFR ATW). Re-bucket until stable or ``max_rounds``.
    """
    out: dict[str, tuple[float, float]] = {
        nid: (float(xy[0]), float(xy[1])) for nid, xy in abs_xy.items()
    }
    for _ in range(max(1, max_rounds)):
        buckets: dict[tuple[float, float], list[str]] = defaultdict(list)
        for nid, xy in out.items():
            key = (round(float(xy[0]), 6), round(float(xy[1]), 6))
            buckets[key].append(nid)
        if all(len(ids) == 1 for ids in buckets.values()):
            break
        nxt: dict[str, tuple[float, float]] = {}
        for (gx, gy), ids in buckets.items():
            ids = sorted(ids)
            if len(ids) == 1:
                nxt[ids[0]] = (float(gx), float(gy))
                continue
            n = len(ids)
            for i, nid in enumerate(ids):
                # one full cell of separation between stacked rivals
                dx = (i - (n - 1) / 2.0) * 1.0
                nxt[nid] = (gx + dx, float(gy))
        out = nxt
    return out


def _component_roots(
    grid_xy: dict[str, tuple[float, float]],
    prereq_edges: list[tuple[str, str]] | list[list[str]],
) -> dict[str, list[str]]:
    parent: dict[str, str] = {nid: nid for nid in grid_xy}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in prereq_edges or []:
        if len(edge) < 2:
            continue
        a, b = str(edge[0]), str(edge[1])
        if a in parent and b in parent:
            union(a, b)

    comps: dict[str, list[str]] = defaultdict(list)
    for nid in grid_xy:
        comps[find(nid)].append(nid)
    return comps


def _bbox(
    ids: list[str], grid_xy: dict[str, tuple[float, float]]
) -> tuple[float, float, float, float]:
    xs = [grid_xy[n][0] for n in ids]
    ys = [grid_xy[n][1] for n in ids]
    return min(xs), min(ys), max(xs), max(ys)


def _interior_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """True only if boxes overlap in area (edge-touch does not count)."""
    x_ov = min(a[2], b[2]) - max(a[0], b[0])
    y_ov = min(a[3], b[3]) - max(a[1], b[1])
    return x_ov > 1e-9 and y_ov > 1e-9


def separate_overlapping_components(
    grid_xy: dict[str, tuple[float, float]],
    prereq_edges: list[tuple[str, str]] | list[list[str]],
    *,
    gutter: float = _COMPONENT_GUTTER,
    min_size: int = 2,
) -> dict[str, tuple[float, float]]:
    """Un-intertwine overlapping allow_branch forests; keep designed L/R order.

    Prerequisite-disconnected nodes are often still intentionally placed on one
    shared grid. Blindly packing every component destroys UDLR. Only push apart
    when two non-trivial components have *interior* bbox overlap, and always
    shift the right-hand one further right (centroid order).
    """
    if len(grid_xy) < 2:
        return grid_xy

    comps = _component_roots(grid_xy, prereq_edges)
    if len(comps) <= 1:
        return grid_xy

    pos: dict[str, tuple[float, float]] = dict(grid_xy)
    # Ignore isolates sitting inside a designed tree — they are not alternate forests.
    sizable = [ids for ids in comps.values() if len(ids) >= min_size]
    if len(sizable) <= 1:
        return pos

    for _ in range(len(sizable) * len(sizable) + 3):
        moved = False
        boxes = [(ids, _bbox(ids, pos)) for ids in sizable]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ids_a, box_a = boxes[i]
                ids_b, box_b = boxes[j]
                if not _interior_overlap(box_a, box_b):
                    continue
                cx_a = sum(pos[n][0] for n in ids_a) / len(ids_a)
                cx_b = sum(pos[n][0] for n in ids_b) / len(ids_b)
                if cx_a <= cx_b:
                    left_box, right_ids = box_a, ids_b
                else:
                    left_box, right_ids = box_b, ids_a
                right_min = min(pos[n][0] for n in right_ids)
                need = left_box[2] + gutter - right_min
                if need <= 1e-9:
                    continue
                for nid in right_ids:
                    gx, gy = pos[nid]
                    pos[nid] = (gx + need, gy)
                moved = True
        if not moved:
            break
        # refresh sizable positions already in pos; boxes recomputed next loop
    return pos


# Back-compat alias used by older call sites / experiments.
pack_components = separate_overlapping_components


def spacing_to_clear(
    grid_xy: dict[str, tuple[float, float]],
    base: tuple[int, int],
) -> tuple[int, int]:
    """Grow pitch until every plaque pair is AABB-clear; never move nodes."""
    min_dx = float(_ITEM_W + _GAP_X)
    min_dy = float(_ITEM_H + _GAP_Y)
    sx, sy = float(base[0]), float(base[1])
    ids = list(grid_xy)
    # Iterate: growing one pair can leave another still overlapping.
    for _ in range(len(ids) * len(ids) + 4):
        grew = False
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                ax, ay = grid_xy[ids[i]]
                bx, by = grid_xy[ids[j]]
                dgx = abs(ax - bx)
                dgy = abs(ay - by)
                if dgx * sx + 1e-9 >= min_dx or dgy * sy + 1e-9 >= min_dy:
                    continue
                options: list[tuple[str, float]] = []
                if dgx > 1e-9:
                    options.append(("x", min_dx / dgx))
                if dgy > 1e-9:
                    options.append(("y", min_dy / dgy))
                if not options:
                    # identical cell — should have been split already; skip
                    # so we do not spin for n² empty iterations (TFR ATW).
                    continue
                axis, need = min(
                    options,
                    key=lambda t: (t[1] - (sx if t[0] == "x" else sy), t[1]),
                )
                if axis == "x" and need > sx + 1e-9:
                    sx = need
                    grew = True
                elif axis == "y" and need > sy + 1e-9:
                    sy = need
                    grew = True
        if not grew:
            break
    return max(int(math.ceil(sx)), base[0]), max(int(math.ceil(sy)), base[1])


def grid_to_centers(
    grid_xy: dict[str, tuple[float, float]],
    *,
    sx: int,
    sy: int,
    mx: int,
    my: int,
) -> dict[str, tuple[float, float]]:
    centers: dict[str, tuple[float, float]] = {}
    for nid, (gx, gy) in grid_xy.items():
        cx = mx + gx * sx + _ITEM_W / 2
        cy = my + gy * sy + _ITEM_H / 2
        centers[nid] = (cx, cy)
    return centers


def normalize_margin(
    centers: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Translate so plaques sit inside the canvas margin (rigid shift only)."""
    if not centers:
        return centers
    min_cx = min(c[0] for c in centers.values())
    min_cy = min(c[1] for c in centers.values())
    shift_x = 0.0
    shift_y = 0.0
    if min_cx - _ITEM_W / 2 < _MARGIN[0]:
        shift_x = _MARGIN[0] + _ITEM_W / 2 - min_cx
    if min_cy - _ITEM_H / 2 < _MARGIN[1]:
        shift_y = _MARGIN[1] + _ITEM_H / 2 - min_cy
    if not shift_x and not shift_y:
        return centers
    return {k: (c[0] + shift_x, c[1] + shift_y) for k, c in centers.items()}


def draw_mex_link(
    draw: ImageDraw.ImageDraw,
    c0: tuple[float, float],
    c1: tuple[float, float],
) -> None:
    """Mutually exclusive: left plaque's right-edge mid → right plaque's left-edge mid."""
    if c0[0] <= c1[0]:
        left, right = c0, c1
    else:
        left, right = c1, c0
    x0 = left[0] + _ITEM_W / 2.0
    y0 = left[1]
    x1 = right[0] - _ITEM_W / 2.0
    y1 = right[1]
    draw.line([(x0, y0), (x1, y1)], fill=_MEX, width=3)


def assert_no_overlap(centers: dict[str, tuple[float, float]]) -> int:
    """Return count of still-overlapping pairs (0 = clean). Touching counts as bad."""
    ids = list(centers)
    bad = 0
    min_dx = float(_ITEM_W + _GAP_X)
    min_dy = float(_ITEM_H + _GAP_Y)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            ax, ay = centers[ids[i]]
            bx, by = centers[ids[j]]
            if abs(ax - bx) + 1e-6 < min_dx and abs(ay - by) + 1e-6 < min_dy:
                bad += 1
    return bad


def _pick_font(size: int = 14) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    text = (text or "").strip() or "?"
    if draw.textlength(text, font=font) <= max_w:
        return [text]
    lines: list[str] = []
    buf = ""
    for ch in text:
        trial = buf + ch
        if draw.textlength(trial, font=font) <= max_w:
            buf = trial
        else:
            if buf:
                lines.append(buf)
            buf = ch
    if buf:
        lines.append(buf)
    return lines[:3] or [text[:8]]


_SMALL_COMPONENT_LIMIT = 10


def _edge_pairs(
    edges: list[Any] | None,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for edge in edges or []:
        if len(edge) < 2:
            continue
        a, b = str(edge[0]), str(edge[1])
        if a and b:
            out.append((a, b))
    return out


def connected_components_undirected(
    node_ids: list[str] | set[str],
    *edge_groups: list[Any] | None,
) -> list[list[str]]:
    """Union-Find components; every edge list is treated as undirected."""
    parent: dict[str, str] = {str(n): str(n) for n in node_ids if n}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: str, b: str) -> None:
        if a not in parent or b not in parent:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for group in edge_groups:
        for a, b in _edge_pairs(group):
            union(a, b)

    buckets: dict[str, list[str]] = defaultdict(list)
    for nid in parent:
        buckets[find(nid)].append(nid)
    comps = [sorted(ids) for ids in buckets.values()]
    comps.sort(key=lambda ids: (-len(ids), ids[0] if ids else ""))
    return comps


def pack_disconnected_tight(
    grid_xy: dict[str, tuple[float, float]],
    *edge_groups: list[Any] | None,
    gutter: float = 2.0,
    max_row_width: float = 18.0,
) -> dict[str, tuple[float, float]]:
    """Rebase each undirected component and pack into a compact strip/grid.

    Used for the small-component pool panel: full-tree absolute coords leave
    isolates scattered across a huge canvas; packing keeps internal layout
    of each component but removes empty gaps between them.
    """
    if len(grid_xy) < 2:
        return grid_xy

    comps = connected_components_undirected(list(grid_xy.keys()), *edge_groups)
    if len(comps) <= 1:
        return grid_xy

    def _order_key(ids: list[str]) -> tuple[float, float, str]:
        cx = sum(grid_xy[n][0] for n in ids) / len(ids)
        cy = sum(grid_xy[n][1] for n in ids) / len(ids)
        return (cx, cy, ids[0])

    comps = sorted(comps, key=_order_key)
    out: dict[str, tuple[float, float]] = {}
    cursor_x = 0.0
    cursor_y = 0.0
    row_h = 0.0

    for ids in comps:
        xs = [grid_xy[n][0] for n in ids]
        ys = [grid_xy[n][1] for n in ids]
        min_x, min_y = min(xs), min(ys)
        width = max(xs) - min_x
        height = max(ys) - min_y
        if cursor_x > 1e-9 and cursor_x + width > max_row_width:
            cursor_x = 0.0
            cursor_y += row_h + gutter
            row_h = 0.0
        for nid in ids:
            gx, gy = grid_xy[nid]
            out[nid] = (cursor_x + (gx - min_x), cursor_y + (gy - min_y))
        cursor_x += width + gutter
        row_h = max(row_h, height)

    return out


def absolute_node_positions(graph: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Resolve relative_position chains on the full tree (layout source of truth)."""
    raw_nodes: dict[str, dict[str, Any]] = {}
    for n in graph.get("nodes") or []:
        nid = str(n.get("id") or "")
        if not nid:
            continue
        raw_nodes[nid] = {
            "id": nid,
            "name": n.get("name") or nid,
            "x": n.get("x"),
            "y": n.get("y"),
            "relative_position_id": n.get("relative_position_id"),
        }
    return resolve_absolute_positions(raw_nodes)


def slice_graph_abs(
    graph: dict[str, Any],
    node_ids: set[str],
    abs_xy: dict[str, tuple[int, int]],
    *,
    title: str,
) -> dict[str, Any]:
    """Subgraph with absolute grid coords (relative links cleared)."""
    nodes: list[dict[str, Any]] = []
    for n in graph.get("nodes") or []:
        nid = str(n.get("id") or "")
        if nid not in node_ids or nid not in abs_xy:
            continue
        ax, ay = abs_xy[nid]
        nodes.append(
            {
                "id": nid,
                "name": n.get("name") or nid,
                "x": ax,
                "y": ay,
                "relative_position_id": None,
                "kind": n.get("kind"),
            }
        )
    prereq = [
        [a, b]
        for a, b in _edge_pairs(graph.get("prereq_edges"))
        if a in node_ids and b in node_ids
    ]
    mex = [
        [a, b]
        for a, b in _edge_pairs(graph.get("mex_edges"))
        if a in node_ids and b in node_ids
    ]
    return {
        "id": title,
        "nodes": nodes,
        "prereq_edges": prereq,
        "mex_edges": mex,
        "stats": {
            "nodes": len(nodes),
            "prereq_edges": len(prereq),
            "mex_edges": len(mex),
        },
    }


def partition_focus_panels(
    graph: dict[str, Any],
    *,
    small_limit: int = _SMALL_COMPONENT_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split a focus_tree into render panels by undirected connectivity.

    Edges: prerequisite **and** mutually_exclusive. Components with
    ``<= small_limit`` nodes are pooled into one panel so tiny isolates
    do not explode the gallery.
    """
    abs_xy = absolute_node_positions(graph)
    if not abs_xy:
        return [], {
            "components": 0,
            "large": 0,
            "small": 0,
            "small_limit": small_limit,
            "panels": 0,
        }

    comps = connected_components_undirected(
        list(abs_xy.keys()),
        graph.get("prereq_edges"),
        graph.get("mex_edges"),
    )
    large = [c for c in comps if len(c) > small_limit]
    small = [c for c in comps if len(c) <= small_limit]

    # Stable order: left-to-right by absolute centroid, then size.
    def _order_key(ids: list[str]) -> tuple[float, int, str]:
        cx = sum(abs_xy[n][0] for n in ids) / len(ids)
        return (cx, -len(ids), ids[0])

    large.sort(key=_order_key)

    tree_id = str(graph.get("id") or "tree")
    panels: list[dict[str, Any]] = []
    for i, ids in enumerate(large):
        label = f"{tree_id} · 分量{i + 1}（{len(ids)}）"
        panels.append(
            {
                "panel_id": f"c{i}",
                "kind": "component",
                "label": label,
                "node_count": len(ids),
                "component_count": 1,
                "graph": slice_graph_abs(
                    graph, set(ids), abs_xy, title=label
                ),
            }
        )

    if small:
        pooled: set[str] = set()
        for ids in small:
            pooled.update(ids)
        label = (
            f"{tree_id} · 小组件×{len(small)}（共{len(pooled)}国策）"
            if len(small) > 1
            else f"{tree_id} · 小组件（{len(pooled)}）"
        )
        panels.append(
            {
                "panel_id": "small",
                "kind": "small_pool",
                "label": label,
                "node_count": len(pooled),
                "component_count": len(small),
                "graph": slice_graph_abs(
                    graph, pooled, abs_xy, title=label
                ),
            }
        )

    meta = {
        "components": len(comps),
        "large": len(large),
        "small": len(small),
        "small_limit": small_limit,
        "panels": len(panels),
    }
    return panels, meta


def render_tree_png(
    graph: dict[str, Any],
    out_path: Path,
    *,
    spacing: tuple[int, int] | None = None,
    game_dir: Path | None = None,
    separate_components: bool = False,
    pack_disconnected: bool = False,
) -> Path:
    """Render one focus_tree graph to PNG (names-only, no plaque overlap).

    ``separate_components`` runs the expensive overlapping-forest packer
    (original full-tree sort). Enable only for the single full-image download.

    ``pack_disconnected`` tightly packs undirected components (for the
    ≤10-node small-pool panel) so isolates are not left at far-apart
    full-tree coordinates.
    """
    game_sp = spacing or parse_focus_spacing(game_dir)
    sx, sy = effective_spacing(game_sp)
    mx, my = _MARGIN

    raw_nodes: dict[str, dict[str, Any]] = {}
    for n in graph.get("nodes") or []:
        nid = str(n.get("id") or "")
        if not nid:
            continue
        raw_nodes[nid] = {
            "id": nid,
            "name": n.get("name") or nid,
            "x": n.get("x"),
            "y": n.get("y"),
            "relative_position_id": n.get("relative_position_id"),
        }

    abs_xy = resolve_absolute_positions(raw_nodes)
    if not abs_xy:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (400, 120), _BG)
        ImageDraw.Draw(img).text(
            (20, 40), graph.get("id") or "empty", fill=_TEXT, font=_pick_font(18)
        )
        img.save(out_path)
        return out_path

    prereq_edges = [
        (str(e[0]), str(e[1]))
        for e in (graph.get("prereq_edges") or [])
        if len(e) >= 2
    ]
    mex_edges = [
        (str(e[0]), str(e[1]))
        for e in (graph.get("mex_edges") or [])
        if len(e) >= 2
    ]
    grid_xy = {
        nid: (float(xy[0]), float(xy[1])) for nid, xy in abs_xy.items()
    }
    if pack_disconnected:
        grid_xy = pack_disconnected_tight(
            grid_xy, prereq_edges, mex_edges
        )
    # Pack alternate branches only for the full-tree export (expensive on
    # wide forests). Panel / preview renders keep game coords as-is.
    if separate_components:
        grid_xy = separate_overlapping_components(grid_xy, prereq_edges)
    grid_xy = split_same_cell(grid_xy)
    sx, sy = spacing_to_clear(grid_xy, (sx, sy))
    centers = normalize_margin(
        grid_to_centers(grid_xy, sx=sx, sy=sy, mx=mx, my=my)
    )

    max_cx = max(c[0] for c in centers.values())
    max_cy = max(c[1] for c in centers.values())
    w = int(max_cx + _ITEM_W / 2 + mx) + 1
    h = int(max_cy + _ITEM_H / 2 + my) + 1
    w = max(w, 320)
    h = max(h, 200)

    img = Image.new("RGBA", (w, h), _BG)
    draw = ImageDraw.Draw(img)
    font = _pick_font(13)
    title_font = _pick_font(16)

    title = str(graph.get("id") or "")
    draw.text((16, 12), title, fill=_TEXT, font=title_font)

    for edge in graph.get("prereq_edges") or []:
        if len(edge) < 2:
            continue
        a, b = str(edge[0]), str(edge[1])
        if a not in centers or b not in centers:
            continue
        draw.line([centers[a], centers[b]], fill=_LINE, width=2)

    for nid, (cx, cy) in centers.items():
        n = raw_nodes[nid]
        left = cx - _ITEM_W / 2
        top = cy - _ITEM_H / 2
        box = [left, top, left + _ITEM_W, top + _ITEM_H]
        draw.rounded_rectangle(box, radius=8, fill=_PLAQUE, outline=_PLAQUE_EDGE, width=2)
        label = str(n.get("name") or nid)
        lines = _wrap(label, font, _ITEM_W - 16, draw)
        total_h = sum(
            draw.textbbox((0, 0), ln, font=font)[3] for ln in lines
        ) + 2 * (len(lines) - 1)
        ty = cy - total_h / 2
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            draw.text((cx - tw / 2, ty), ln, fill=_TEXT, font=font)
            ty += draw.textbbox((0, 0), ln, font=font)[3] + 2

    # Exclusive links on top so they stay visible after separation moves nodes.
    for edge in graph.get("mex_edges") or []:
        if len(edge) < 2:
            continue
        a, b = str(edge[0]), str(edge[1])
        if a not in centers or b not in centers:
            continue
        draw_mex_link(draw, centers[a], centers[b])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path
