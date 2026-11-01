"""Offline guards for extracting character and raid signup read queries.

All records use temporary SQLite files. The original Discord cog continues
to own connection lifecycles, permissions, commands and all write operations.
"""

import inspect
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

from cogs import raid_signup as raid, raid_schedule as schedule
from cogs import lfg_preview
from hyped_ogre.features.characters import queries as character_queries
from hyped_ogre.features.raids import signup_queries


class RaidReadQueriesMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid_signup.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)

    def test_original_cog_signatures_defaults_and_shared_lfg_import(self):
        self.assertIs(lfg_preview.raid_signup, raid)
        self.assertEqual(raid.RaidPrototype.__module__, "cogs.raid_signup")
        self.assertEqual(raid.setup.__module__, "cogs.raid_signup")
        for name, required_count in (
            ("list_characters", 1), ("member_characters", 1),
            ("character_race", 2), ("main_character_ids", 1),
            ("has_wowsims_export", 2), ("signup_note_details", 2),
            ("my_signup", 2), ("my_absence", 2),
        ):
            with self.subTest(name=name):
                func = getattr(raid, name)
                self.assertEqual(len(inspect.signature(func).parameters),
                                 required_count + 1)
                self.assertEqual(func.__defaults__, (raid.RAID_DB,))

    def test_character_order_race_main_and_discord_ownership(self):
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        raid.save_character(42, "Mage Alt", "Mage", "Frost", "DPS", self.path)
        raid.save_character(99, "Other Member", "Rogue", "Combat", "DPS", self.path)
        characters = raid.member_characters(42, self.path)
        self.assertEqual([row[1] for row in characters], ["Snore Mon", "Mage Alt"])
        main, alt = [row[0] for row in characters]
        self.assertEqual(raid.main_character_ids(42, self.path), {main})
        self.assertEqual(raid.list_characters(42, self.path)[0][0], "Snore Mon")
        self.assertEqual(raid.member_characters(99, self.path)[0][1], "Other Member")
        self.assertEqual(raid.member_characters(123, self.path), [])
        self.assertEqual(raid.character_race(123, main, self.path), "")
        raid.set_character_race(42, main, "Orc", self.path)
        self.assertEqual(raid.character_race(42, main, self.path), "Orc")
        self.assertEqual(raid.character_race(99, main, self.path), "")
        raid.set_main_character(42, alt, self.path)
        self.assertEqual(raid.main_character_ids(42, self.path), {alt})
        self.assertEqual(raid.member_characters(42, self.path)[0][0], alt)

        with raid.database(self.path) as db:
            self.assertEqual(
                raid.list_characters(42, self.path),
                character_queries.list_characters(db, 42),
            )
            self.assertEqual(
                raid.member_characters(42, self.path),
                character_queries.member_characters(db, 42),
            )

    def test_only_own_wowsims_json_is_detected_and_queries_are_read_only(self):
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        cid = raid.member_characters(42, self.path)[0][0]
        with raid.database(self.path) as db:
            db.execute(
                "INSERT INTO gear_exports "
                "(character_id,submitted_at,format,raw_text) "
                "VALUES (?,?,?,?)",
                (cid, "2026-11-01T20:00:00+00:00", "simc_raw", "player=warrior"),
            )
        self.assertFalse(raid.has_wowsims_export(42, cid, self.path))
        with raid.database(self.path) as db:
            db.execute(
                "INSERT INTO gear_exports "
                "(character_id,submitted_at,format,raw_text) "
                "VALUES (?,?,?,?)",
                (cid, "2026-11-01T20:01:00+00:00", "wowsims_json", "{}"),
            )
        self.assertTrue(raid.has_wowsims_export(42, cid, self.path))
        self.assertFalse(raid.has_wowsims_export(99, cid, self.path))
        with raid.database(self.path) as db:
            before = db.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0]
            self.assertTrue(character_queries.has_wowsims_export(db, 42, cid))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0],
                             before)

    def test_signup_and_absence_queries_preserve_notes_and_isolation(self):
        event = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00", "Hyjal (20m)",
            wowsims_policy="optional",
        )
        self.assertEqual(event["raid_id"], 1)
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        cid = raid.member_characters(42, self.path)[0][0]
        raid.save_signup(1, 42, cid, self.path, note="Late after work")
        self.assertEqual(raid.signup_note_details(1, 42, self.path),
                         ("Snore Mon", "Available", "Late after work"))
        self.assertEqual(raid.my_signup(1, 42, self.path),
                         ("Snore Mon", "Available"))
        self.assertIsNone(raid.my_signup(1, 43, self.path))
        self.assertIsNone(raid.signup_note_details(1, 43, self.path))
        self.assertIsNone(raid.my_absence(1, 42, self.path))
        stamp = raid.save_absence(1, 42, "Away", self.path)
        self.assertEqual(raid.my_signup(1, 42, self.path),
                         ("Snore Mon", "Unavailable"))
        self.assertEqual(raid.signup_note_details(1, 42, self.path),
                         ("Snore Mon", "Unavailable", "Late after work"))
        self.assertEqual(raid.my_absence(1, 42, self.path), ("Away", stamp))
        self.assertIsNone(raid.my_absence(1, 99, self.path))
        with raid.database(self.path) as db:
            self.assertEqual(signup_queries.my_signup(db, 1, 42),
                             raid.my_signup(1, 42, self.path))
            self.assertEqual(signup_queries.signup_note_details(db, 1, 42),
                             raid.signup_note_details(1, 42, self.path))
            self.assertEqual(signup_queries.my_absence(db, 1, 42),
                             raid.my_absence(1, 42, self.path))

    def test_database_patch_remains_effective_and_missing_schema_is_safe(self):
        with patch.object(raid, "database", wraps=raid.database) as database:
            self.assertEqual(raid.member_characters(999, self.path), [])
            database.assert_called_once_with(self.path)
        unknown = str(Path(self.temp.name) / "missing.db")
        self.assertEqual(raid.main_character_ids(42, unknown), set())
        with closing(sqlite3.connect(unknown)) as db, db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
                .fetchone()[0], 0,
            )


if __name__ == "__main__":
    unittest.main()
