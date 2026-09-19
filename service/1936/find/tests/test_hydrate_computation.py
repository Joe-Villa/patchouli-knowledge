"""submit hydrate：computation 正文与 notes 回填。"""

from __future__ import annotations

import sys
from pathlib import Path

_FIND = Path(__file__).resolve().parents[1]
_CORE = _FIND.parent
sys.path.insert(0, str(_FIND))
sys.path.append(str(_CORE / "llm"))
sys.path.append(str(_CORE / "data"))

from agent_hydrate import _hydrate_submit  # noqa: E402
from evidence import COMPUTATION_ENTITY_TYPE  # noqa: E402

GAME = _CORE / "data" / "game"


def test_computation_text_item_sufficient():
    pkg = _hydrate_submit(
        {
            "coverage": "sufficient",
            "items": [
                {
                    "entity_type": COMPUTATION_ENTITY_TYPE,
                    "role": "primary",
                    "text": "1 STATE_JIANGXI 24213496\n2 STATE_HENAN 20936384",
                }
            ],
            "notes": "口径：history/pops size 求和",
        },
        question="人口前二",
        game=GAME,
    )
    assert pkg.coverage == "sufficient"
    assert len(pkg.items) == 1
    assert pkg.items[0].entity_type == COMPUTATION_ENTITY_TYPE
    assert pkg.items[0].path == "run_code"
    assert "STATE_JIANGXI" in pkg.items[0].text


def test_notes_backfill_when_read_block_fails():
    pkg = _hydrate_submit(
        {
            "coverage": "sufficient",
            "items": [
                {
                    "path": "common/history/pops/11_east_asia.txt",
                    "key": "STATE_JIANGXI",
                    "role": "primary",
                }
            ],
            "notes": "Top1 江西 24213496（run_code 结果）",
            "unresolved": [],
        },
        question="人口最多",
        game=GAME,
    )
    assert pkg.coverage == "sufficient"
    assert len(pkg.items) == 1
    assert pkg.items[0].entity_type == COMPUTATION_ENTITY_TYPE
    assert "24213496" in pkg.items[0].text
    assert not any("read_block" in (g.reason or "") for g in pkg.unresolved)


def test_sufficient_without_text_or_notes_becomes_empty():
    pkg = _hydrate_submit(
        {
            "coverage": "sufficient",
            "items": [
                {
                    "path": "common/history/pops/11_east_asia.txt",
                    "key": "STATE_JIANGXI",
                }
            ],
        },
        question="x",
        game=GAME,
    )
    assert pkg.coverage == "empty"
    assert pkg.items == []


if __name__ == "__main__":
    test_computation_text_item_sufficient()
    test_notes_backfill_when_read_block_fails()
    test_sufficient_without_text_or_notes_becomes_empty()
    print("ok")
