"""一键构建 database/derived/1910。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.comments import fetch_comments, fetch_workshop_description_zh, pick_reviews
from ingest.loc import load_locale
from ingest.map_export import assemble_country_dossiers, build_sqlite, export_map_assets
    from ingest.site import (
    buildings,
    contacts,
    count_characters,
    count_events,
    count_loc_keys,
    extract_qq_group,
    objectives,
    start_country_files,
    start_date,
    workshop_description,
    worldlines,
    FUSED_MODS,
    TIPS,
)
from paths import (
    WORKSHOP_CREATOR,
    WORKSHOP_ID,
    derived_dir,
    mod_root,
    vanilla_root,
    workshop_db,
)


def main() -> None:
    mod = mod_root()
    vanilla = vanilla_root()
    out = derived_dir()
    out.mkdir(parents=True, exist_ok=True)
    map_dir = out / "map"
    rebuild = os.environ.get("INTERWAR_REBUILD_MAP", "").strip() in {"1", "true", "yes"}

    print(f"mod={mod}")
    print(f"vanilla={vanilla}")
    print(f"derived={out}")

    loc = load_locale(mod, vanilla)
    zh_desc = ""
    try:
        zh_desc = fetch_workshop_description_zh(file_id=WORKSHOP_ID)
        print(f"workshop zh desc chars={len(zh_desc)}")
    except Exception as exc:
        print(f"workshop zh desc failed: {exc}")
    desc = workshop_description(workshop_db(), zh_text=zh_desc)
    groups = objectives(mod, loc)
    start_tags = start_country_files(mod)

    reviews: list = []
    try:
        raw = fetch_comments(creator=WORKSHOP_CREATOR, file_id=WORKSHOP_ID)
        reviews = pick_reviews(raw)
        print(f"comments fetched={len(raw)} picked={len(reviews)}")
    except Exception as exc:
        print(f"comments failed: {exc}")

    thumb = mod / "thumbnail.png"
    if thumb.is_file():
        shutil.copy2(thumb, out / "thumbnail.png")

    site = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "intro": {
            "name": desc.get("title") or "Interwar 1910-1960",
            "author": desc.get("author") or "by天草",
            "events": count_events(mod),
            "characters": count_characters(mod),
            "loc_keys": count_loc_keys(mod),
            "contacts": contacts(loc, qq_group=extract_qq_group(zh_desc)),
            "fused_mods": FUSED_MODS,
            "buildings": buildings(mod, loc),
            "workshop_html": desc.get("html") or "",
            "reviews": reviews,
        },
        "world": {
            "start_date": start_date(mod),
            "country_count": len(start_tags),
            "start_tags": start_tags,
            "objectives": groups,
        },
        "guides": {
            "worldlines": worldlines(loc),
            "tips": TIPS,
        },
    }
    (out / "site.json").write_text(
        json.dumps(site, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("wrote site.json")

    sqlite_path = out / "map_editor.sqlite"
    print("building map sqlite…")
    build_sqlite(mod, vanilla, sqlite_path, rebuild=rebuild)
    print("exporting map png/json…")
    map_stats = export_map_assets(sqlite_path, map_dir)
    dossiers = assemble_country_dossiers(
        mod=mod,
        vanilla=vanilla,
        loc=loc,
        groups=groups,
        map_stats=map_stats,
    )
    (out / "countries.json").write_text(
        json.dumps(dossiers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"done events={site['intro']['events']} "
        f"chars={site['intro']['characters']} "
        f"loc={site['intro']['loc_keys']} "
        f"countries={len(dossiers)}"
    )


if __name__ == "__main__":
    main()
