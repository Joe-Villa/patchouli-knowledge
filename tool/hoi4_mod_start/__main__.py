#!/usr/bin/env python3
"""Build database/derived/hoi4/mod_start.json from catalog + reduced corpora.

Usage (repo root)::

    python -m hoi4_mod_start
    # or
    python tool/hoi4_mod_start/__main__.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[1]
_REPO = _TOOL.parent
if str(_TOOL) not in sys.path:
    sys.path.insert(0, str(_TOOL))

from hoi4_mod_start import build_mod_start_index, default_index_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--catalog",
        type=Path,
        default=_REPO / "service" / "1936" / "data" / "mods_catalog.json",
    )
    ap.add_argument(
        "--mods-root",
        type=Path,
        default=_REPO / "database" / "data" / "steam" / "394360" / "mods",
    )
    ap.add_argument(
        "--vanilla-root",
        type=Path,
        default=_REPO / "database" / "data" / "steam" / "394360" / "vanilla",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="default: database/derived/hoi4/mod_start.json",
    )
    args = ap.parse_args(argv)
    out = args.out or default_index_path(_REPO)
    result = build_mod_start_index(
        catalog_path=args.catalog,
        mods_root=args.mods_root,
        vanilla_root=args.vanilla_root if args.vanilla_root.is_dir() else None,
        out_path=out,
    )
    st = result.get("stats") or {}
    print(
        f"wrote {result.get('path')}  mods={st.get('mods')} "
        f"known={st.get('known')} unknown={st.get('unknown')}"
    )
    # preview sort order
    mods = list((result.get("mods") or {}).values())
    mods.sort(
        key=lambda m: (
            m.get("start_year") is None,
            m.get("start_year") or 10**9,
            str(m.get("id") or ""),
        )
    )
    van = result.get("vanilla")
    if van:
        y = van.get("start_year")
        print(f"  vanilla  ({y if y is not None else '?'}) {van.get('name')}")
    for m in mods:
        y = m.get("start_year")
        print(f"  {y if y is not None else '?':>4}  {m.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
