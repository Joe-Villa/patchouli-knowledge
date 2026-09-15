from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .catalog import ModRecord
from .fit_tfidf import FitParts, FitTfidf
from .formula import PopMode, total_score


@dataclass
class RankedMod:
    mod: ModRecord
    fit: float
    sim_title: float
    sim_description: float
    sim_tags: float
    soft_pop: float
    soft_fresh: float
    endorse: float
    score: float
    rank: int


def rank_mods(
    mods: list[ModRecord],
    fit_model: FitTfidf,
    query: str,
    *,
    pop_mode: PopMode = "default",
    endorse: float = 0.0,
    now: datetime | None = None,
    hard_filter: Callable[[ModRecord], bool] | None = None,
    top_fraction: float = 0.10,
    top_n: int | None = None,
) -> tuple[list[RankedMod], list[RankedMod]]:
    """返回 (全部按 score 降序, 短名单)。

    短名单默认前 top_fraction；若给 top_n 则再截断为前 top_n（展示壳用）。
    """
    candidates = [m for m in mods if hard_filter(m)] if hard_filter else list(mods)
    if not candidates:
        return [], []

    # Fit 模型建在全库上更稳；这里只对候选下标取分
    id_to_idx = {m.id: i for i, m in enumerate(fit_model.mods)}
    parts_all = fit_model.fit_all(query)

    ranked: list[RankedMod] = []
    for m in candidates:
        i = id_to_idx[m.id]
        parts: FitParts = parts_all[i]
        bundle = total_score(
            parts.fit,
            subscribers=m.subscribers,
            time_updated=m.time_updated,
            endorse=float(endorse),
            pop_mode=pop_mode,
            now=now,
        )
        ranked.append(
            RankedMod(
                mod=m,
                fit=bundle["fit"],
                sim_title=parts.sim_title,
                sim_description=parts.sim_description,
                sim_tags=parts.sim_tags,
                soft_pop=bundle["soft_pop"],
                soft_fresh=bundle["soft_fresh"],
                endorse=bundle["endorse"],
                score=bundle["score"],
                rank=0,
            )
        )
    ranked.sort(key=lambda r: (r.score, r.fit, r.mod.subscribers), reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i

    k = max(1, math.ceil(len(ranked) * top_fraction))
    short = ranked[:k]
    if top_n is not None:
        short = short[: max(1, int(top_n))]
    return ranked, short
