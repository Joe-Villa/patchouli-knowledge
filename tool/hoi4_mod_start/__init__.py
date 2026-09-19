"""HOI4 模组开局时间：解析、落库、列表排序与展示标签。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1

# NDefines.NGame.START_DATE = "2020.1.1.1"
_NGAME_START = re.compile(
    r"NDefines\.NGame\.START_DATE\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE,
)
# vanilla-style inside NGame = { START_DATE = "1936.1.1.12", ... }
_BLOCK_START = re.compile(r"(?m)^\s*START_DATE\s*=\s*\"([^\"]+)\"")
_BOOKMARK_DATE = re.compile(
    r"(?m)^\s*date\s*=\s*([0-9]{3,4}\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
)


def default_index_path(repo: Path | None = None) -> Path:
    raw = os.environ.get("HOI4_MOD_START", "").strip() or os.environ.get(
        "FOCUS_MOD_START", ""
    ).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if repo is None:
        # tool/hoi4_mod_start → repo
        repo = Path(__file__).resolve().parents[2]
    return (repo / "database" / "derived" / "hoi4" / "mod_start.json").resolve()


def _date_key(date: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in date.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _year_of(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(date.split(".", 1)[0])
    except ValueError:
        return None


def parse_game_start(game_root: Path) -> dict[str, Any]:
    """Parse START_DATE from defines, else earliest bookmark date."""
    root = Path(game_root)
    defines = root / "common" / "defines"
    ngame_hits: list[tuple[str, str]] = []
    block_hits: list[tuple[str, str]] = []
    if defines.is_dir():
        for path in sorted(defines.glob("*.lua")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _NGAME_START.finditer(text):
                ngame_hits.append((m.group(1), path.name))
            for m in _BLOCK_START.finditer(text):
                # skip TENSION_TIME_SCALE_START_DATE etc. on same-ish line prefix
                line_start = text.rfind("\n", 0, m.start()) + 1
                line = text[line_start : m.start()]
                if "TENSION" in line.upper():
                    continue
                block_hits.append((m.group(1), path.name))

    if ngame_hits:
        date, fname = ngame_hits[0]
        return {
            "start_date": date,
            "start_year": _year_of(date),
            "source": "defines_ngame",
            "source_file": fname,
        }
    if block_hits:
        date, fname = block_hits[0]
        return {
            "start_date": date,
            "start_year": _year_of(date),
            "source": "defines_block",
            "source_file": fname,
        }

    bookmarks = root / "common" / "bookmarks"
    dates: list[str] = []
    if bookmarks.is_dir():
        for path in bookmarks.glob("*.txt"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _BOOKMARK_DATE.search(text)
            if m:
                dates.append(m.group(1))
    if dates:
        dates.sort(key=_date_key)
        date = dates[0]
        return {
            "start_date": date,
            "start_year": _year_of(date),
            "source": "bookmark_min",
            "source_file": None,
        }
    return {
        "start_date": None,
        "start_year": None,
        "source": "unknown",
        "source_file": None,
    }


def format_mod_label(*, name: str, start_year: int | None) -> str:
    """UI label: ``(2020) The Fire Rises``；未知年份用 ``(?)``。"""
    title = (name or "").strip() or "?"
    if start_year is None:
        return f"(?) {title}"
    return f"({start_year}) {title}"


def load_mod_start_index(path: Path | None = None) -> dict[str, Any]:
    p = path or default_index_path()
    if not p.is_file():
        return {"schema": SCHEMA, "ok": False, "mods": {}, "vanilla": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA, "ok": False, "mods": {}, "vanilla": None}
    if not isinstance(data, dict):
        return {"schema": SCHEMA, "ok": False, "mods": {}, "vanilla": None}
    mods = data.get("mods") or {}
    if not isinstance(mods, dict):
        mods = {}
    return {
        "schema": data.get("schema") or SCHEMA,
        "ok": True,
        "built_at": data.get("built_at"),
        "mods": mods,
        "vanilla": data.get("vanilla"),
        "path": str(p),
    }


def lookup_start(
    index: dict[str, Any], mod_id: str
) -> tuple[str | None, int | None]:
    mid = str(mod_id or "").strip()
    if mid.lower() in {"vanilla", "van", "base"}:
        raw = index.get("vanilla") or {}
        return raw.get("start_date"), raw.get("start_year")
    raw = (index.get("mods") or {}).get(mid) or {}
    return raw.get("start_date"), raw.get("start_year")


def sort_key_for_mod(start_year: int | None, *, mod_id: str = "") -> tuple:
    """Known years ascending; unknown last; tie-break by id."""
    if start_year is None:
        return (1, 10**9, str(mod_id))
    return (0, int(start_year), str(mod_id))


def enrich_mod_entry(
    raw: dict[str, Any],
    index: dict[str, Any],
    *,
    is_vanilla: bool = False,
) -> dict[str, Any]:
    mid = "vanilla" if is_vanilla else str(raw.get("id") or "").strip()
    name = str(raw.get("name") or mid).strip()
    short = str(raw.get("short") or mid).strip()
    date, year = lookup_start(index, mid)
    label = format_mod_label(name=name, start_year=year)
    out = {
        "id": mid,
        "short": short,
        "name": name,
        "label": label,
        "category": raw.get("category") or ("base" if is_vanilla else "common"),
        "start_date": date,
        "start_year": year,
    }
    return out


def sort_mod_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort non-vanilla by start_year; caller should keep vanilla separate."""
    return sorted(
        entries,
        key=lambda m: sort_key_for_mod(m.get("start_year"), mod_id=str(m.get("id") or "")),
    )


def build_mod_start_index(
    *,
    catalog_path: Path,
    mods_root: Path,
    vanilla_root: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    mods_out: dict[str, Any] = {}
    for raw in catalog.get("mods") or []:
        mid = str(raw.get("id") or "").strip()
        if not mid:
            continue
        root = Path(mods_root) / mid
        if root.is_dir():
            parsed = parse_game_start(root)
        else:
            parsed = {
                "start_date": None,
                "start_year": None,
                "source": "missing_root",
                "source_file": None,
            }
        mods_out[mid] = {
            "id": mid,
            "short": raw.get("short"),
            "name": raw.get("name"),
            **parsed,
        }

    vanilla_meta = None
    if vanilla_root is not None and Path(vanilla_root).is_dir():
        vanilla_meta = {
            "id": "vanilla",
            "short": "vanilla",
            "name": "Vanilla (本体)",
            **parse_game_start(Path(vanilla_root)),
        }

    payload = {
        "ok": True,
        "schema": SCHEMA,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "catalog": str(Path(catalog_path).resolve()),
        "mods_root": str(Path(mods_root).resolve()),
        "vanilla": vanilla_meta,
        "mods": mods_out,
        "stats": {
            "mods": len(mods_out),
            "known": sum(1 for m in mods_out.values() if m.get("start_year") is not None),
            "unknown": sum(1 for m in mods_out.values() if m.get("start_year") is None),
        },
    }
    dest = out_path or default_index_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload["path"] = str(dest)
    return payload
