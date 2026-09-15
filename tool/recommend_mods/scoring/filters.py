"""硬过滤：用 W 元数据二值砍枝（不进打分）。"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .catalog import ModRecord, _TAG_SPLIT, clean_description

# 工坊常见标签（供 LLM / 高级面板参考）
KNOWN_TAGS = (
    "Gameplay",
    "Balance",
    "Economy and Buildings",
    "Fixes",
    "Historical",
    "Utilities",
    "Graphics",
    "Map",
    "Expansion",
    "Events",
    "Alternative History",
    "Journal Entries",
    "Pops",
    "Diplomacy",
    "Warfare",
    "Cultures and Religions",
    "Translation",
    "Technologies",
    "Interest Groups",
    "New Nations",
    "Trade",
    "Flags",
    "Total Conversion",
    "Sound",
)


@dataclass
class HardFilterSpec:
    """空字段 = 不限制。只砍「一定不行」，不做软排序。"""

    min_subscribers: int | None = None
    max_subscribers: int | None = None
    max_file_size_mb: float | None = None
    updated_within_days: int | None = None  # 必须在近 N 天内更新过
    min_description_chars: int | None = None  # 剥 BBCode 后最短简介（砍肤浅）
    require_tags_any: list[str] = field(default_factory=list)  # 至少命中一个
    require_tags_all: list[str] = field(default_factory=list)  # 全部命中
    exclude_tags_any: list[str] = field(default_factory=list)  # 命中任一则剔除
    exclude_title_keywords: list[str] = field(default_factory=list)  # 标题子串（大小写不敏感）

    def is_empty(self) -> bool:
        return (
            self.min_subscribers is None
            and self.max_subscribers is None
            and self.max_file_size_mb is None
            and self.updated_within_days is None
            and self.min_description_chars is None
            and not self.require_tags_any
            and not self.require_tags_all
            and not self.exclude_tags_any
            and not self.exclude_title_keywords
        )

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 去掉空列表 / None，方便前端展示
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None or v == []:
                continue
            out[k] = v
        return out


def _norm_tag(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip()).lower()


def _mod_tags(m: ModRecord) -> set[str]:
    return {_norm_tag(p) for p in _TAG_SPLIT.split(m.tags or "") if p.strip()}


def _as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[,|，、\n]+", v)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for x in v:
            s = str(x or "").strip()
            if s:
                out.append(s)
        return out
    return []


def parse_hard_filter(raw: Any) -> HardFilterSpec:
    if not isinstance(raw, dict):
        return HardFilterSpec()
    return HardFilterSpec(
        min_subscribers=_as_int(raw.get("min_subscribers")),
        max_subscribers=_as_int(raw.get("max_subscribers")),
        max_file_size_mb=_as_float(raw.get("max_file_size_mb")),
        updated_within_days=_as_int(raw.get("updated_within_days")),
        min_description_chars=_as_int(raw.get("min_description_chars")),
        require_tags_any=_as_str_list(raw.get("require_tags_any")),
        require_tags_all=_as_str_list(raw.get("require_tags_all")),
        exclude_tags_any=_as_str_list(raw.get("exclude_tags_any")),
        exclude_title_keywords=_as_str_list(raw.get("exclude_title_keywords")),
    )


def _uniq_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        k = _norm_tag(x) if x else ""
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out


def merge_hard_filters(*specs: HardFilterSpec | None) -> HardFilterSpec:
    """合并多份硬过滤：数值取更严，列表取并集。"""
    out = HardFilterSpec()
    for spec in specs:
        if spec is None or spec.is_empty():
            continue
        if spec.min_subscribers is not None:
            out.min_subscribers = (
                spec.min_subscribers
                if out.min_subscribers is None
                else max(out.min_subscribers, spec.min_subscribers)
            )
        if spec.max_subscribers is not None:
            out.max_subscribers = (
                spec.max_subscribers
                if out.max_subscribers is None
                else min(out.max_subscribers, spec.max_subscribers)
            )
        if spec.max_file_size_mb is not None:
            out.max_file_size_mb = (
                spec.max_file_size_mb
                if out.max_file_size_mb is None
                else min(out.max_file_size_mb, spec.max_file_size_mb)
            )
        if spec.updated_within_days is not None:
            out.updated_within_days = (
                spec.updated_within_days
                if out.updated_within_days is None
                else min(out.updated_within_days, spec.updated_within_days)
            )
        if spec.min_description_chars is not None:
            out.min_description_chars = (
                spec.min_description_chars
                if out.min_description_chars is None
                else max(out.min_description_chars, spec.min_description_chars)
            )
        out.require_tags_any = _uniq_keep_order(
            list(out.require_tags_any) + list(spec.require_tags_any)
        )
        out.require_tags_all = _uniq_keep_order(
            list(out.require_tags_all) + list(spec.require_tags_all)
        )
        out.exclude_tags_any = _uniq_keep_order(
            list(out.exclude_tags_any) + list(spec.exclude_tags_any)
        )
        out.exclude_title_keywords = _uniq_keep_order(
            list(out.exclude_title_keywords) + list(spec.exclude_title_keywords)
        )
    return out


def apply_hard_filter(
    mods: list[ModRecord],
    spec: HardFilterSpec | None,
    *,
    now_ts: float | None = None,
) -> list[ModRecord]:
    if spec is None or spec.is_empty():
        return list(mods)
    now = now_ts if now_ts is not None else time.time()
    max_bytes = (
        int(spec.max_file_size_mb * 1024 * 1024)
        if spec.max_file_size_mb is not None and spec.max_file_size_mb >= 0
        else None
    )
    min_updated = None
    if spec.updated_within_days is not None and spec.updated_within_days > 0:
        min_updated = now - spec.updated_within_days * 86400.0

    req_any = {_norm_tag(t) for t in spec.require_tags_any}
    req_all = {_norm_tag(t) for t in spec.require_tags_all}
    excl = {_norm_tag(t) for t in spec.exclude_tags_any}
    title_ban = [k.lower() for k in spec.exclude_title_keywords if k.strip()]

    out: list[ModRecord] = []
    for m in mods:
        if spec.min_subscribers is not None and m.subscribers < spec.min_subscribers:
            continue
        if spec.max_subscribers is not None and m.subscribers > spec.max_subscribers:
            continue
        if max_bytes is not None and m.file_size > max_bytes:
            continue
        if min_updated is not None:
            if m.time_updated is None or float(m.time_updated) < min_updated:
                continue
        if spec.min_description_chars is not None and spec.min_description_chars > 0:
            dlen = len(clean_description(m.description))
            if dlen < spec.min_description_chars:
                continue
        tags = _mod_tags(m)
        if req_any and tags.isdisjoint(req_any):
            continue
        if req_all and not req_all.issubset(tags):
            continue
        if excl and not tags.isdisjoint(excl):
            continue
        if title_ban:
            title_l = (m.titles_blob or m.title or "").lower()
            if any(k in title_l for k in title_ban):
                continue
        out.append(m)
    return out
