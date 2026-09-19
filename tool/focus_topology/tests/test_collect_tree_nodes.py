"""collect_tree_node_ids: shared_focus membership via prereq + relative_position."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2]
if str(_TOOL) not in sys.path:
    sys.path.insert(0, str(_TOOL))

from focus_topology.topology import (  # noqa: E402
    FocusCorpus,
    FocusNode,
    FocusTree,
    collect_tree_node_ids,
    relative_children_index,
)


def _node(
    nid: str,
    *,
    kind: str = "shared_focus",
    relative_position_id: str | None = None,
    prerequisites: list[list[str]] | None = None,
) -> FocusNode:
    return FocusNode(
        id=nid,
        kind=kind,
        file="test.txt",
        start_line=1,
        relative_position_id=relative_position_id,
        prerequisites=prerequisites or [],
    )


def _tree(*, shared: list[str] | None = None, local: list[str] | None = None) -> FocusTree:
    return FocusTree(
        id="test_tree",
        file="tree.txt",
        start_line=1,
        shared_focus_refs=list(shared or []),
        local_focus_ids=list(local or []),
    )


def _corp(*nodes: FocusNode) -> FocusCorpus:
    c = FocusCorpus(game_root=Path("."))
    for n in nodes:
        c.nodes[n.id] = n
    return c


class CollectTreeNodeIdsTests(unittest.TestCase):
    def test_relative_only_shared_chain(self) -> None:
        """ParadoxArt-style: shared branch linked only by relative_position_id."""
        corp = _corp(
            _node("focus_head"),
            _node("focus_2", relative_position_id="focus_head"),
            _node("focus_3", relative_position_id="focus_2"),
            _node("unrelated_shared"),
        )
        tree = _tree(shared=["focus_head"])
        present, missing = collect_tree_node_ids(corp, tree)
        self.assertEqual(missing, [])
        self.assertEqual(set(present), {"focus_head", "focus_2", "focus_3"})

    def test_prereq_only_shared_chain(self) -> None:
        """TNO/TFR-style: shared branch linked by prerequisite, no rel."""
        corp = _corp(
            _node("entry"),
            _node("mid", prerequisites=[["entry"]]),
            _node("leaf", prerequisites=[["mid"]]),
            _node("other_branch", prerequisites=[["unrelated"]]),
        )
        tree = _tree(shared=["entry"])
        present, missing = collect_tree_node_ids(corp, tree)
        self.assertEqual(missing, [])
        self.assertEqual(set(present), {"entry", "mid", "leaf"})

    def test_union_prereq_and_relative(self) -> None:
        """One child via prereq, another via relative_position from same seed."""
        corp = _corp(
            _node("seed"),
            _node("via_prereq", prerequisites=[["seed"]]),
            _node("via_rel", relative_position_id="seed"),
            _node("via_rel_child", relative_position_id="via_rel", prerequisites=[["via_rel"]]),
        )
        tree = _tree(shared=["seed"])
        present, _ = collect_tree_node_ids(corp, tree)
        self.assertEqual(set(present), {"seed", "via_prereq", "via_rel", "via_rel_child"})

    def test_local_as_rel_anchor_does_not_pull_unattached_shared(self) -> None:
        """Local focus used as relative_position by unrelated shared must not absorb them."""
        corp = _corp(
            _node("local_root", kind="focus"),
            _node("local_b", kind="focus", relative_position_id="local_root"),
            _node("stray_shared", relative_position_id="local_root"),
            _node("stray_child", relative_position_id="stray_shared"),
        )
        tree = _tree(local=["local_root", "local_b"])
        present, _ = collect_tree_node_ids(corp, tree)
        self.assertEqual(set(present), {"local_root", "local_b"})

    def test_nested_shared_focus_block_expands_rel(self) -> None:
        """shared_focus {} nested in the tree file is a shared-origin seed."""
        corp = _corp(
            _node("nested_shared", kind="shared_focus"),
            _node("rel_kid", relative_position_id="nested_shared"),
        )
        tree = _tree(local=["nested_shared"])
        present, _ = collect_tree_node_ids(corp, tree)
        self.assertEqual(set(present), {"nested_shared", "rel_kid"})

    def test_relative_ancestor_for_layout(self) -> None:
        """Local focus with rel to outside shared still pulls ancestor for layout."""
        corp = _corp(
            _node("anchor", kind="focus"),
            _node("local_a", kind="focus", relative_position_id="anchor"),
        )
        tree = _tree(local=["local_a"])
        present, _ = collect_tree_node_ids(corp, tree)
        self.assertEqual(set(present), {"local_a", "anchor"})

    def test_missing_shared_seed(self) -> None:
        corp = _corp(_node("only_local", kind="focus"))
        tree = _tree(local=["only_local"], shared=["gone"])
        present, missing = collect_tree_node_ids(corp, tree)
        self.assertEqual(present, ["only_local"])
        self.assertEqual(missing, ["gone"])

    def test_relative_children_index(self) -> None:
        corp = _corp(
            _node("a"),
            _node("b", relative_position_id="a"),
            _node("c", relative_position_id="a"),
        )
        idx = relative_children_index(corp)
        self.assertEqual(set(idx["a"]), {"b", "c"})


if __name__ == "__main__":
    unittest.main()
