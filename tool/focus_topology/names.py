"""Resolve focus display names from a corpus localization.sqlite."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

# HOI4 loc nesting: "$OtherKey$" (whole value or embedded).
_LOC_REF = re.compile(r"\$([A-Za-z0-9_.\-]+)\$")
_LOC_REF_ONLY = re.compile(r"^\$([A-Za-z0-9_.\-]+)\$\s*$")
_MAX_LOC_DEPTH = 8


def resolve_loc_db(game_root: Path, *, vanilla_root: Path | None = None) -> Path | None:
    primary = Path(game_root) / "localization.sqlite"
    if primary.is_file() and primary.stat().st_size > 0:
        return primary
    if vanilla_root is not None:
        fallback = Path(vanilla_root) / "localization.sqlite"
        if fallback.is_file() and fallback.stat().st_size > 0:
            return fallback
    return None


def resolve_loc_dbs(
    game_root: Path, *, vanilla_root: Path | None = None
) -> list[Path]:
    """Mod loc first, then vanilla — later DBs only fill missing keys."""
    out: list[Path] = []
    primary = Path(game_root) / "localization.sqlite"
    if primary.is_file() and primary.stat().st_size > 0:
        out.append(primary)
    if vanilla_root is not None:
        fallback = Path(vanilla_root) / "localization.sqlite"
        if (
            fallback.is_file()
            and fallback.stat().st_size > 0
            and fallback.resolve() not in {p.resolve() for p in out}
        ):
            out.append(fallback)
    return out


def _usable_loc_value(key: str, value: str | None) -> str | None:
    """Reject empty / whitespace / untranslated placeholders that equal the key."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == key or text == f"${key}$":
        return None
    return text


def lookup_focus_names(
    db_path: Path | None | list[Path] | tuple[Path, ...],
    focus_ids: list[str],
    *,
    lang: str = "simp_chinese",
    fallback_lang: str = "english",
) -> dict[str, str]:
    """id → display name; missing keys omitted (caller falls back to id).

    Preference: ``lang`` (中文) → ``fallback_lang`` (英文) → omit (caller uses key).
    Resolves HOI4 ``$OtherKey$`` nesting.
    """
    return lookup_loc_keys(
        db_path, focus_ids, lang=lang, fallback_lang=fallback_lang
    )


def lookup_loc_keys(
    db_path: Path | None | list[Path] | tuple[Path, ...],
    keys: list[str],
    *,
    lang: str = "simp_chinese",
    fallback_lang: str = "english",
) -> dict[str, str]:
    """loc key → value; missing keys omitted.

    Order: primary lang, then fallback_lang. Multiple DBs: earlier wins;
    later DBs only fill keys still missing. Expands ``$OtherKey$`` redirects.
    """
    if not keys:
        return {}
    if db_path is None:
        return {}
    if isinstance(db_path, (list, tuple)):
        paths = [Path(p) for p in db_path]
    else:
        paths = [Path(db_path)]

    out: dict[str, str] = {}
    pending = list(dict.fromkeys(keys))
    for path in paths:
        if not pending:
            break
        if not path.is_file():
            continue
        filled = _lookup_loc_keys_one(
            path, pending, lang=lang, fallback_lang=fallback_lang
        )
        out.update(filled)
        pending = [k for k in pending if k not in out]
    return out


def _fetch_loc_rows(
    conn: sqlite3.Connection,
    keys: list[str],
    langs: list[str],
) -> dict[str, dict[str, str]]:
    by_key: dict[str, dict[str, str]] = {}
    if not keys:
        return by_key
    chunk = 400
    lang_ph = ",".join("?" * len(langs))
    for i in range(0, len(keys), chunk):
        batch = keys[i : i + chunk]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT key, lang, value FROM localization "
            f"WHERE key IN ({placeholders}) AND lang IN ({lang_ph})",
            [*batch, *langs],
        ).fetchall()
        for row in rows:
            by_key.setdefault(row["key"], {})[row["lang"]] = row["value"]
    return by_key


def _collect_refs(text: str | None) -> list[str]:
    if not text:
        return []
    return _LOC_REF.findall(str(text))


def _pick_raw(
    bucket: dict[str, str], *, lang: str, fallback_lang: str
) -> str | None:
    if lang in bucket and bucket[lang] is not None:
        return bucket[lang]
    if fallback_lang != lang and fallback_lang in bucket:
        return bucket[fallback_lang]
    return None


def _expand_loc_value(
    key: str,
    by_key: dict[str, dict[str, str]],
    *,
    lang: str,
    fallback_lang: str,
    _seen: frozenset[str] | None = None,
    _depth: int = 0,
) -> str | None:
    """Expand HOI4 ``$Other$`` nesting; return None if unusable."""
    if _depth > _MAX_LOC_DEPTH:
        return None
    seen = _seen or frozenset()
    if key in seen:
        return None
    seen = seen | {key}
    bucket = by_key.get(key) or {}

    # Prefer primary lang chain, then fallback lang chain.
    for try_lang in (
        [lang, fallback_lang] if fallback_lang != lang else [lang]
    ):
        raw = bucket.get(try_lang)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text or text == key or text == f"${key}$":
            continue

        m = _LOC_REF_ONLY.match(text)
        if m:
            target = m.group(1)
            expanded = _expand_loc_value(
                target,
                by_key,
                lang=lang,
                fallback_lang=fallback_lang,
                _seen=seen,
                _depth=_depth + 1,
            )
            if expanded:
                return expanded
            continue

        if "$" in text:

            def _repl(match: re.Match[str]) -> str:
                target = match.group(1)
                nested = _expand_loc_value(
                    target,
                    by_key,
                    lang=lang,
                    fallback_lang=fallback_lang,
                    _seen=seen,
                    _depth=_depth + 1,
                )
                return nested if nested else match.group(0)

            text = _LOC_REF.sub(_repl, text).strip()
            if not text or text == key or _LOC_REF_ONLY.match(text):
                continue
        return text
    return None


def _lookup_loc_keys_one(
    path: Path,
    keys: list[str],
    *,
    lang: str,
    fallback_lang: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = list(dict.fromkeys(keys))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        langs = [lang]
        if fallback_lang and fallback_lang != lang:
            langs.append(fallback_lang)

        by_key = _fetch_loc_rows(conn, ids, langs)
        # Pull redirect targets so nesting can resolve in one pass.
        pending_refs = {
            ref
            for bucket in by_key.values()
            for raw in bucket.values()
            for ref in _collect_refs(raw)
            if ref not in by_key
        }
        guard = 0
        while pending_refs and guard < _MAX_LOC_DEPTH:
            guard += 1
            extra = _fetch_loc_rows(conn, sorted(pending_refs), langs)
            by_key.update(extra)
            pending_refs = {
                ref
                for bucket in extra.values()
                for raw in bucket.values()
                for ref in _collect_refs(raw)
                if ref not in by_key
            }

        for key in ids:
            val = _expand_loc_value(
                key, by_key, lang=lang, fallback_lang=fallback_lang
            )
            if val:
                out[key] = val
    finally:
        conn.close()
    return out


def apply_names_to_graph(
    graph: dict[str, Any],
    names: dict[str, str],
) -> dict[str, Any]:
    """Set node ``name`` from loc map; missing → keep id (key)."""
    for node in graph.get("nodes") or []:
        nid = str(node.get("id") or "")
        if not nid:
            continue
        node["name"] = names.get(nid) or nid
    return graph


def lookup_tag_names(
    db_path: Path | None | list[Path] | tuple[Path, ...],
    tags: list[str],
    *,
    lang: str = "simp_chinese",
    fallback_lang: str = "english",
) -> dict[str, str]:
    """country tag → localized name (HOI4 loc key usually equals the tag)."""
    return lookup_loc_keys(db_path, tags, lang=lang, fallback_lang=fallback_lang)


def format_tag_label(tag: str, name: str | None = None) -> str:
    """Display as 中华人民共和国(PRC); fall back to bare tag if no name."""
    t = (tag or "").strip()
    if not t:
        return ""
    n = (name or "").strip()
    if n:
        return f"{n}({t})"
    return t
