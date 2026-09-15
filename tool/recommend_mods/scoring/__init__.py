"""模组推荐综合打分：Fit + SoftPop + SoftFresh + Endorse；另有惊喜通道。"""

from .formula import soft_fresh, soft_pop, total_score
from .fit_tfidf import FitTfidf
from .catalog import load_ge300_details
from .rank import rank_mods
from .surprise import build_surprise_shortlist, rank_surprise, weighted_sample

__all__ = [
    "soft_fresh",
    "soft_pop",
    "total_score",
    "FitTfidf",
    "load_ge300_details",
    "rank_mods",
    "build_surprise_shortlist",
    "rank_surprise",
    "weighted_sample",
]
