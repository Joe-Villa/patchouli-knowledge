"""Focus tree graph → mermaid flowchart (forest theme)."""

from __future__ import annotations

import re
from typing import Any

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def escape_label(text: str) -> str:
    """Make a string safe inside mermaid node [\"...\"]."""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", " ")
    s = s.replace('"', "#quot;")
    s = s.replace("#", "#35;")
    s = s.replace("<", "#lt;").replace(">", "#gt;")
    s = s.replace("[", "#91;").replace("]", "#93;")
    s = " ".join(s.split())
    return s or "?"


def safe_node_id(focus_id: str) -> str:
    raw = (focus_id or "").strip() or "node"
    if _UNSAFE_ID.search(raw):
        return "n_" + _UNSAFE_ID.sub("_", raw)
    # mermaid reserved-ish: avoid bare "end"
    if raw.lower() in {"end", "subgraph", "graph", "flowchart"}:
        return f"n_{raw}"
    return raw


def tree_graph_to_mermaid(
    graph: dict[str, Any],
    *,
    theme: str = "forest",
) -> str:
    """Build one forest-themed flowchart from build_tree_graph output."""
    lines: list[str] = [
        f"%%{{init: {{'theme':'{theme}'}}}}%%",
        "flowchart TB",
    ]
    nodes = graph.get("nodes") or []
    for node in nodes:
        nid = safe_node_id(str(node.get("id") or ""))
        label = escape_label(str(node.get("name") or node.get("id") or nid))
        lines.append(f'  {nid}["{label}"]')

    for edge in graph.get("prereq_edges") or []:
        if len(edge) < 2:
            continue
        a, b = safe_node_id(str(edge[0])), safe_node_id(str(edge[1]))
        lines.append(f"  {a} --> {b}")

    for edge in graph.get("mex_edges") or []:
        if len(edge) < 2:
            continue
        a, b = safe_node_id(str(edge[0])), safe_node_id(str(edge[1]))
        lines.append(f"  {a} -.-> {b}")

    return "\n".join(lines) + "\n"
