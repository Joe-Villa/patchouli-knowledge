#!/usr/bin/env python3
"""select_corpus：各类别同一套自动判断（无 LLM）。"""

from __future__ import annotations

import sys
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DATA))

from mod_catalog import (  # noqa: E402
    CorpusPin,
    ModCatalog,
    select_corpus,
)


def main() -> None:
    cat = ModCatalog.load()
    cases = [
        ("奴隶制", "pool"),
        ("RA里中国颜色", "mod"),
        ("interwar和realismai八百万神有什么不同", "compare"),
        ("RA和原版对比奴隶制", "compare"),
        ("interwar和realismai和原版对比", "rejected"),
        ("interwar和realismai中国定义", "rejected"),
        ("UHP和BPM和RA对比", "rejected"),
    ]
    failed = 0
    for q, expect in cases:
        ch = select_corpus(q, cat)
        ok = ch.kind == expect
        if ch.kind == "compare":
            ok = ok and ch.is_compare and len(ch.sides) == 2
        if ch.kind == "pool":
            ok = ok and len(ch.pool) > 0 and ch.reason == "status_pool"
        print(("OK" if ok else "FAIL"), expect, ch.kind, ch.reason, "←", q)
        if not ok:
            failed += 1

    # 只看原版：不判断，固定原版
    ch = select_corpus(
        "RA里中国颜色",
        cat,
        pin=CorpusPin(status="vanillaonly"),
    )
    ok = ch.kind == "vanilla" and ch.status == "vanillaonly"
    print(("OK" if ok else "FAIL"), "vanillaonly", ch.kind, ch.reason)
    if not ok:
        failed += 1

    # bytiancao：未点名 → 整池（不是原版）
    ch = select_corpus(
        "开局中国有什么变化",
        cat,
        pin=CorpusPin(status="bytiancao"),
    )
    ok = (
        ch.kind == "pool"
        and ch.reason == "status_pool"
        and ch.status == "bytiancao"
        and len(ch.pool) > 0
    )
    print(("OK" if ok else "FAIL"), "bytiancao-pool", ch.kind, ch.reason, "n=", len(ch.pool))
    if not ok:
        failed += 1

    # bytiancao 池内点名 → 自动选中该模组
    btc = next((m for m in cat.mods if m.category == "bytiancao"), None)
    if btc is None:
        print("SKIP bytiancao-auto (no bytiancao mods)")
    else:
        ch = select_corpus(
            f"{btc.short}机制如何",
            cat,
            pin=CorpusPin(status="bytiancao"),
        )
        ok = ch.kind == "mod" and ch.mod is not None and ch.mod.id == btc.id
        print(("OK" if ok else "FAIL"), "bytiancao-auto", ch.kind, ch.reason)
        if not ok:
            failed += 1

    # 标签跨类拒绝
    ch = select_corpus(
        "任意",
        cat,
        pin=CorpusPin(status="bytiancao", mode="mods", mod_ids=("2893069455",)),
    )
    ok = ch.kind == "rejected" and ch.reason.startswith("pin_wrong_category")
    print(("OK" if ok else "FAIL"), "pin_wrong_category", ch.kind, ch.reason)
    if not ok:
        failed += 1

    # 手动标签优先于自动
    ra = cat.by_id.get("2893069455")
    if ra is None:
        print("SKIP ui-tag (no RA)")
    else:
        ch = select_corpus(
            "奴隶制",
            cat,
            pin=CorpusPin(status="common", mode="mods", mod_ids=(ra.id,)),
        )
        ok = ch.kind == "mod" and ch.mod is not None and ch.mod.id == ra.id
        print(("OK" if ok else "FAIL"), "manual-tag-wins", ch.kind, ch.reason)
        if not ok:
            failed += 1

    if failed:
        raise SystemExit(failed)
    print("all ok")


if __name__ == "__main__":
    main()
