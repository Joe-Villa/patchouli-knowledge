from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(
    "/home/liulingda/桌面/Patchouli Knowledge/database/data/steam/529340/community/workshop_details/workshop_details_all.sqlite"
)
FALLBACK_DB = Path(
    "/home/liulingda/桌面/Patchouli Knowledge/database/data/steam/529340/community/workshop_details/workshop_details_ge300.sqlite"
)
DEFAULT_BRIEF = Path(
    "/home/liulingda/桌面/Patchouli Knowledge/database/data/steam/529340/community/workshop_brief/vic3_mods_all.sqlite"
)
FALLBACK_BRIEF = Path(
    "/home/liulingda/桌面/Patchouli Knowledge/database/data/steam/529340/community/workshop_brief/vic3_mods_ge300.sqlite"
)
DEFAULT_MODS_CATALOG = Path(
    "/home/liulingda/桌面/Patchouli Knowledge/service/1836/data/mods_catalog.json"
)

_TAG_SPLIT = re.compile(r"[|,/]+")
_BBCODE = re.compile(r"\[/?[^\]]+\]")
_WS = re.compile(r"\s+")

# 可选：普通推荐 Endorse 仍可走 formula；惊喜通道不读此表。
EXTRA_ENDORSE_IDS: dict[str, float] = {}


def clean_description(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = _BBCODE.sub(" ", text)
    return _WS.sub(" ", text).strip()


@dataclass
class ModRecord:
    id: str
    title: str  # 详情库原始快照（兼容）
    title_en: str = ""
    title_zh: str = ""
    description: str = ""
    tags: str = ""
    subscribers: int = 0
    time_updated: int | None = None
    file_size: int = 0
    creator: str = ""  # SteamID64（详情库）
    author: str = ""  # 工坊显示名
    favorited: int = 0
    views: int = 0
    vote_score: float | None = None  # 可选；惊喜通道不使用星级/评分

    @property
    def title_display(self) -> str:
        """展示优先中文标题。"""
        return (self.title_zh or self.title_en or self.title or "").strip()

    @property
    def title_for_fit(self) -> str:
        """检索用：中英标题去重拼接。"""
        parts: list[str] = []
        for t in (self.title_zh, self.title_en, self.title):
            t = (t or "").strip()
            if t and t not in parts:
                parts.append(t)
        return " ".join(parts)

    @property
    def titles_blob(self) -> str:
        return "\n".join(
            t for t in (self.title_zh, self.title_en, self.title) if (t or "").strip()
        )

    @property
    def tag_text(self) -> str:
        parts = [p.strip() for p in _TAG_SPLIT.split(self.tags or "") if p.strip()]
        return " ".join(parts)

    @property
    def description_clean(self) -> str:
        return clean_description(self.description)

    @property
    def description_len(self) -> int:
        return len(self.description_clean)


def _row_int(row: sqlite3.Row, key: str, default: int = 0) -> int:
    keys = row.keys()
    if key not in keys:
        return default
    try:
        v = row[key]
        if v in (None, ""):
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _row_float_opt(row: sqlite3.Row, key: str) -> float | None:
    keys = row.keys()
    if key not in keys:
        return None
    try:
        v = row[key]
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_brief_path(raw: str | Path | None = None) -> Path | None:
    if raw is not None and str(raw).strip():
        p = Path(str(raw).strip())
        return p if p.is_file() else None
    for p in (
        Path("/data/vic3_mods_all.sqlite"),
        Path("/data/vic3_mods_ge300.sqlite"),
        DEFAULT_BRIEF,
        FALLBACK_BRIEF,
    ):
        if p.is_file():
            return p
    return None


def load_author_map(brief_path: Path | str | None = None) -> dict[str, str]:
    """mod id → 工坊作者显示名。brief 缺失则空表。"""
    path = resolve_brief_path(brief_path)
    if not path:
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(mods)")}
        if "id" not in cols or "author" not in cols:
            return {}
        return {
            str(mid): str(name)
            for mid, name in con.execute(
                "SELECT id, author FROM mods WHERE author IS NOT NULL AND author != ''"
            )
            if mid
        }
    finally:
        con.close()


def load_ge300_details(
    db_path: Path | str | None = None,
    *,
    brief_path: Path | str | None = None,
    author_map: dict[str, str] | None = None,
) -> list[ModRecord]:
    if db_path:
        path = Path(db_path)
    elif DEFAULT_DB.is_file():
        path = DEFAULT_DB
    else:
        path = FALLBACK_DB
    authors = author_map if author_map is not None else load_author_map(brief_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(details)")}
    select = [
        "id",
        "title",
        "description",
        "tags",
        "subscriptions",
        "time_updated",
        "file_size",
        "creator",
    ]
    for opt in ("favorited", "views", "vote_score", "score", "title_en", "title_zh", "author"):
        if opt in cols:
            select.append(opt)
    rows = con.execute(f"SELECT {', '.join(select)} FROM details").fetchall()
    con.close()
    out: list[ModRecord] = []
    for r in rows:
        vote = _row_float_opt(r, "vote_score")
        if vote is None:
            vote = _row_float_opt(r, "score")
        mid = str(r["id"])
        raw_title = (r["title"] or "").strip()
        keys = r.keys()
        title_en = (r["title_en"] or "").strip() if "title_en" in keys else ""
        title_zh = (r["title_zh"] or "").strip() if "title_zh" in keys else ""
        if not title_en:
            title_en = title_zh or raw_title
        if not title_zh:
            title_zh = title_en or raw_title
        author = ""
        if "author" in keys and r["author"]:
            author = str(r["author"]).strip()
        if not author:
            author = authors.get(mid, "")
        out.append(
            ModRecord(
                id=mid,
                title=raw_title,
                title_en=title_en,
                title_zh=title_zh,
                description=(r["description"] or "").strip(),
                tags=(r["tags"] or "").strip(),
                subscribers=_row_int(r, "subscriptions", 0),
                time_updated=_row_int(r, "time_updated", 0) or None,
                file_size=_row_int(r, "file_size", 0),
                creator=str(r["creator"] or ""),
                author=author,
                favorited=_row_int(r, "favorited", 0),
                views=_row_int(r, "views", 0),
                vote_score=vote,
            )
        )
    out.sort(key=lambda m: m.subscribers, reverse=True)
    return out


def load_endorse_weights(
    catalog_path: Path | str | None = None,
) -> dict[str, float]:
    """保留接口；惊喜通道已不用。普通推荐若启用 Endorse 可再接。"""
    path = Path(catalog_path) if catalog_path else DEFAULT_MODS_CATALOG
    weights: dict[str, float] = dict(EXTRA_ENDORSE_IDS)
    if not path.is_file():
        return weights
    return weights
