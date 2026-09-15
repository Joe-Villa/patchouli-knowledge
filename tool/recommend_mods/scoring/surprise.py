"""给我惊喜：无 query 的发现通道。

Interesting = Hook + SoftSubSweet + SoftFresh
启动时预计算短名单；请求时只在短名单内按权重随机抽。
全员同一规则，无认同开小灶；不用星级。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .catalog import ModRecord
from .filters import HardFilterSpec, apply_hard_filter
from .formula import soft_fresh

DEFAULT_SURPRISE_FILTER = HardFilterSpec(
    min_subscribers=15,
    min_description_chars=120,
    exclude_tags_any=["Sound"],
    exclude_title_keywords=["chinese translation", "中文翻译包"],
)

# 短名单默认长度（Interesting Top K）
DEFAULT_SHORTLIST_K = 400

SUB_SWEET_LO = 50.0
SUB_SWEET_HI = 800.0
SUB_SWEET_BONUS_MAX = 12.0

HOOK_TAG_BONUS = {
    "gameplay": 6.0,
    "events": 7.0,
    "journal entries": 6.0,
    "historical": 5.0,
    "alternative history": 7.0,
    "cultures and religions": 6.0,
    "economy and buildings": 5.0,
    "diplomacy": 4.0,
    "warfare": 4.0,
    "pops": 4.0,
    "interest groups": 5.0,
    "new nations": 5.0,
    "expansion": 4.0,
    "map": 3.0,
}
HOOK_TAG_PENALTY = {
    "translation": -10.0,
    "fixes": -6.0,
    "utilities": -4.0,
    "sound": -8.0,
    "graphics": -3.0,
    "flags": -4.0,
}
HOOK_TAG_CAP = 14.0
HOOK_TAG_FLOOR = -12.0

_HOOK_LEXICON = re.compile(
    r"("
    r"journal|event|law|ig\b|interest group|company|building|pop|"
    r"culture|religion|diplomacy|war|battle|nation|flavor|rework|"
    r"日记|事件|法律|利益集团|公司|建筑|人口|文化|宗教|外交|战争|"
    r"国家|风味|重做|叙事|剧本|决议|人物|姓名"
    r")",
    re.IGNORECASE,
)
_SHALLOW_TITLE = re.compile(
    r"(?i)^(fix(es)?|bug\s*fix|translation|译|汉化包|ui\s*fix)\b"
)
_TAG_SPLIT = re.compile(r"[|,/]+")


@dataclass
class SurpriseRanked:
    mod: ModRecord
    score: float
    hook: float
    soft_sub_sweet: float
    soft_fresh: float
    rank: int = 0


def soft_sub_sweet(subscribers: int | float | None) -> float:
    """订阅甜点加成：落在 [LO, HI] 满分；两侧线性降到 0。"""
    s = max(0.0, float(subscribers or 0))
    if s <= 0:
        return 0.0
    if SUB_SWEET_LO <= s <= SUB_SWEET_HI:
        return SUB_SWEET_BONUS_MAX
    if s < SUB_SWEET_LO:
        return SUB_SWEET_BONUS_MAX * (s / SUB_SWEET_LO)
    span = 2500.0 - SUB_SWEET_HI
    if span <= 0:
        return 0.0
    t = min(1.0, (s - SUB_SWEET_HI) / span)
    return SUB_SWEET_BONUS_MAX * (1.0 - t)


def _norm_tag(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip()).lower()


def hook_score(mod: ModRecord) -> float:
    tags = {_norm_tag(p) for p in _TAG_SPLIT.split(mod.tags or "") if p.strip()}
    tag_s = 0.0
    for t in tags:
        tag_s += HOOK_TAG_BONUS.get(t, 0.0)
        tag_s += HOOK_TAG_PENALTY.get(t, 0.0)
    tag_s = max(HOOK_TAG_FLOOR, min(HOOK_TAG_CAP, tag_s))

    blob = f"{mod.titles_blob}\n{mod.description_clean}"
    lex = 0.0
    hits = _HOOK_LEXICON.findall(blob)
    if hits:
        uniq = {h.lower() for h in hits}
        lex = min(8.0, 2.0 + 1.2 * len(uniq))

    shallow = -8.0 if _SHALLOW_TITLE.search((mod.title_for_fit or "").strip()) else 0.0
    return tag_s + lex + shallow


def surprise_score(
    mod: ModRecord,
    *,
    now: datetime | None = None,
) -> SurpriseRanked:
    hook = hook_score(mod)
    sweet = soft_sub_sweet(mod.subscribers)
    sf = soft_fresh(mod.time_updated, now=now)
    return SurpriseRanked(
        mod=mod,
        score=hook + sweet + sf,
        hook=hook,
        soft_sub_sweet=sweet,
        soft_fresh=sf,
    )


def rank_surprise(
    mods: list[ModRecord],
    *,
    hard: HardFilterSpec | None = None,
    now: datetime | None = None,
) -> list[SurpriseRanked]:
    pool = apply_hard_filter(mods, hard if hard is not None else DEFAULT_SURPRISE_FILTER)
    ranked = [surprise_score(m, now=now) for m in pool]
    ranked.sort(key=lambda r: (r.score, r.hook, r.soft_sub_sweet), reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
    return ranked


def build_surprise_shortlist(
    mods: list[ModRecord],
    *,
    top_k: int = DEFAULT_SHORTLIST_K,
    hard: HardFilterSpec | None = None,
    now: datetime | None = None,
) -> list[SurpriseRanked]:
    """Interesting 全量打分后截 Top K，作为固定惊喜短名单。"""
    k = max(1, int(top_k))
    return rank_surprise(mods, hard=hard, now=now)[:k]


def _sample_weight(score: float, floor: float) -> float:
    # 略抬高分，但不锁死头部（幂次 1.3）
    return max(0.05, (score - floor + 3.0) ** 1.3)


def weighted_sample(
    shortlist: list[SurpriseRanked],
    *,
    n: int = 10,
    exclude_ids: Iterable[str] | None = None,
    rng: random.Random | None = None,
) -> list[SurpriseRanked]:
    """在短名单内按 Interesting 权重无放回抽样。

    exclude_ids 仅软避开；若剩余不足 n，忽略排除，允许轻度重复。
    """
    if not shortlist or n <= 0:
        return []
    rnd = rng or random.SystemRandom()
    ban = {str(x) for x in (exclude_ids or [])}
    cand = [r for r in shortlist if r.mod.id not in ban]
    if len(cand) < n:
        cand = list(shortlist)

    if len(cand) <= n:
        out = list(cand)
        rnd.shuffle(out)
        for i, r in enumerate(out, start=1):
            r.rank = i
        return out

    floor = min(r.score for r in cand)
    pool = list(cand)
    weights = [_sample_weight(r.score, floor) for r in pool]
    picked: list[SurpriseRanked] = []
    for _ in range(n):
        total = sum(weights)
        if total <= 0 or not pool:
            break
        x = rnd.random() * total
        acc = 0.0
        idx = 0
        for i, w in enumerate(weights):
            acc += w
            if acc >= x:
                idx = i
                break
        picked.append(pool.pop(idx))
        weights.pop(idx)

    rnd.shuffle(picked)
    for i, r in enumerate(picked, start=1):
        r.rank = i
    return picked
