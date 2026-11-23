"""Website roster follows the public Characters board's main/hidden policy."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyped_ogre.integrations import website_roster as site


class WebsiteRosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid.db")
        con = sqlite3.connect(self.path)
        con.executescript("""
            CREATE TABLE characters (
                character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                name TEXT, class_name TEXT, spec TEXT, role TEXT,
                is_main INTEGER, roster_visible INTEGER
            );
            CREATE TABLE character_board_overrides (
                character_id INTEGER PRIMARY KEY, display_name TEXT, hidden INTEGER
            );
        """)
        con.executemany(
            "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?)", [
                (1, 100, "Big Snore", "Warrior", "Protection", "Tank", 1, 1),
                (2, 101, "Clever Priest", "Priest", "Holy", "Healer", 1, 1),
                (3, 102, "Rogue One", "Rogue", "Combat", "DPS", 1, 1),
                (4, 103, "Mage One", "Mage", "Frost", "DPS", 1, 1),
                (5, 103, "Mage Alt", "Mage", "Fire", "DPS", 0, 1),
                (6, 104, "Private Warrior", "Warrior", "Arms", "DPS", 1, 1),
                (7, 105, "LFG Only", "Warlock", "Affliction", "DPS", 1, 0),
            ],
        )
        con.execute("INSERT INTO character_board_overrides VALUES (6,'',1)")
        con.execute("INSERT INTO character_board_overrides VALUES (4,'Website Alias',0)")
        con.commit()
        con.close()

    def test_visible_mains_exclude_alts_hidden_and_lfg_only(self):
        records = site.visible_mains(self.path)
        names = [row[0] for row in records]
        self.assertEqual(set(names), {
            "Big Snore", "Clever Priest", "Rogue One", "Mage One",
        })
        self.assertNotIn("Website Alias", names)

    def test_role_groups_have_counts_and_only_three_public_columns(self):
        rows, sections = site.website_rows(site.visible_mains(self.path))
        self.assertEqual(rows[1][0], "4 main characters")
        self.assertEqual(rows[2], list(site.HEADERS))
        self.assertEqual([rows[i][0] for i in sections], [
            "Tanks (1)", "Healers (1)", "Melee (1)", "Ranged (1)",
        ])
        self.assertEqual(len(rows), 11)
        self.assertTrue(all(len(row) == 3 for row in rows))
        self.assertEqual(sum(row[0] == "Big Snore" for row in rows), 1)
        self.assertFalse(any("100" in str(cell) for row in rows for cell in row))

    def test_hide_after_sync_removes_member_from_public_output(self):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO character_board_overrides VALUES (3,'',1)")
        rows, _ = site.website_rows(site.visible_mains(self.path))
        self.assertEqual(rows[1][0], "3 main characters")
        self.assertFalse(any(row[0] == "Rogue One" for row in rows))
        self.assertIn(["Melee (0)", "", ""], rows)

    def test_sync_creates_dedicated_tab_and_writes_raw_values(self):
        calls = []
        def fake_json(session, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET" and "fields" in kwargs.get("params", {}):
                return {"sheets": []}
            if method == "POST" and "batchUpdate" in url and any(
                "addSheet" in req for req in kwargs["json"]["requests"]
            ):
                return {"replies": [{"addSheet": {"properties": {"sheetId": 123}}}]}
            if method == "GET":
                return {"values": []}
            return {}
        from unittest.mock import MagicMock
        session = MagicMock()
        with patch.object(site.raid_sheets, "configured", return_value=True), (
             patch.object(site.raid_sheets, "_session", return_value=session)), (
             patch.object(site.raid_sheets, "_json", side_effect=fake_json)):
            self.assertTrue(site.sync_once(self.path))
        writes = [x for x in calls if x[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2]["params"]["valueInputOption"], "RAW")
        self.assertEqual(writes[0][2]["json"]["values"][1][0], "4 main characters")
        self.assertIn("Hyped Website Roster", writes[0][1])
        self.assertIn("A1:C11", writes[0][1])
        self.assertTrue(session.close.called)

    def test_no_changes_skips_rewrite(self):
        desired, _ = site.website_rows(site.visible_mains(self.path))
        from unittest.mock import MagicMock
        def fake_json(session, method, url, **kwargs):
            if "fields" in kwargs.get("params", {}):
                return {"sheets": [{"properties": {
                    "sheetId": 123, "title": site.TAB}}]}
            if method == "GET":
                return {"values": [row[:1] if not row[1] and not row[2] else row
                                   for row in desired]}
            raise AssertionError("unchanged sheet should not be written")
        with patch.object(site.raid_sheets, "configured", return_value=True), (
             patch.object(site.raid_sheets, "_session", return_value=MagicMock())), (
             patch.object(site.raid_sheets, "_json", side_effect=fake_json)):
            self.assertFalse(site.sync_once(self.path))


if __name__ == "__main__":
    unittest.main()
