"""Character registration and profile mutation extraction regression tests.

Only temporary SQLite databases are created. Discord interactions and Fly
production volumes are not used.
"""

from concurrent.futures import ThreadPoolExecutor
import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cogs import raid_signup as raid, raid_schedule as schedule
from hyped_ogre.features.characters import mutations


class CharacterMutationsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid_signup.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)

    def test_original_extension_and_api_signatures_are_still_available(self):
        with patch.dict("os.environ", {"HYPED_GUILD_ID": "77"}):
            from cogs import lfg_preview
        self.assertIs(lfg_preview.raid_signup, raid)
        self.assertEqual(raid.RaidPrototype.__module__, "cogs.raid_signup")
        self.assertEqual(raid.setup.__module__, "cogs.raid_signup")
        for func, count in (
            (raid.save_character, 6),
            (raid.set_character_race, 4),
            (raid.set_main_character, 3),
            (raid.update_character_profile, 6),
        ):
            with self.subTest(name=func.__name__):
                self.assertEqual(len(inspect.signature(func).parameters), count)
                self.assertEqual(func.__defaults__, (raid.RAID_DB,))

    def test_register_normalises_name_and_reuses_id_without_dropping_gear(self):
        self.assertEqual(raid.save_character(
            42, "  Snore    Mon ", "Warrior", "Protection", "Tank", self.path
        ), "created")
        first = raid.member_characters(42, self.path)[0][0]
        with raid.database(self.path) as db:
            db.execute(
                "INSERT INTO gear_exports "
                "(character_id, submitted_at, format, raw_text) "
                "VALUES (?, ?, 'wowsims_json', '{}')",
                (first, "2026-11-01T20:00:00+00:00"),
            )
        self.assertEqual(raid.save_character(
            42, "snore mon", "Warrior", "Fury", "DPS", self.path
        ), "updated")
        with raid.database(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT character_id, name, spec FROM characters "
                "WHERE discord_id=42"
            ).fetchall(), [(first, "Snore Mon", "Fury")])
            self.assertEqual(
                db.execute("SELECT character_id FROM gear_exports").fetchall(),
                [(first,)],
            )

    def test_registration_lock_serialises_duplicate_clicks(self):
        def register(_):
            return raid.save_character(
                42, "Concurrent Ogre", "Warrior", "Protection", "Tank",
                self.path,
            )
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(register, range(4)))
        self.assertEqual(results.count("created"), 1)
        self.assertEqual(results.count("updated"), 3)
        self.assertEqual(len(raid.member_characters(42, self.path)), 1)

    def test_invalid_and_foreign_changes_do_not_affect_saved_profiles(self):
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        character_id = raid.member_characters(42, self.path)[0][0]
        with self.assertRaisesRegex(ValueError, "valid class"):
            raid.save_character(42, "Invalid", "Mage", "Frost", "Tank", self.path)
        with self.assertRaisesRegex(ValueError, "40 characters"):
            raid.save_character(42, "X" * 41, "Mage", "Frost", "DPS", self.path)
        with self.assertRaisesRegex(ValueError, "isn't yours"):
            raid.set_character_race(99, character_id, "Orc", self.path)
        with self.assertRaisesRegex(ValueError, "cannot play"):
            raid.set_character_race(42, character_id, "Tauren", self.path)
        with self.assertRaisesRegex(ValueError, "someone else's"):
            raid.set_main_character(99, character_id, self.path)
        with self.assertRaisesRegex(ValueError, "someone else's"):
            raid.update_character_profile(
                99, character_id, "Mage", "Frost", "DPS", self.path,
            )
        with raid.database(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT name, class_name, spec, role, race, is_main "
                "FROM characters WHERE character_id=?", (character_id,),
            ).fetchone(), ("Snore Mon", "Warrior", "Protection", "Tank", "", 1))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM characters"
            ).fetchone()[0], 1)

    def test_profile_reselects_compatible_race_and_retains_raid_and_gear(self):
        schedule.save_raid(self.path, 1, "2026-12-09 21:00",
                           "Hyjal (20m)", wowsims_policy="optional")
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        cid = raid.member_characters(42, self.path)[0][0]
        raid.set_character_race(42, cid, "Orc", self.path)
        with raid.database(self.path) as db:
            db.execute(
                "INSERT INTO gear_exports "
                "(character_id, submitted_at, format, raw_text) "
                "VALUES (?, ?, 'wowsims_json', '{}')",
                (cid, "2026-11-01T20:00:00+00:00"),
            )
        raid.save_signup(1, 42, cid, self.path)
        self.assertEqual(raid.update_character_profile(
            42, cid, "Warrior", "Fury", "DPS", self.path
        ), "Snore Mon")
        self.assertEqual(raid.character_race(42, cid, self.path), "Orc")
        self.assertEqual(raid.update_character_profile(
            42, cid, "Mage", "Frost", "DPS", self.path
        ), "Snore Mon")
        self.assertEqual(raid.character_race(42, cid, self.path), "")
        with raid.database(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT character_id FROM gear_exports"
            ).fetchall(), [(cid,)])
            self.assertEqual(db.execute(
                "SELECT character_id FROM signups"
            ).fetchall(), [(cid,)])
        raid.set_character_race(42, cid, "Undead", self.path)
        self.assertEqual(raid.character_race(42, cid, self.path), "Undead")

    def test_main_switch_is_owner_specific_and_uses_existing_connection(self):
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank", self.path)
        raid.save_character(42, "Mage Alt", "Mage", "Frost", "DPS", self.path)
        raid.save_character(99, "Other", "Mage", "Frost", "DPS", self.path)
        other_main = next(x[0] for x in raid.member_characters(99, self.path))
        alt = next(x[0] for x in raid.member_characters(42, self.path)
                   if x[1] == "Mage Alt")
        with patch.object(raid, "database", wraps=raid.database) as open_db:
            with patch.object(mutations, "set_main_character",
                              wraps=mutations.set_main_character) as action:
                self.assertIsNone(raid.set_main_character(42, alt, self.path))
                action.assert_called_once()
                open_db.assert_called_once_with(self.path)
        self.assertEqual(raid.main_character_ids(42, self.path), {alt})
        self.assertEqual(raid.main_character_ids(99, self.path), {other_main})

    def test_own_database_and_cross_feature_boundary_remain_unchanged(self):
        # Deletion still belongs to the original cog: it alone guards
        # cross-database LFG requests and existing raid history.
        self.assertEqual(raid.delete_character.__module__, "cogs.raid_signup")
        with self.assertRaises(sqlite3.OperationalError):
            with raid.database(self.path) as db:
                mutations.register_character(
                    db, 77, "A", "Warrior", "Protection", "Tank"
                )
                raise sqlite3.OperationalError("rollback")
        self.assertEqual(raid.member_characters(77, self.path), [])


if __name__ == "__main__":
    unittest.main()
