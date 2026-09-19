#!/usr/bin/env python3
"""Build pre-parsed HOI4 focus corpus → database/derived/focus/<mod>/.

Usage (repo root)::

    python ingest/focus_corpus/build_focus_corpus.py --mod TFR

Does **not** build all mods by default. Pass one --mod (short / id / alias / vanilla).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
_REPO = _INGEST.parent
_TOOL = _REPO / "tool"
_HOI4_DATA = _REPO / "service" / "1936" / "data"
_SVC1939 = _REPO / "service" / "1939"

for p in (_INGEST, _TOOL, _HOI4_DATA, _SVC1939):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from focus_topology.derived import build_focus_derived, focus_derived_root  # noqa: E402
from paths import mods_catalog_path, mods_root, vanilla_root  # noqa: E402


def _load_catalog() -> dict:
    path = mods_catalog_path()
    if not path.is_file():
        return {"mods": []}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_mod(choice: str) -> tuple[Path, dict]:
    raw = (choice or "").strip()
    if not raw or raw.lower() in {"vanilla", "van", "base"}:
        root = vanilla_root()
        if not root.is_dir():
            raise FileNotFoundError(f"vanilla root missing: {root}")
        return root, {"id": "vanilla", "short": "vanilla", "name": "Vanilla (本体)"}

    catalog = _load_catalog()
    key = raw.lower()
    hit = None
    for m in catalog.get("mods") or []:
        mid = str(m.get("id") or "")
        short = str(m.get("short") or "")
        aliases = [str(a).lower() for a in (m.get("aliases") or [])]
        if key == mid.lower() or key == short.lower() or key in aliases:
            hit = m
            break
        if key == str(m.get("name") or "").lower():
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--mod",
        help="模组 short / workshop id / alias，或 vanilla（一次只建一个）",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="按 mods_catalog.json 构建全部模组（另加 vanilla）",
    )
    ap.add_argument(
        "--include-vanilla",
        action="store_true",
        help="与 --all 联用时也构建 vanilla",
    )
    ap.add_argument("--lang", default="simp_chinese")
    args = ap.parse_args(argv)

    targets: list[str] = []
    if args.all:
        catalog = _load_catalog()
        targets = [str(m.get("short") or m.get("id")) for m in (catalog.get("mods") or [])]
        if args.include_vanilla:
            targets = ["vanilla", *targets]
    else:
        targets = [args.mod]

    failed = 0
    for choice in targets:
        try:
            game_root, meta = resolve_mod(choice)
        except (KeyError, FileNotFoundError) as e:
            print(f"FAIL resolve {choice}: {e}")
            failed += 1
            continue
        print(f"=== mod={meta.get('short')} id={meta.get('id')} ===")
        print(f"game_root={game_root}")
        result = build_focus_derived(
            game_root,
            mod_meta=meta,
            lang=args.lang,
            vanilla_root=vanilla_root(),
            repo=_REPO,
        )
        if not result.get("ok"):
            print(json.dumps(result, ensure_ascii=False, indent=2))
            failed += 1
            continue
        man = result.get("manifest") or {}
        stats = man.get("stats") or {}
        print(f"wrote {result.get('derived')}")
        print(
            f"trees={stats.get('trees')} nodes={stats.get('nodes_indexed')} "
            f"exclusive={stats.get('exclusive')} non_exclusive={stats.get('non_exclusive')} "
            f"build_ms={stats.get('build_ms')}"
        )
    print(f"done failed={failed}/{len(targets)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
