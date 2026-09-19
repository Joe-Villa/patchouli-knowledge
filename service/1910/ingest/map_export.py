"""调用地图编辑器建库，导出官网只读 PNG + 国家档案。"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ingest import loc as locmod
from ingest.catalogs import build_catalogs, group_laws, group_techs
from ingest.history_stats import sum_building_levels_by_tag, sum_population_by_tag
from ingest.site import flavor_by_tag, parse_country_history, parse_state_regions
from paths import editor_root, vic3kit_src


def _origin_src() -> Path:
    editor = editor_root()
    for cand in (
        editor / "app" / "collect_origin_data" / "src",
        editor.parent / "collect_origin_data" / "src",
        editor / "collect_origin_data" / "src",
    ):
        if (cand / "history_states_flat.py").is_file():
            return cand
    return editor / "collect_origin_data" / "src"


def _prepare_imports() -> None:
    editor = editor_root()
    kit = vic3kit_src()
    origin = _origin_src()
    catalogs = Path("/home/liulingda/桌面/vic3modder/catalogs")
    extra = [
        str(origin),
        str(editor / "src"),
        str(editor / "map_db"),
        str(editor),
        str(kit),
        str(catalogs / "cultures" / "src"),
        str(catalogs / "religions" / "src"),
        str(catalogs / "shared" / "src"),
    ]
    for p in extra:
        if p not in sys.path:
            sys.path.insert(0, p)
    from bootstrap.paths import register_import_paths

    register_import_paths()
    # 编辑器根下有空的 collect_origin_data/src，必须盖过去
    origin_s = str(origin)
    if origin_s in sys.path:
        sys.path.remove(origin_s)
    sys.path.insert(0, origin_s)
    compat_sr = editor / "collect_origin_data" / "src"
    if (compat_sr / "state_region_flat.py").is_file():
        cs = str(compat_sr)
        if cs in sys.path:
            sys.path.remove(cs)
        sys.path.insert(0, cs)
    for extra_cat in (
        str(catalogs / "cultures" / "src"),
        str(catalogs / "religions" / "src"),
        str(catalogs / "shared" / "src"),
    ):
        if extra_cat in sys.path:
            sys.path.remove(extra_cat)
        sys.path.insert(0, extra_cat)
    import editor_config

    schema = editor_root() / "schema.sql"
    if schema.is_file():
        editor_config.SCHEMA_PATH = schema
    import compose_catalogs

    def _compose(conn: sqlite3.Connection, db_path: Path, alias: str, insert_sql: str) -> int:
        path = str(db_path.resolve())
        conn.execute(f"ATTACH DATABASE ? AS {alias}", (path,))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(insert_sql)
            n = int(conn.execute("SELECT changes()").fetchone()[0])
            conn.commit()
            return n
        finally:
            try:
                conn.execute(f"DETACH DATABASE {alias}")
            except sqlite3.OperationalError:
                conn.commit()
                conn.execute(f"DETACH DATABASE {alias}")

    compose_catalogs.compose_religions = lambda conn, religions_db: _compose(
        conn,
        religions_db,
        "cat_rel",
        """
        INSERT INTO ref_religion (religion, r, g, b, name_zh, name_en)
        SELECT religion, r, g, b, '', ''
        FROM cat_rel.religion
        """,
    )
    compose_catalogs.compose_cultures = lambda conn, cultures_db: _compose(
        conn,
        cultures_db,
        "cat_cul",
        """
        INSERT INTO ref_culture (culture, default_religion, r, g, b)
        SELECT culture, default_religion, r, g, b
        FROM cat_cul.culture
        """,
    )


def build_sqlite(mod: Path, vanilla: Path, sqlite_path: Path, *, rebuild: bool) -> None:
    if sqlite_path.is_file() and not rebuild:
        return
    _prepare_imports()
    from bootstrap import build_map_db
    from editor_config import MapEditorConfig

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    def progress(cur: int, total: int, msg: str) -> None:
        print(f"[map] {cur}/{total} {msg}", flush=True)

    build_map_db(
        mod,
        sqlite_path,
        MapEditorConfig(vanilla=vanilla),
        fail_on_error=False,
        skip_map_images=False,
        on_build_progress=progress,
    )


def export_map_assets(sqlite_path: Path, map_dir: Path) -> dict[str, Any]:
    _prepare_imports()
    from interactive_map.db_reader import (
        load_countries_json,
        load_names_json,
        load_provinces_json,
        load_provinces_png_bytes,
    )
    from interactive_map.province_model import load_province_model
    from interactive_map.render import render_ownership_png

    map_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        png = load_provinces_png_bytes(conn)
        (map_dir / "provinces.png").write_bytes(png)
        model = load_province_model(conn, png)
        own, _, _ = render_ownership_png(model, conn)
        (map_dir / "ownership.png").write_bytes(own)
        provinces = load_provinces_json(conn)
        countries = load_countries_json(conn)
        names = load_names_json(conn, locale="zh")
        state_prov_counts: dict[str, int] = defaultdict(int)
        tag_state_counts: dict[tuple[str, str], int] = defaultdict(int)
        for tag, state in conn.execute("SELECT tag, state FROM st_prov"):
            state_prov_counts[str(state)] += 1
            tag_state_counts[(str(tag), str(state))] += 1
        owned_tags = {str(t) for (t,) in conn.execute("SELECT DISTINCT tag FROM st_prov")}
    finally:
        conn.close()

    hex_to_tag = {hex_key: info["tag"] for hex_key, info in provinces.items()}
    (map_dir / "provinces.json").write_text(
        json.dumps(hex_to_tag, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tag_meta = {}
    for tag, info in countries.items():
        tag_meta[tag] = {
            "r": info["r"],
            "g": info["g"],
            "b": info["b"],
            "name": (names.get("tags") or {}).get(tag, tag),
        }
    (map_dir / "countries.json").write_text(
        json.dumps(tag_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "state_prov_counts": dict(state_prov_counts),
        "tag_state_counts": {
            f"{tag}::{state}": n for (tag, state), n in tag_state_counts.items()
        },
        "owned_tags": sorted(owned_tags),
        "tag_meta": tag_meta,
    }


def assemble_country_dossiers(
    *,
    mod: Path,
    vanilla: Path,
    loc: dict[str, str],
    groups: list[dict[str, Any]],
    map_stats: dict[str, Any],
) -> dict[str, Any]:
    law_to_group, group_to_cat, tech_cats, tech_eras, era_techs, tech_effects = (
        build_catalogs(vanilla, mod)
    )
    history = parse_country_history(
        mod, loc, tech_effects=tech_effects, era_techs=era_techs
    )
    regions = parse_state_regions(vanilla, mod)
    flav = flavor_by_tag(groups)
    pops = sum_population_by_tag(mod)
    levels = sum_building_levels_by_tag(mod)
    state_n = map_stats["state_prov_counts"]
    share = map_stats["tag_state_counts"]
    dossiers: dict[str, Any] = {}
    names = map_stats["tag_meta"]
    for tag in map_stats["owned_tags"]:
        arable = 0.0
        resources: dict[str, float] = defaultdict(float)
        for key, n_owned in share.items():
            t, state = key.split("::", 1)
            if t != tag:
                continue
            total = state_n.get(state) or 0
            if total <= 0:
                continue
            ratio = n_owned / total
            info = regions.get(state) or {}
            arable += float(info.get("arable_land") or 0) * ratio
            for rid, amt in (info.get("resources") or {}).items():
                resources[rid] += float(amt) * ratio
        res_out = []
        for rid, amt in sorted(resources.items(), key=lambda kv: -kv[1]):
            if amt < 0.5:
                continue
            res_out.append(
                {
                    "id": rid,
                    "name": locmod.lookup(loc, rid, default=rid),
                    "amount": int(round(amt)),
                }
            )
        hist = history.get(tag) or {"laws": [], "techs": []}
        meta = names.get(tag) or {}
        dossiers[tag] = {
            "tag": tag,
            "name": locmod.lookup(loc, tag, f"COUNTRY_{tag}", default=meta.get("name") or tag),
            "flavor": flav.get(tag, ""),
            "population": int(pops.get(tag, 0)),
            "building_levels": int(levels.get(tag, 0)),
            "arable_land": int(round(arable)),
            "resources": res_out,
            "laws": hist["laws"],
            "techs": hist["techs"],
            "law_groups": group_laws(
                hist["laws"],
                law_to_group=law_to_group,
                group_to_cat=group_to_cat,
                loc=loc,
            ),
            "tech_groups": group_techs(hist["techs"], tech_cats, tech_eras),
        }
    return dossiers
