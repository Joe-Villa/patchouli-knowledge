#!/usr/bin/env python3
"""融入模组目录：加载 catalog、解析题干 → 选定 corpus（含两方对比）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent
DEFAULT_CATALOG = _DATA / "mods_catalog.json"
DEFAULT_MODS_OUT = _DATA / "mods"

# 模组类型（category）：模组条目所属池。禁止 vanillaonly。
CATEGORY_COMMON = "common"
CATEGORY_BYTIANCAO = "bytiancao"
VALID_CATEGORIES = frozenset({CATEGORY_COMMON, CATEGORY_BYTIANCAO})

# Agent 可见池（status）：与 category 对齐；vanillaonly = 不看任何模组。
STATUS_COMMON = "common"
STATUS_BYTIANCAO = "bytiancao"
STATUS_VANILLAONLY = "vanillaonly"
VALID_STATUSES = frozenset(
    {STATUS_COMMON, STATUS_BYTIANCAO, STATUS_VANILLAONLY}
)

# 对比意图：有这些才把双模组 / 模组+原版当成对比，否则双模组仍算歧义
_COMPARE_INTENT_RE = re.compile(
    r"对比|比较|对照|区别|差异|差别|不同|"
    r"有何不同|有什么不同|哪儿不同|哪里不同|有啥区别|"
    r"\bvs\.?\b|versus|"
    r"比原版|较原版|与原版|和原版|相对原版|相对官版|"
    r"相差|差在",
    re.IGNORECASE,
)
_VANILLA_MENTION_RE = re.compile(r"原版|vanilla|官版", re.IGNORECASE)


def normalize_category(raw: Any) -> str:
    """模组 category；缺省 / 非法 → common。兼容拼写 catagory。"""
    if raw is None:
        return CATEGORY_COMMON
    c = str(raw).strip().lower()
    if c in {"", "general", "通用"}:
        return CATEGORY_COMMON
    if c in VALID_CATEGORIES:
        return c
    return CATEGORY_COMMON


def normalize_status(raw: Any) -> str:
    """请求 status；缺省 / 非法 → common。"""
    if raw is None:
        return STATUS_COMMON
    s = str(raw).strip().lower().replace("-", "").replace("_", "")
    if s in {"", "general", "通用"}:
        return STATUS_COMMON
    if s in {"vanilla", "vanillaonly", "onlyvanilla", "vanillamode"}:
        return STATUS_VANILLAONLY
    if s in {"bytiancao", "tiancao", "天草"}:
        return STATUS_BYTIANCAO
    if s == "common":
        return STATUS_COMMON
    # 未压缩形式再试一次
    s2 = str(raw).strip().lower()
    if s2 in VALID_STATUSES:
        return s2
    return STATUS_COMMON


@dataclass(frozen=True)
class ModInfo:
    id: str
    short: str
    name: str
    aliases: tuple[str, ...] = ()
    category: str = CATEGORY_COMMON

    def display_name(self) -> str:
        if self.short and self.short != self.name:
            return f"{self.short}（{self.name}）"
        return self.name or self.short or self.id


@dataclass
class ModCatalog:
    workshop_app: int
    workshop_root: Path
    mods: list[ModInfo] = field(default_factory=list)
    by_id: dict[str, ModInfo] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> ModCatalog:
        p = Path(path or DEFAULT_CATALOG).resolve()
        data = json.loads(p.read_text(encoding="utf-8"))
        workshop_root = Path(
            os.path.expanduser(
                str(
                    data.get("default_workshop_root")
                    or "~/.steam/debian-installation/steamapps/workshop/content/529340"
                )
            )
        ).resolve()
        mods: list[ModInfo] = []
        by_id: dict[str, ModInfo] = {}
        for raw in data.get("mods") or []:
            mid = str(raw["id"]).strip()
            aliases = tuple(
                dict.fromkeys(
                    [
                        *(str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()),
                        str(raw.get("short") or "").strip(),
                        str(raw.get("name") or "").strip(),
                        mid,
                    ]
                )
            )
            aliases = tuple(a for a in aliases if a)
            # 兼容用户拼写 catagory；禁止把模组标成 vanillaonly
            cat_raw = raw.get("category", raw.get("catagory"))
            category = normalize_category(cat_raw)
            info = ModInfo(
                id=mid,
                short=str(raw.get("short") or mid).strip(),
                name=str(raw.get("name") or mid).strip(),
                aliases=aliases,
                category=category,
            )
            mods.append(info)
            by_id[mid] = info
        return cls(
            workshop_app=int(data.get("workshop_app") or 529340),
            workshop_root=workshop_root,
            mods=mods,
            by_id=by_id,
        )

    def workshop_mod_dir(self, mod_id: str) -> Path:
        return self.workshop_root / str(mod_id)

    def reduced_mod_dir(self, mod_id: str, mods_root: Path | None = None) -> Path:
        root = Path(mods_root or DEFAULT_MODS_OUT).resolve()
        return root / str(mod_id)

    def mods_for_status(self, status: str) -> list[ModInfo]:
        """当前 status 可见的模组池；vanillaonly → 空。"""
        st = normalize_status(status)
        if st == STATUS_VANILLAONLY:
            return []
        return [m for m in self.mods if m.category == st]

    def view_for_status(self, status: str) -> ModCatalog:
        """返回仅含可见池的浅视图（共享 ModInfo）。"""
        st = normalize_status(status)
        pool = self.mods_for_status(st)
        return ModCatalog(
            workshop_app=self.workshop_app,
            workshop_root=self.workshop_root,
            mods=list(pool),
            by_id={m.id: m for m in pool},
        )


@dataclass(frozen=True)
class CorpusSide:
    """对比中的一方，或单 corpus 的规范化描述。"""

    kind: str  # vanilla | mod
    mod: ModInfo | None = None
    matched_alias: str | None = None

    def label(self) -> str:
        if self.kind == "vanilla":
            return "原版游戏"
        if self.mod is not None:
            return self.mod.display_name()
        return "未知资料库"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "matched_alias": self.matched_alias,
            "mod": None
            if self.mod is None
            else {
                "id": self.mod.id,
                "short": self.mod.short,
                "name": self.mod.name,
                "category": self.mod.category,
            },
            "label": self.label(),
        }


@dataclass(frozen=True)
class CorpusChoice:
    """一次请求选定的资料库（单方、两方对比、或类别整池）。"""

    kind: str  # vanilla | mod | compare | pool | rejected
    mod: ModInfo | None = None
    reason: str = ""
    matched_alias: str | None = None
    sides: tuple[CorpusSide, ...] = ()
    status: str = STATUS_COMMON
    # kind=pool：当前 status 下可见的全部模组（只读挂载，非「一次只答一个」）
    pool: tuple[ModInfo, ...] = ()

    @property
    def ok(self) -> bool:
        return self.kind in {"vanilla", "mod", "compare", "pool"}

    @property
    def is_compare(self) -> bool:
        return self.kind == "compare" and len(self.sides) == 2

    def banner_line(self) -> str:
        """固定模板：API 层拼进回答，非 LLM。"""
        if self.kind == "vanilla":
            return "【资料库】原版游戏"
        if self.kind == "pool":
            label = {"common": "通用", "bytiancao": "by天草"}.get(self.status, self.status)
            return f"【资料库】{label}整池（{len(self.pool)} 个模组 + 原版）"
        if self.kind == "mod" and self.mod is not None:
            m = self.mod
            return f"【资料库】{m.display_name()}  workshop_id={m.id}"
        if self.kind == "compare" and len(self.sides) == 2:
            a, b = self.sides
            return f"【资料库·对比】{a.label()}  vs  {b.label()}"
        return f"【资料库】无法确定（{self.reason or 'rejected'}）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "reason": self.reason,
            "matched_alias": self.matched_alias,
            "is_compare": self.is_compare,
            "status": self.status,
            "mod": None
            if self.mod is None
            else {
                "id": self.mod.id,
                "short": self.mod.short,
                "name": self.mod.name,
                "category": self.mod.category,
            },
            "pool": [
                {"id": m.id, "short": m.short, "name": m.name, "category": m.category}
                for m in self.pool
            ],
            "pool_size": len(self.pool),
            "sides": [s.to_dict() for s in self.sides],
            "banner": self.banner_line(),
        }


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _alias_hits(alias: str, question: str) -> bool:
    """短 ASCII 别名按词边界匹配，避免 RA⊂historical、RA⊂centralization。"""
    a = alias.strip()
    if not a:
        return False
    if len(a) <= 3 and re.fullmatch(r"[A-Za-z0-9]+", a):
        pat = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(a)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        return pat.search(question) is not None
    q_norm = _normalize(question)
    return _normalize(a) in q_norm or a in question


def _has_compare_intent(question: str) -> bool:
    return _COMPARE_INTENT_RE.search(question or "") is not None


def _mentions_vanilla(question: str) -> bool:
    return _VANILLA_MENTION_RE.search(question or "") is not None


def _collect_mod_hits(question: str, catalog: ModCatalog) -> list[tuple[ModInfo, str]]:
    """题干命中的模组（去重）；每项为 (mod, matched_alias)。只扫 catalog.mods。"""
    q = question
    by_mod: dict[str, tuple[int, ModInfo, str]] = {}

    for m in catalog.mods:
        if m.id in q:
            by_mod[m.id] = (10_000 + len(m.id), m, m.id)

    for m in catalog.mods:
        for alias in m.aliases:
            if not _alias_hits(alias, q):
                continue
            length = len(alias.strip())
            prev = by_mod.get(m.id)
            if prev is None or length > prev[0]:
                by_mod[m.id] = (length, m, alias.strip())

    # 稳定顺序：按 matched alias 长度降序，再按 id
    ranked = sorted(by_mod.values(), key=lambda t: (-t[0], t[1].id))
    return [(m, alias) for _len, m, alias in ranked]


def _with_status(choice: CorpusChoice, status: str) -> CorpusChoice:
    st = normalize_status(status)
    if choice.status == st:
        return choice
    return CorpusChoice(
        kind=choice.kind,
        mod=choice.mod,
        reason=choice.reason,
        matched_alias=choice.matched_alias,
        sides=choice.sides,
        status=st,
        pool=choice.pool,
    )


@dataclass(frozen=True)
class CorpusPin:
    """显式锁定资料库（网页选项等）。

    status：自动判断/标签的可选模组范围（common | bytiancao | vanillaonly）。
    mode=auto：未选手动 tag，按题干在当前范围内自动判断；
    vanillaonly 时无视 mode/mod_ids。
    """

    status: str = STATUS_COMMON
    mode: str = "auto"  # auto | vanilla | mods
    mod_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Any) -> CorpusPin:
        if raw is None:
            return cls()
        if isinstance(raw, CorpusPin):
            return raw
        if not isinstance(raw, dict):
            return cls()

        status = normalize_status(raw.get("status"))
        mode = str(raw.get("mode") or "auto").strip().lower()

        # 旧客户端：mode=vanilla / vanilla_only=true → status=vanillaonly
        if mode in {"vanilla", "vanilla_only", "only_vanilla"} or bool(
            raw.get("vanilla_only")
        ):
            return cls(status=STATUS_VANILLAONLY, mode="vanilla")

        if status == STATUS_VANILLAONLY:
            return cls(status=STATUS_VANILLAONLY, mode="vanilla")

        ids: list[str] = []
        for item in raw.get("mod_ids") or raw.get("tags") or []:
            mid = str(item or "").strip()
            if mid and mid not in ids:
                ids.append(mid)

        if mode in {"mods", "mod", "tags", "tag"}:
            return cls(
                status=status,
                mode="mods",
                mod_ids=tuple(ids[:2]),
            )
        if ids:
            return cls(status=status, mode="mods", mod_ids=tuple(ids[:2]))
        return cls(status=status, mode="auto")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": normalize_status(self.status),
            "mode": self.mode,
            "mod_ids": list(self.mod_ids),
        }

    @property
    def is_locked(self) -> bool:
        st = normalize_status(self.status)
        if st == STATUS_VANILLAONLY:
            return True
        return self.mode in {"vanilla", "mods"}


def choice_from_pin(pin: CorpusPin, catalog: ModCatalog) -> CorpusChoice | None:
    """按显式锁定生成 CorpusChoice；auto/未锁定返回 None。"""
    if pin is None:
        return None
    status = normalize_status(pin.status)
    if status == STATUS_VANILLAONLY or pin.mode == "vanilla":
        return CorpusChoice(
            kind="vanilla",
            reason="ui_vanilla_only",
            status=STATUS_VANILLAONLY,
        )
    if pin.mode != "mods":
        return None

    view = catalog.view_for_status(status)
    ids = list(pin.mod_ids)
    if not ids:
        return CorpusChoice(
            kind="rejected",
            reason="pin_mods_empty",
            status=status,
        )
    if len(ids) > 2:
        return CorpusChoice(
            kind="rejected",
            reason="pin_too_many_mods:" + ",".join(ids),
            status=status,
        )
    resolved: list[tuple[ModInfo, str]] = []
    missing: list[str] = []
    wrong: list[str] = []
    for mid in ids:
        info = catalog.by_id.get(mid)
        if info is None:
            missing.append(mid)
        elif mid not in view.by_id:
            wrong.append(mid)
        else:
            resolved.append((info, mid))
    if missing:
        return CorpusChoice(
            kind="rejected",
            reason="pin_unknown_mod:" + ",".join(missing),
            status=status,
        )
    if wrong:
        return CorpusChoice(
            kind="rejected",
            reason="pin_wrong_category:" + ",".join(wrong),
            status=status,
        )
    if len(resolved) == 1:
        m, alias = resolved[0]
        return CorpusChoice(
            kind="mod",
            mod=m,
            reason="ui_tag",
            matched_alias=alias,
            status=status,
        )
    (m1, a1), (m2, a2) = resolved[0], resolved[1]
    return CorpusChoice(
        kind="compare",
        reason="ui_tags_compare",
        sides=(
            CorpusSide(kind="mod", mod=m1, matched_alias=a1),
            CorpusSide(kind="mod", mod=m2, matched_alias=a2),
        ),
        status=status,
    )


def select_corpus(
    question: str,
    catalog: ModCatalog,
    *,
    pin: CorpusPin | None = None,
) -> CorpusChoice:
    """按 pin + 题干选定资料库。

    - 只看原版（vanillaonly）→ 只挂原版
    - 已选手动 tag（mode=mods）→ 锁定这些模组（≤2，须属当前类别）
    - 未选手动 tag（mode=auto）：
      - 题干别名/id 命中 1～2 个 → 单库或对比
      - 未命中 → kind=pool：可见当前 status 类别下**全部**模组（+原版对照）
    两方对比需对比意图词；超过两方或歧义 → 拒绝。
    """
    pin = pin or CorpusPin()
    status = normalize_status(pin.status)

    if status == STATUS_VANILLAONLY:
        return CorpusChoice(
            kind="vanilla",
            reason="status_vanillaonly",
            status=STATUS_VANILLAONLY,
        )

    pinned = choice_from_pin(pin, catalog)
    if pinned is not None:
        return pinned

    view = catalog.view_for_status(status)

    q = (question or "").strip()
    if not q:
        return CorpusChoice(
            kind="rejected",
            reason="empty_question",
            status=status,
        )

    hits = _collect_mod_hits(q, view)
    want_compare = _has_compare_intent(q)
    want_vanilla = _mentions_vanilla(q)

    if len(hits) > 2:
        return _with_status(
            CorpusChoice(
                kind="rejected",
                reason="too_many_mods:" + ",".join(m.id for m, _ in hits),
            ),
            status,
        )

    if len(hits) == 2:
        (m1, a1), (m2, a2) = hits[0], hits[1]
        if want_vanilla and want_compare:
            return _with_status(
                CorpusChoice(kind="rejected", reason="three_way_unsupported"),
                status,
            )
        if not want_compare:
            return _with_status(
                CorpusChoice(
                    kind="rejected",
                    reason="ambiguous_mods:" + ",".join(sorted([m1.id, m2.id])),
                ),
                status,
            )
        return CorpusChoice(
            kind="compare",
            reason="compare_two_mods",
            sides=(
                CorpusSide(kind="mod", mod=m1, matched_alias=a1),
                CorpusSide(kind="mod", mod=m2, matched_alias=a2),
            ),
            status=status,
        )

    if len(hits) == 1:
        m, alias = hits[0]
        if want_vanilla and want_compare:
            return CorpusChoice(
                kind="compare",
                mod=m,
                reason="compare_mod_vanilla",
                matched_alias=alias,
                sides=(
                    CorpusSide(kind="mod", mod=m, matched_alias=alias),
                    CorpusSide(kind="vanilla", matched_alias="原版"),
                ),
                status=status,
            )
        return CorpusChoice(
            kind="mod",
            mod=m,
            reason="workshop_id" if alias == m.id else "alias",
            matched_alias=alias,
            status=status,
        )

    # 未选手动 tag 且题干未点名 → 当前类别整池可见（不是只挂原版）
    pool = tuple(view.mods)
    if not pool:
        return CorpusChoice(
            kind="vanilla",
            reason="pool_empty",
            status=status,
        )
    return CorpusChoice(
        kind="pool",
        reason="status_pool",
        status=status,
        pool=pool,
    )


def choice_from_ids(
    mod_ids: list[str] | tuple[str, ...],
    catalog: ModCatalog,
    *,
    status: str,
    reason: str = "llm_auto",
) -> CorpusChoice:
    """按模组 id 列表生成 CorpusChoice（须已在 status 可见池内）。空 → 原版。"""
    st = normalize_status(status)
    if st == STATUS_VANILLAONLY:
        return CorpusChoice(
            kind="vanilla",
            reason="status_vanillaonly",
            status=STATUS_VANILLAONLY,
        )
    view = catalog.view_for_status(st)
    ids: list[str] = []
    for raw in mod_ids or []:
        mid = str(raw or "").strip()
        if mid and mid not in ids:
            ids.append(mid)
    if not ids:
        return CorpusChoice(kind="vanilla", reason=reason, status=st)
    if len(ids) > 2:
        return CorpusChoice(
            kind="rejected",
            reason="llm_too_many_mods:" + ",".join(ids),
            status=st,
        )
    resolved: list[tuple[ModInfo, str]] = []
    missing: list[str] = []
    wrong: list[str] = []
    for mid in ids:
        info = catalog.by_id.get(mid)
        if info is None:
            missing.append(mid)
        elif mid not in view.by_id:
            wrong.append(mid)
        else:
            resolved.append((info, mid))
    if missing:
        return CorpusChoice(
            kind="rejected",
            reason="llm_unknown_mod:" + ",".join(missing),
            status=st,
        )
    if wrong:
        return CorpusChoice(
            kind="rejected",
            reason="llm_wrong_category:" + ",".join(wrong),
            status=st,
        )
    if len(resolved) == 1:
        m, alias = resolved[0]
        return CorpusChoice(
            kind="mod",
            mod=m,
            reason=reason,
            matched_alias=alias,
            status=st,
        )
    (m1, a1), (m2, a2) = resolved[0], resolved[1]
    return CorpusChoice(
        kind="compare",
        reason=reason + "_compare",
        sides=(
            CorpusSide(kind="mod", mod=m1, matched_alias=a1),
            CorpusSide(kind="mod", mod=m2, matched_alias=a2),
        ),
        status=st,
    )


def resolve_game_root(
    choice: CorpusChoice,
    *,
    vanilla_root: Path,
    mods_root: Path | None = None,
) -> Path:
    """单 corpus 对应的削减根目录。compare 请用 resolve_side_root；pool → 原版作 /game。"""
    if choice.kind in {"vanilla", "pool"}:
        return Path(vanilla_root).resolve()
    if choice.kind == "mod" and choice.mod is not None:
        root = Path(mods_root or DEFAULT_MODS_OUT).resolve() / choice.mod.id
        return root.resolve()
    raise ValueError(f"cannot resolve game_root for {choice.kind}")


def resolve_pool_mod_roots(
    choice: CorpusChoice,
    *,
    mods_root: Path | None = None,
) -> dict[str, Path]:
    """pool 模式下：workshop_id → 削减树路径（仅已构建的目录）。"""
    if choice.kind != "pool":
        return {}
    root = Path(mods_root or DEFAULT_MODS_OUT).resolve()
    out: dict[str, Path] = {}
    for m in choice.pool:
        p = (root / m.id).resolve()
        if p.is_dir():
            out[m.id] = p
    return out


def resolve_side_root(
    side: CorpusSide,
    *,
    vanilla_root: Path,
    mods_root: Path | None = None,
) -> Path:
    """对比中单方对应的削减根目录。"""
    if side.kind == "vanilla":
        return Path(vanilla_root).resolve()
    if side.kind == "mod" and side.mod is not None:
        root = Path(mods_root or DEFAULT_MODS_OUT).resolve() / side.mod.id
        return root.resolve()
    raise ValueError(f"cannot resolve side root for {side.kind}")
