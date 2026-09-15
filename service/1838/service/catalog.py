"""工坊详情卡片：只读与 1837 相同的 workshop_details sqlite。

主库字段含 title_en / title_zh / author（由 merge_titles_into_details 写入）。
不再依赖独立 workshop_i18n.sqlite。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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

# 与现网 details 表对齐（含双语 title + author）
ALL_COLUMNS = (
    "id",
    "result",
    "title",
    "title_en",
    "title_zh",
    "description",
    "creator",
    "author",
    "file_size",
    "preview_url",
    "time_created",
    "time_updated",
    "subscriptions",
    "favorited",
    "views",
    "lifetime_subscriptions",
    "lifetime_favorited",
    "lifetime_playtime",
    "tags",
    "url",
    "created",
    "updated",
)

_DIGITS = re.compile(r"^\d+$")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ModCard:
    id: str
    result: int | None
    title: str
    title_en: str
    title_zh: str
    description: str
    creator: str
    author: str
    file_size: str
    preview_url: str
    time_created: int | None
    time_updated: int | None
    subscriptions: int
    favorited: int
    views: int
    lifetime_subscriptions: int
    lifetime_favorited: int
    lifetime_playtime: int
    tags: str
    url: str
    created: str
    updated: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        d = self.to_dict()
        desc = d.get("description") or ""
        if len(desc) > 280:
            d["description"] = desc[:280] + "…"
            d["description_truncated"] = True
        else:
            d["description_truncated"] = False
        return d


def _row_int(row: sqlite3.Row, key: str) -> int | None:
    if key not in row.keys():
        return None
    v = row[key]
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _row_str(row: sqlite3.Row, key: str) -> str:
    if key not in row.keys():
        return ""
    v = row[key]
    if v is None:
        return ""
    return str(v)


def row_to_card(row: sqlite3.Row, *, author_fallback: str = "") -> ModCard:
    raw = _row_str(row, "title")
    te = _row_str(row, "title_en").strip()
    tz = _row_str(row, "title_zh").strip()
    if not te:
        te = tz or raw
    if not tz:
        tz = te or raw
    author = _row_str(row, "author").strip() or author_fallback
    return ModCard(
        id=_row_str(row, "id"),
        result=_row_int(row, "result"),
        title=raw,
        title_en=te,
        title_zh=tz,
        description=_row_str(row, "description"),
        creator=_row_str(row, "creator"),
        author=author,
        file_size=_row_str(row, "file_size"),
        preview_url=_row_str(row, "preview_url"),
        time_created=_row_int(row, "time_created"),
        time_updated=_row_int(row, "time_updated"),
        subscriptions=_row_int(row, "subscriptions") or 0,
        favorited=_row_int(row, "favorited") or 0,
        views=_row_int(row, "views") or 0,
        lifetime_subscriptions=_row_int(row, "lifetime_subscriptions") or 0,
        lifetime_favorited=_row_int(row, "lifetime_favorited") or 0,
        lifetime_playtime=_row_int(row, "lifetime_playtime") or 0,
        tags=_row_str(row, "tags"),
        url=_row_str(row, "url"),
        created=_row_str(row, "created"),
        updated=_row_str(row, "updated"),
    )


def load_author_map(brief_path: Path | str | None) -> dict[str, str]:
    if not brief_path:
        return {}
    path = Path(brief_path)
    if not path.is_file():
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


def resolve_brief_path(raw: str | None = None) -> Path | None:
    if raw and raw.strip():
        p = Path(raw.strip())
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


def resolve_db_path(raw: str | None = None) -> Path:
    if raw and raw.strip():
        return Path(raw.strip())
    for p in (
        Path("/data/workshop_details_all.sqlite"),
        Path("/data/workshop_details_ge300.sqlite"),
        DEFAULT_DB,
        FALLBACK_DB,
    ):
        if p.is_file():
            return p
    return DEFAULT_DB


class WorkshopCatalog:
    def __init__(
        self,
        db_path: Path | str,
        *,
        brief_path: Path | str | None = None,
        authors: dict[str, str] | None = None,
    ) -> None:
        self.path = Path(db_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"workshop db missing: {self.path}")
        self.brief_path = str(brief_path) if brief_path else ""
        self._authors = authors if authors is not None else load_author_map(brief_path)
        self._con = sqlite3.connect(
            f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
        )
        self._con.row_factory = sqlite3.Row
        present = {r[1] for r in self._con.execute("PRAGMA table_info(details)")}
        # 旧库可无缺少 title_en/zh/author：SELECT 时跳过，row_to_card 会回退
        self._cols = [c for c in ALL_COLUMNS if c in present]
        if "id" not in self._cols or "title" not in self._cols:
            raise RuntimeError("details missing required columns id/title")
        self._cols_sql = ", ".join(self._cols)
        self.has_title_i18n = "title_en" in present and "title_zh" in present
        self.total = int(self._con.execute("SELECT COUNT(*) FROM details").fetchone()[0])
        if self.has_title_i18n:
            self.title_split = int(
                self._con.execute(
                    "SELECT COUNT(*) FROM details "
                    "WHERE ifnull(title_en,'') != ifnull(title_zh,'')"
                ).fetchone()[0]
            )
        else:
            self.title_split = 0
        self.authors_matched = int(
            self._con.execute(
                "SELECT COUNT(*) FROM details WHERE ifnull(author,'') != ''"
            ).fetchone()[0]
        )
        if self.authors_matched == 0 and self._authors:
            self.authors_matched = sum(
                1
                for (mid,) in self._con.execute("SELECT id FROM details")
                if mid in self._authors
            )

    def close(self) -> None:
        self._con.close()

    def _card(self, row: sqlite3.Row) -> ModCard:
        mid = _row_str(row, "id")
        return row_to_card(row, author_fallback=self._authors.get(mid, ""))

    def get(self, mod_id: str) -> ModCard | None:
        mid = (mod_id or "").strip()
        if not mid:
            return None
        row = self._con.execute(
            f"SELECT {self._cols_sql} FROM details WHERE id = ? LIMIT 1",
            (mid,),
        ).fetchone()
        return self._card(row) if row else None

    def browse(
        self,
        *,
        offset: int = 0,
        limit: int = 24,
        sort: str = "subscriptions",
    ) -> tuple[list[ModCard], int]:
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100))
        order = {
            "subscriptions": "subscriptions DESC, id ASC",
            "updated": "time_updated DESC, id ASC",
            "created": "time_created DESC, id ASC",
            "title": "title COLLATE NOCASE ASC, id ASC",
            "id": "CAST(id AS INTEGER) ASC",
        }.get(sort, "subscriptions DESC, id ASC")
        rows = self._con.execute(
            f"SELECT {self._cols_sql} FROM details ORDER BY {order} LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._card(r) for r in rows], self.total

    def search(self, query: str, *, limit: int = 40) -> list[ModCard]:
        q = _WS.sub(" ", (query or "").strip())
        if not q:
            return []
        limit = max(1, min(int(limit), 100))

        if _DIGITS.match(q):
            exact = self.get(q)
            if exact:
                return [exact]
            rows = self._con.execute(
                f"SELECT {self._cols_sql} FROM details WHERE id LIKE ? "
                f"ORDER BY subscriptions DESC LIMIT ?",
                (f"{q}%", limit),
            ).fetchall()
            return [self._card(r) for r in rows]

        like = f"%{q}%"
        if self.has_title_i18n:
            where = (
                "(title LIKE ? COLLATE NOCASE "
                "OR ifnull(title_en,'') LIKE ? COLLATE NOCASE "
                "OR ifnull(title_zh,'') LIKE ? COLLATE NOCASE "
                "OR ifnull(author,'') LIKE ? COLLATE NOCASE)"
            )
            params: tuple[Any, ...] = (like, like, like, like, limit)
        else:
            where = "(title LIKE ? COLLATE NOCASE OR ifnull(author,'') LIKE ? COLLATE NOCASE)"
            params = (like, like, limit)
        # author 也可能只在 brief map
        rows = self._con.execute(
            f"SELECT {self._cols_sql} FROM details WHERE {where} "
            f"ORDER BY subscriptions DESC LIMIT ?",
            params,
        ).fetchall()
        cards = [self._card(r) for r in rows]
        if len(cards) >= limit:
            return cards[:limit]

        qfold = q.casefold()
        by_author = [
            mid for mid, name in self._authors.items() if qfold in name.casefold()
        ]
        if not by_author:
            return cards
        seen = {c.id for c in cards}
        extra_ids = [mid for mid in by_author if mid not in seen][:200]
        if not extra_ids:
            return cards
        placeholders = ",".join("?" for _ in extra_ids)
        extra = self._con.execute(
            f"SELECT {self._cols_sql} FROM details WHERE id IN ({placeholders}) "
            f"ORDER BY subscriptions DESC LIMIT ?",
            (*extra_ids, limit),
        ).fetchall()
        for r in extra:
            mid = _row_str(r, "id")
            if mid in seen:
                continue
            cards.append(self._card(r))
            seen.add(mid)
            if len(cards) >= limit:
                break
        return cards[:limit]
