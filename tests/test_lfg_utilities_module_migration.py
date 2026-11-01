"""LFG module migration: preserve previews, dungeon data and SQLite writes."""

import importlib
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cogs import lfg_demo as legacy_demo
from cogs import lfg_dungeons as legacy_dungeons
from cogs.lfg_sort import listing_board_key
from hyped_ogre.features.lfg import demo, dungeons


class LFGUtilitiesMigrationTests(unittest.TestCase):
    def test_original_import_paths_resolve_to_the_same_modules(self):
        self.assertIs(legacy_demo, demo)
        self.assertIs(legacy_dungeons, dungeons)
        self.assertIs(importlib.import_module("cogs.lfg_demo"), demo)
        self.assertIs(importlib.import_module("cogs.lfg_dungeons"), dungeons)
        for name in ("demo_entries", "display_race", "display_uk_times", "DEMO_PLAYERS"):
            self.assertIs(getattr(legacy_demo, name), getattr(demo, name))
        for name in ("DUNGEONS", "DUNGEON_SET", "DUNGEON_LEVELS",
                     "DUNGEON_SELECT_GROUPS", "validate_dungeons",
                     "normalise_wanted_items", "init_requests",
                     "get_request", "list_requests", "save_request",
                     "delete_request"):
            self.assertIs(getattr(legacy_dungeons, name), getattr(dungeons, name))

    def test_preview_is_synthetic_with_existing_times_and_sort(self):
        rows = demo.demo_entries()
        self.assertEqual(rows, legacy_demo.demo_entries())
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows, sorted(rows, key=listing_board_key))
        self.assertEqual(rows[0]["character"], "Grim Ironjaw")
        self.assertEqual(demo.display_race("Skyborne (Windshaper)"), "Skyborne")
        self.assertEqual(demo.display_uk_times("01:00 UK - 13:00 UK"),
                         "1am UK - 1pm UK")
        self.assertTrue(all(r["channel_id"] == 0 for r in rows))

    def test_dungeon_choices_and_validation_remain_unchanged(self):
        self.assertEqual(len(dungeons.DUNGEONS), 35)
        self.assertEqual(len(dungeons.DUNGEON_SET), 35)
        self.assertEqual(len(dungeons.DUNGEON_LEVELS), 35)
        self.assertEqual([len(x) for x in dungeons.DUNGEON_SELECT_GROUPS], [18, 17])
        self.assertEqual(dungeons.validate_dungeons([
            "Blackrock Depths", "Ragefire Chasm", "Blackrock Depths",
        ]), ("Ragefire Chasm", "Blackrock Depths"))
        for value in ([], ["Warsong Gulch"], ["Unknown dungeon"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dungeons.validate_dungeons(value)
        self.assertEqual(dungeons.normalise_wanted_items("  Hand  of Justice "),
                         "Hand of Justice")

    def test_existing_dungeon_requests_upgrade_without_losing_launch_plans(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "lfg_board.db"
            with closing(sqlite3.connect(db_path)) as db, db:
                db.row_factory = sqlite3.Row
                db.executescript("""
                    CREATE TABLE lfg_dungeon_requests (
                        channel_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        character_id INTEGER NOT NULL,
                        dungeons TEXT NOT NULL,
                        item_ids TEXT NOT NULL DEFAULT '',
                        availability TEXT NOT NULL,
                        notes TEXT NOT NULL DEFAULT '',
                        avatar_url TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(channel_id, user_id)
                    );
                    CREATE TABLE lfg_listings (
                        channel_id INTEGER, user_id INTEGER, notes TEXT
                    );
                    INSERT INTO lfg_dungeon_requests
                        (channel_id,user_id,character_id,dungeons,item_ids,availability)
                    VALUES (99,42,123,'Blackrock Depths','11815,12590','Evenings');
                    INSERT INTO lfg_listings VALUES (99,42,'Keep launch plan');
                """)
                dungeons.init_requests(db)
                dungeons.init_requests(db)
                current = dungeons.get_request(db, 99, 42)
                self.assertEqual(current["character_id"], 123)
                self.assertEqual(current["wanted_items"], "11815, 12590")
                self.assertEqual(db.execute(
                    "SELECT notes FROM lfg_listings WHERE user_id=42"
                ).fetchone()["notes"], "Keep launch plan")
                dungeons.save_request(
                    db, 99, 42, 123, ("Ragefire Chasm",),
                    "A better sword", "After 20:00 UK",
                )
                updated = dungeons.get_request(db, 99, 42)
                self.assertEqual(updated["character_id"], 123)
                self.assertEqual(updated["dungeons"], "Ragefire Chasm")
                self.assertEqual(updated["wanted_items"], "A better sword")
                self.assertEqual(len(dungeons.list_requests(db, 99)), 1)
                self.assertEqual(dungeons.delete_request(db, 99, 42), 1)
                self.assertIsNone(dungeons.get_request(db, 99, 42))
                self.assertEqual(db.execute(
                    "SELECT notes FROM lfg_listings WHERE user_id=42"
                ).fetchone()["notes"], "Keep launch plan")


if __name__ == "__main__":
    unittest.main()
