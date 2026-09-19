"""Unit tests for focus-tree panel partition (prereq+mex components)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from layout_png import (  # noqa: E402
    connected_components_undirected,
    partition_focus_panels,
)


class PartitionPanelsTests(unittest.TestCase):
    def test_mex_joins_components(self) -> None:
        nodes = [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 1, "y": 0}]
        comps = connected_components_undirected(
            ["a", "b"], [], [["a", "b"]]
        )
        self.assertEqual(len(comps), 1)

        comps2 = connected_components_undirected(["a", "b"], [], [])
        self.assertEqual(len(comps2), 2)

    def test_small_pool_and_large_split(self) -> None:
        # large chain of 12, two isolates, one pair
        nodes = [{"id": f"L{i}", "x": i, "y": 0} for i in range(12)]
        nodes += [
            {"id": "s1", "x": 0, "y": 5},
            {"id": "s2", "x": 1, "y": 5},
            {"id": "p1", "x": 0, "y": 8},
            {"id": "p2", "x": 1, "y": 8},
        ]
        prereq = [[f"L{i}", f"L{i+1}"] for i in range(11)] + [["p1", "p2"]]
        graph = {
            "id": "demo",
            "nodes": nodes,
            "prereq_edges": prereq,
            "mex_edges": [],
        }
        panels, meta = partition_focus_panels(graph, small_limit=10)
        self.assertEqual(meta["components"], 4)  # L + s1 + s2 + pair
        self.assertEqual(meta["large"], 1)
        self.assertEqual(meta["small"], 3)
        self.assertEqual(len(panels), 2)
        kinds = [p["kind"] for p in panels]
        self.assertEqual(kinds, ["component", "small_pool"])
        self.assertEqual(panels[0]["node_count"], 12)
        self.assertEqual(panels[1]["node_count"], 4)
        self.assertEqual(panels[1]["component_count"], 3)

    def test_pack_disconnected_tight_closes_gaps(self) -> None:
        from layout_png import pack_disconnected_tight

        grid = {
            "a": (0.0, 0.0),
            "b": (1.0, 0.0),
            "c": (40.0, 5.0),
            "d": (41.0, 5.0),
        }
        prereq = [("a", "b"), ("c", "d")]
        packed = pack_disconnected_tight(grid, prereq, [])
        # two components side by side; no 40-unit gap
        xs = [packed[n][0] for n in packed]
        self.assertLess(max(xs) - min(xs), 10)
        # internal relative offset preserved (b is 1 right of a)
        self.assertAlmostEqual(packed["b"][0] - packed["a"][0], 1.0)
        self.assertAlmostEqual(packed["d"][0] - packed["c"][0], 1.0)

        from layout_png import resolve_absolute_positions

        nodes = {
            "a": {"id": "a", "x": 28, "y": 5, "relative_position_id": None},
            "b": {"id": "b", "x": 50, "y": 9, "relative_position_id": None},
        }
        abs_xy = resolve_absolute_positions(nodes)
        self.assertEqual(min(x for x, _ in abs_xy.values()), 0)
        self.assertEqual(min(y for _, y in abs_xy.values()), 0)
        self.assertEqual(abs_xy["b"], (22, 4))

    def test_exactly_ten_is_small(self) -> None:
        nodes = [{"id": f"n{i}", "x": i, "y": 0} for i in range(10)]
        prereq = [[f"n{i}", f"n{i+1}"] for i in range(9)]
        graph = {
            "id": "t10",
            "nodes": nodes,
            "prereq_edges": prereq,
            "mex_edges": [],
        }
        panels, meta = partition_focus_panels(graph, small_limit=10)
        self.assertEqual(meta["large"], 0)
        self.assertEqual(meta["small"], 1)
        self.assertEqual(panels[0]["kind"], "small_pool")
        self.assertEqual(panels[0]["node_count"], 10)


if __name__ == "__main__":
    unittest.main()
