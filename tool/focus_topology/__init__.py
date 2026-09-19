"""HOI4 national focus topology (TAG → trees / edges / forks / mermaid)."""

from .catalog import catalog_focus_trees
from .derived import (
    build_focus_derived,
    focus_derived_root,
    load_catalog_json,
    load_index,
    load_tree_graph,
    resolve_derived_dir,
)
from .mermaid import tree_graph_to_mermaid
from .names import lookup_focus_names, resolve_loc_db
from .topology import (
    build_tree_graph,
    focus_topology_for_tag,
    load_focus_corpus,
    match_trees_for_tag,
)

__all__ = [
    "build_focus_derived",
    "build_tree_graph",
    "catalog_focus_trees",
    "focus_derived_root",
    "focus_topology_for_tag",
    "load_catalog_json",
    "load_focus_corpus",
    "load_index",
    "load_tree_graph",
    "lookup_focus_names",
    "match_trees_for_tag",
    "resolve_derived_dir",
    "resolve_loc_db",
    "tree_graph_to_mermaid",
]
