#!/usr/bin/env python3
"""跑样例题：Fit(TF-IDF) × SoftPop × SoftFresh × Endorse(1)，取前 10%。"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scoring.catalog import load_ge300_details
from scoring.fit_tfidf import FitTfidf
from scoring.rank import rank_mods

SAMPLE_QUERIES = [
    ("Q5 加成/加速/变强，别太复杂", "有没有单纯给加成的，就那种变强加速一类的，别整太复杂"),
    ("Q6 冷战/一战以后/偏现代开局", "冷战、一战以后、开局偏现代那种，有没有，先说几个"),
    ("Q7 总转换/大改地图剧本", "我想要总转换 / 大改地图剧本那种，推两三个看看"),
    ("Q8 UI/信息更全界面", "有没有比较好的 UI、信息更全一点的界面模组"),
    ("Q9 立绘/人物美化", "有没有好看的立绘、人物美化之类的"),
    ("Q10 BGM/音乐包", "有没有换 BGM、音乐包"),
    ("Q17 经济建筑贸易增强", "经济、建筑、贸易相关增强，给我几个"),
]


def _pct(xs: list[int], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = int(round((len(xs) - 1) * p))
    return float(xs[i])


def report_query(label: str, query: str, fit_model, mods, top_show: int = 15) -> dict:
    all_ranked, short = rank_mods(mods, fit_model, query, endorse=0.0)
    n = len(all_ranked)
    k = len(short)
    all_subs = [r.mod.subscribers for r in all_ranked]
    short_subs = [r.mod.subscribers for r in short]
    med_all = statistics.median(all_subs)
    # 「订阅不高却出场」：短名单内低于全库中位数，且 Fit 高于短名单 Fit 中位数
    fit_med_short = statistics.median([r.fit for r in short]) if short else 0
    underdog = [
        r
        for r in short
        if r.mod.subscribers < med_all and r.fit >= fit_med_short
    ]
    # 更严：短名单里订阅落在全库最低 40% 的
    p40 = _pct(all_subs, 0.40)
    low_sub_in_short = [r for r in short if r.mod.subscribers <= p40]

    print("\n" + "=" * 88)
    print(f"{label}")
    print(f"q: {query}")
    print(f"N={n}  top10%={k}  sub_median_all={med_all:.0f}  p40_all={p40:.0f}")
    print(
        f"短名单 sub p50={_pct(short_subs,0.5):.0f}  "
        f"低于全库中位: {sum(1 for s in short_subs if s < med_all)}/{k}  "
        f"≤p40: {len(low_sub_in_short)}/{k}  "
        f"高Fit低订(underdog): {len(underdog)}"
    )
    print("-" * 88)
    print(f"{'#':>3} {'score':>7} {'fit':>6} {'pop':>5} {'fr':>4} {'subs':>7}  title")
    for r in short[:top_show]:
        title = r.mod.title.replace("\n", " ")[:56]
        mark = " *" if r.mod.subscribers <= p40 else ""
        print(
            f"{r.rank:3d} {r.score:7.4f} {r.fit:6.3f} {r.soft_pop:5.3f} "
            f"{r.soft_fresh:4.2f} {r.mod.subscribers:7d}  {title}{mark}"
        )
    if k > top_show:
        print(f"  ... +{k - top_show} more in shortlist")

    if underdog:
        print("underdog 样例（低订 + 高 Fit）:")
        for r in underdog[:8]:
            print(
                f"  subs={r.mod.subscribers:5d} fit={r.fit:.3f} score={r.score:.4f}  "
                f"{r.mod.title[:60]}"
            )
    return {
        "label": label,
        "n": n,
        "k": k,
        "median_all": med_all,
        "low_sub_count": len(low_sub_in_short),
        "underdog_count": len(underdog),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--query", default=None, help="单条自定义问句")
    ap.add_argument("--top-show", type=int, default=12)
    args = ap.parse_args()

    print("loading ge300 details…")
    mods = load_ge300_details(args.db)
    print(f"mods={len(mods)}  building TF-IDF (char 2-4 grams)…")
    fit_model = FitTfidf(mods)
    print("ready.")

    summaries = []
    if args.query:
        summaries.append(report_query("custom", args.query, fit_model, mods, args.top_show))
    else:
        for label, q in SAMPLE_QUERIES:
            summaries.append(report_query(label, q, fit_model, mods, args.top_show))

    print("\n" + "=" * 88)
    print("汇总：短名单中「订阅≤全库 p40」的题数占比（期望：不少题能挖出低订高 Fit）")
    for s in summaries:
        print(
            f"  {s['label']}: low_sub {s['low_sub_count']}/{s['k']}  "
            f"underdog {s['underdog_count']}"
        )


if __name__ == "__main__":
    main()
