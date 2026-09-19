"""Tests for HOI4 localization $OtherKey$ nesting."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from focus_topology.names import lookup_focus_names


def _make_db(rows: list[tuple[str, str, str]]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    path = Path(tmp.name)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE localization (key TEXT, lang TEXT, value TEXT, "
        "PRIMARY KEY (key, lang))"
    )
    con.executemany(
        "INSERT INTO localization(key, lang, value) VALUES (?,?,?)", rows
    )
    con.commit()
    con.close()
    return path


class LocNestingTests(unittest.TestCase):
    def test_whole_string_redirect_prefers_chinese(self) -> None:
        db = _make_db(
            [
                (
                    "PRC_sea_strengthen_the_central_secretariat",
                    "simp_chinese",
                    "$PRC_strengthen_the_central_secretariat$",
                ),
                (
                    "PRC_sea_strengthen_the_central_secretariat",
                    "english",
                    "$PRC_strengthen_the_central_secretariat$",
                ),
                (
                    "PRC_strengthen_the_central_secretariat",
                    "simp_chinese",
                    "强化中央书记处",
                ),
                (
                    "PRC_strengthen_the_central_secretariat",
                    "english",
                    "Strengthen the Central Secretariat",
                ),
            ]
        )
        try:
            names = lookup_focus_names(
                db,
                ["PRC_sea_strengthen_the_central_secretariat"],
                lang="simp_chinese",
                fallback_lang="english",
            )
            self.assertEqual(
                names["PRC_sea_strengthen_the_central_secretariat"],
                "强化中央书记处",
            )
        finally:
            db.unlink(missing_ok=True)

    def test_unresolved_redirect_omitted(self) -> None:
        db = _make_db(
            [
                ("a", "english", "$missing$"),
                ("a", "simp_chinese", "$missing$"),
            ]
        )
        try:
            names = lookup_focus_names(db, ["a"])
            self.assertNotIn("a", names)
        finally:
            db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
