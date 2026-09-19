"""原版法律三类 / 科技三类目录（vanilla + 模组覆盖）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ingest.paradox import extract_block, iter_txt, read_text, strip_comments

LAW_CAT_ORDER = ("power_structure", "economy", "human_rights")
LAW_CAT_LABELS = {
    "power_structure": "权力结构",
    "economy": "经济",
    "human_rights": "人权",
    "other": "其他",
}

# 与 file/1910/法律组布局以及默认法律 一致：每槽位 (法律组, 默认法律)
LAW_LAYOUT: dict[str, list[tuple[str, str]]] = {
    "power_structure": [
        ("lawgroup_governance_principles", "law_monarchy"),
        ("lawgroup_distribution_of_power", "law_autocracy"),
        ("lawgroup_citizenship", "law_subjecthood"),
        ("lawgroup_church_and_state", "law_freedom_of_conscience"),
        ("lawgroup_bureaucracy", "law_appointed_bureaucrats"),
        ("lawgroup_army_model", "law_peasant_levies"),
        ("lawgroup_navy_model", "law_merchant_navy"),
        ("lawgroup_internal_security", "law_no_home_affairs"),
    ],
    "economy": [
        ("lawgroup_economic_system", "law_traditionalism"),
        ("lawgroup_trade_policy", "law_isolationism"),
        ("lawgroup_taxation", "law_land_based_taxation"),
        ("lawgroup_land_reform", "law_serfdom"),
        ("lawgroup_colonization", "law_no_colonial_affairs"),
        ("lawgroup_policing", "law_no_police"),
        ("lawgroup_education_system", "law_no_schools"),
        ("lawgroup_health_system", "law_no_health_system"),
    ],
    "human_rights": [
        ("lawgroup_free_speech", "law_censorship"),
        ("lawgroup_labor_rights", "law_no_workers_rights"),
        ("lawgroup_childrens_rights", "law_child_labor_allowed"),
        ("lawgroup_rights_of_women", "law_no_womens_rights"),
        ("lawgroup_welfare", "law_no_social_security"),
        ("lawgroup_migration", "law_closed_borders"),
        ("lawgroup_slavery", "law_slavery_banned"),
        ("lawgroup_labour_associations", "law_guild_system"),
    ],
}
IGNORE_LAW_GROUPS = frozenset(
    {
        "lawgroup_caste_hegemony",  # 种姓
        "lawgroup_edo_social_system",  # 江户等级制度
    }
)
LAYOUT_LAW_GROUPS = frozenset(
    gid for slots in LAW_LAYOUT.values() for gid, _ in slots
)

TECH_CAT_ORDER = ("production", "military", "society")
TECH_CAT_LABELS = {
    "production": "生产",
    "military": "军事",
    "society": "社会",
    "other": "其他",
}

_LAW_HEADER = re.compile(
    r"^(?:REPLACE_OR_CREATE|REPLACE|INJECT)?:?(law_[a-z0-9_]+)\s*=\s*\{",
    re.M,
)
_LAW_GROUP = re.compile(r"group\s*=\s*(lawgroup_\w+)")
_LG_HEADER = re.compile(
    r"^(?:REPLACE_OR_CREATE|REPLACE|INJECT)?:?(lawgroup_\w+)\s*=\s*\{",
    re.M,
)
_LG_CAT = re.compile(r"law_group_category\s*=\s*(\w+)")
_TECH_HEADER = re.compile(
    r"^(?:REPLACE_OR_CREATE|REPLACE|INJECT)?:?([a-z][a-z0-9_-]*)\s*=\s*\{",
    re.M,
)
_TECH_CAT = re.compile(r"category\s*=\s*(production|military|society)")
_TECH_ERA = re.compile(r"era\s*=\s*(era_\d+)")
_SKIP_TECH_KEYS = frozenset({"if", "else", "limit", "modifier", "ai_weight"})

_EFFECT_HEADER = re.compile(
    r"^(effect_starting_technology_tier_\d+_tech)\s*=\s*\{",
    re.M,
)
_EFFECT_TECH = re.compile(r"add_technology_researched\s*=\s*([A-Za-z0-9_-]+)")
_EFFECT_ERA = re.compile(r"add_era_researched\s*=\s*(era_\d+)")


def _scan_law_groups(roots: list[Path]) -> dict[str, str]:
    """lawgroup_* -> power_structure|economy|human_rights"""
    out: dict[str, str] = {}
    for root in roots:
        for path in iter_txt(root / "common" / "law_groups"):
            text = strip_comments(read_text(path))
            for m in _LG_HEADER.finditer(text):
                gid = m.group(1)
                block = extract_block(text, m.end() - 1)
                cm = _LG_CAT.search(block)
                if cm:
                    out[gid] = cm.group(1)
    return out


def _scan_laws(roots: list[Path]) -> dict[str, str]:
    """law_* -> lawgroup_*"""
    out: dict[str, str] = {}
    for root in roots:
        for path in iter_txt(root / "common" / "laws"):
            text = strip_comments(read_text(path))
            for m in _LAW_HEADER.finditer(text):
                lid = m.group(1)
                block = extract_block(text, m.end() - 1)
                gm = _LAW_GROUP.search(block)
                if gm:
                    out[lid] = gm.group(1)
    return out


def _scan_techs(
    roots: list[Path],
) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """tech→category、tech→era、era→[tech ids]。含 law_enforcement 等。"""
    cats: dict[str, str] = {}
    tech_era: dict[str, str] = {}
    eras: dict[str, list[str]] = {}
    seen_era: dict[str, set[str]] = {}
    for root in roots:
        tech_dir = root / "common" / "technology" / "technologies"
        for path in iter_txt(tech_dir):
            text = strip_comments(read_text(path))
            for m in _TECH_HEADER.finditer(text):
                tid = m.group(1)
                if tid in _SKIP_TECH_KEYS:
                    continue
                block = extract_block(text, m.end() - 1)
                cm = _TECH_CAT.search(block)
                if not cm:
                    continue
                cats[tid] = cm.group(1)
                em = _TECH_ERA.search(block)
                if not em:
                    continue
                era = em.group(1)
                tech_era[tid] = era
                bag = seen_era.setdefault(era, set())
                if tid not in bag:
                    bag.add(tid)
                    eras.setdefault(era, []).append(tid)
    return cats, tech_era, eras


def load_starting_tech_effects(roots: list[Path]) -> dict[str, dict[str, Any]]:
    """effect_starting_technology_tier_N_tech -> {techs, eras}。后扫描的覆盖先前。"""
    out: dict[str, dict[str, Any]] = {}
    for root in roots:
        for path in iter_txt(root / "common" / "scripted_effects"):
            text = strip_comments(read_text(path))
            for m in _EFFECT_HEADER.finditer(text):
                eid = m.group(1)
                block = extract_block(text, m.end() - 1)
                out[eid] = {
                    "techs": _EFFECT_TECH.findall(block),
                    "eras": _EFFECT_ERA.findall(block),
                }
    return out


def expand_tech_ids(
    raw_ids: list[str],
    *,
    effects: dict[str, dict[str, Any]],
    era_techs: dict[str, list[str]],
) -> list[str]:
    """把 effect_* / add_era_researched 展开成具体科技 id，保序去重。"""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(tid: str) -> None:
        if tid in seen:
            return
        seen.add(tid)
        ordered.append(tid)

    for token in raw_ids:
        if token.startswith("effect_") and token in effects:
            body = effects[token]
            for era in body.get("eras") or []:
                for tid in era_techs.get(era) or []:
                    add(tid)
            for tid in body.get("techs") or []:
                add(tid)
            continue
        if token.startswith("era_"):
            for tid in era_techs.get(token) or []:
                add(tid)
            continue
        add(token)
    return ordered


def build_catalogs(
    vanilla: Path, mod: Path
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, list[str]],
    dict[str, dict[str, Any]],
]:
    """law→group, group→cat, tech→cat, tech→era, era→techs, starting effects。"""
    roots = [vanilla, mod]
    group_to_cat = _scan_law_groups(roots)
    law_to_group = _scan_laws(roots)
    techs, tech_era, era_techs = _scan_techs(roots)
    effects = load_starting_tech_effects(roots)
    return law_to_group, group_to_cat, techs, tech_era, era_techs, effects


def group_laws(
    items: list[dict],
    *,
    law_to_group: dict[str, str],
    group_to_cat: dict[str, str],
    loc: dict[str, str],
) -> list[dict]:
    """按固定法律组顺序填槽；缺省用默认法律（红字）；种姓/江户忽略；其余进其他。"""
    from ingest import loc as locmod

    by_group: dict[str, dict] = {}
    for it in items:
        gid = law_to_group.get(it["id"])
        if not gid or gid in IGNORE_LAW_GROUPS:
            continue
        if gid not in by_group:
            by_group[gid] = it

    out: list[dict] = []
    for cat in LAW_CAT_ORDER:
        slots: list[dict] = []
        for gid, default_id in LAW_LAYOUT[cat]:
            if gid in by_group:
                it = by_group[gid]
                slots.append(
                    {
                        "id": it["id"],
                        "name": it["name"],
                        "group_id": gid,
                        "is_default": False,
                        "undefined": False,
                        "amendments": it.get("amendments") or [],
                    }
                )
            else:
                slots.append(
                    {
                        "id": default_id,
                        "name": locmod.lookup(loc, default_id, default=default_id),
                        "group_id": gid,
                        "is_default": True,
                        "undefined": False,
                        "amendments": [],
                    }
                )
        out.append({"id": cat, "name": LAW_CAT_LABELS[cat], "items": slots})

    other_gids = sorted(
        {
            g
            for g in group_to_cat
            if g not in LAYOUT_LAW_GROUPS and g not in IGNORE_LAW_GROUPS
        }
        | {
            g
            for g in by_group
            if g not in LAYOUT_LAW_GROUPS and g not in IGNORE_LAW_GROUPS
        }
    )
    other_items: list[dict] = []
    for gid in other_gids:
        gname = locmod.lookup(loc, gid, default=gid)
        if gid in by_group:
            it = by_group[gid]
            other_items.append(
                {
                    "id": it["id"],
                    "name": it["name"],
                    "group_id": gid,
                    "group_name": gname,
                    "is_default": False,
                    "undefined": False,
                    "amendments": it.get("amendments") or [],
                }
            )
        else:
            other_items.append(
                {
                    "id": "",
                    "name": "未定义",
                    "group_id": gid,
                    "group_name": gname,
                    "is_default": False,
                    "undefined": True,
                    "amendments": [],
                }
            )
    if other_items:
        out.append(
            {"id": "other", "name": LAW_CAT_LABELS["other"], "items": other_items}
        )
    return out


def _era_num(era_id: str) -> int:
    m = re.match(r"era_(\d+)$", era_id or "")
    return int(m.group(1)) if m else 10_000


def _era_label(era_id: str) -> str:
    n = _era_num(era_id)
    if n < 10_000:
        return f"时代 {n}"
    return era_id or "未知"


def group_techs(
    items: list[dict],
    tech_to_cat: dict[str, str],
    tech_to_era: dict[str, str],
) -> list[dict]:
    """按生产/军事/社会分框，框内再按 era 分组。"""
    owned_by_cat: dict[str, list] = {k: [] for k in TECH_CAT_ORDER}
    owned_by_cat["other"] = []
    for it in items:
        cat = tech_to_cat.get(it["id"], "other")
        if cat not in owned_by_cat:
            cat = "other"
        owned_by_cat[cat].append(it)

    catalog: dict[str, dict[str, set[str]]] = {k: {} for k in TECH_CAT_ORDER}
    for tid, cat in tech_to_cat.items():
        if cat not in catalog:
            continue
        era = tech_to_era.get(tid) or "other"
        catalog[cat].setdefault(era, set()).add(tid)

    out: list[dict] = []
    for cat in TECH_CAT_ORDER:
        owned_items = owned_by_cat[cat]
        by_era_owned: dict[str, list] = {}
        for it in owned_items:
            era = tech_to_era.get(it["id"]) or "other"
            by_era_owned.setdefault(era, []).append(it)

        max_owned = 0
        for era in by_era_owned:
            max_owned = max(max_owned, _era_num(era) if era != "other" else 0)

        era_ids = sorted(
            set(catalog[cat]) | set(by_era_owned),
            key=lambda e: (_era_num(e) if e != "other" else 999, e),
        )
        eras_out: list[dict] = []
        for era in era_ids:
            n = _era_num(era) if era != "other" else 999
            owned_list = by_era_owned.get(era, [])
            all_in_era = catalog[cat].get(era, set())
            if not owned_list and (not all_in_era or n > max_owned):
                continue
            eras_out.append(
                {
                    "id": era,
                    "name": _era_label(era),
                    "items": owned_list,
                }
            )

        out.append(
            {
                "id": cat,
                "name": TECH_CAT_LABELS[cat],
                "items": owned_items,
                "eras": eras_out,
            }
        )

    other_items = owned_by_cat["other"]
    if other_items:
        out.append(
            {
                "id": "other",
                "name": TECH_CAT_LABELS["other"],
                "items": other_items,
                "eras": [
                    {
                        "id": "other",
                        "name": "其他",
                        "items": other_items,
                    }
                ],
            }
        )
    return out
