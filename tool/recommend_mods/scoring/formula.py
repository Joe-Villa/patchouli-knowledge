from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

# —— 量纲：三项可正可负，相加成总分；Fit 为主力，低订重罚 ——
FIT_SCALE = 100.0  # fit∈[0,1] → 贡献约 [0,100]

# SoftPop：以 100 订为盈亏平衡；以下重罚，以上轻奖且饱和
POP_THRESHOLD = 100.0
POP_PENALTY_MAX = 45.0  # s=0 时约 -45
POP_BONUS_MAX = 10.0  # 极高订封顶约 +10
POP_BONUS_TAU = 280.0  # 越大，百级以上涨得越慢
POP_BONUS_CAP_AT = 20_000.0

# SoftFresh：加减分，幅度小于 Fit / 低订惩罚
FRESH_RECENT = 5.0  # <6 个月
FRESH_MID = 0.0  # 6 个月～2 年
FRESH_OLD = -8.0  # >2 年
FRESH_UNKNOWN = -4.0

PopMode = Literal["default", "popular", "ignore"]


def soft_pop(subscribers: int | float | None, mode: PopMode = "default") -> float:
    """订阅贡献分（可负）。

    - s < 100：二次重罚，越少越惨
    - s = 100：0
    - s > 100：对数缓奖，边际递减
    """
    if mode == "ignore":
        return 0.0
    s = max(0.0, float(subscribers or 0))
    penalty_max = POP_PENALTY_MAX
    bonus_max = POP_BONUS_MAX
    if mode == "popular":
        penalty_max = 55.0
        bonus_max = 14.0

    if s < POP_THRESHOLD:
        # ((100-s)/100)^2 ∈ (0,1] → 乘 -penalty_max
        t = (POP_THRESHOLD - s) / POP_THRESHOLD
        return -penalty_max * (t * t)

    # s >= 100：log1p 饱和到 bonus_max
    span = math.log1p((POP_BONUS_CAP_AT - POP_THRESHOLD) / POP_BONUS_TAU)
    if span <= 0:
        return 0.0
    x = math.log1p((s - POP_THRESHOLD) / POP_BONUS_TAU) / span
    return bonus_max * min(1.0, max(0.0, x))


def soft_fresh(
    time_updated: int | float | datetime | None,
    *,
    now: datetime | None = None,
    disabled: bool = False,
) -> float:
    """维护贡献分（可负）。"""
    if disabled:
        return 0.0
    if time_updated is None or time_updated == "":
        return FRESH_UNKNOWN
    if isinstance(time_updated, datetime):
        updated = time_updated if time_updated.tzinfo else time_updated.replace(tzinfo=timezone.utc)
    else:
        try:
            ts = float(time_updated)
            if ts > 1e12:
                ts /= 1000.0
            updated = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return FRESH_UNKNOWN
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - updated).total_seconds() / 86400.0)
    if days < 183:
        return FRESH_RECENT
    if days < 730:
        return FRESH_MID
    return FRESH_OLD


def total_score(
    fit: float,
    *,
    subscribers: int | float | None,
    time_updated: int | float | datetime | None,
    endorse: float = 0.0,
    pop_mode: PopMode = "default",
    now: datetime | None = None,
    fresh_disabled: bool = False,
) -> dict[str, float]:
    """三项相加。返回的 fit/soft_pop/soft_fresh 均为贡献分（可负）。

    endorse：额外加分（旧乘子 1+e 改为直接加 e 量级的分）；默认 0。
    """
    fit01 = max(0.0, min(1.0, float(fit)))
    fit_c = FIT_SCALE * fit01
    sp = soft_pop(subscribers, pop_mode)
    sf = soft_fresh(time_updated, now=now, disabled=fresh_disabled)
    en = float(endorse)
    score = fit_c + sp + sf + en
    return {
        "fit": fit_c,
        "fit_raw": fit01,
        "soft_pop": sp,
        "soft_fresh": sf,
        "endorse": en,
        "score": score,
    }
