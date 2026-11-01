"""Guard raid signup schema extraction without moving the live Discord cog."""

import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from discord.ext import commands

from cogs import raid_signup as raid
from hyped_ogre.features.raids import signup_reference, signup_schema


class RaidSignupInternalsMigrationTests(unittest.TestCase):
    def test_original_discord_extension_and_group_cog_are_unchanged(self):
        # LFG imports raid_signup before rollf loads it as an extension.
        # Keep its original module so load/reload and app commands stay valid.
        spec = importlib.util.find_spec("cogs.raid_signup")
        self.assertTrue(spec.origin.replace("\\\\", "/").endswith("/cogs/raid_signup.py"))
        self.assertEqual(raid.RaidPrototype.__module__, "cogs.raid_signup")
        self.assertEqual(raid.setup.__module__, "cogs.raid_signup")
        self.assertTrue(issubclass(raid.RaidPrototype, commands.GroupCog))
        self.assertEqual(raid.RaidPrototype.__cog_name__, "RaidPrototype")
        self.assertEqual(raid.RaidPrototype.__cog_app_commands_group__.name, "raid")
        self.assertEqual(raid.RAID_CHANNEL_ID, 1563663151323553802)

    def test_shared_reference_objects_are_imported_without_change(self):
        self.assertIs(raid.SHEET_TABS, signup_reference.SHEET_TABS)
        self.assertIs(raid.ATTENDANCE_OPTIONS, signup_reference.ATTENDANCE_OPTIONS)
        self.assertIs(raid.CLASS_SPECS, signup_reference.CLASS_SPECS)
        self.assertEqual(raid.CLASS_SPECS["Warrior"]["Protection"], ("Tank",))
        self.assertEqual(raid.CLASS_SPECS["Hunter"]["Survival"], ("DPS",))
        self.assertEqual(raid.CLASS_SPECS["Druid"]["Feral"], ("Tank", "DPS"))
        self.assertEqual(
            raid.ATTENDANCE_OPTIONS,
            ("Pending", "Attended", "Late", "AFK", "No-show", "Excused"),
        )
        self.assertEqual(len(raid.SHEET_TABS["Roster Builder"]), 15)

    def test_existing_create_schema_wrapper_calls_canonical_upgrader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "raid_signup.db")
            with patch.object(
                raid, "ensure_signup_schema_on_connection",
                wraps=signup_schema.ensure_signup_schema_on_connection,
            ) as upgrade:
                raid.create_schema(path)
                upgrade.assert_called_once()
            raid.create_schema(path)
            with raid.database(path) as db:
                tables = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertTrue({
                    "characters", "raids", "signups", "raid_absences",
                    "raid_public_roster", "gear_exports", "gear_images",
                    "attendance",
                }.issubset(tables))
                self.assertIn("race", {
                    x[1] for x in db.execute("PRAGMA table_info(characters)")
                })
                self.assertIn("show_on_discord", {
                    x[1] for x in db.execute("PRAGMA table_info(signups)")
                })
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 0
                )

    def test_existing_characters_and_equipment_survive_legacy_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "raid_signup.db")
            raid.create_schema(path)
            with raid.database(path) as db:
                db.execute("DROP INDEX idx_raid_characters_member_name")
                db.execute(
                    "INSERT INTO characters "
                    "(character_id, discord_id, name, realm, class_name, spec, "
                    "role, is_main) VALUES (7,42,'Snore Mon','Old',"
                    "'Warrior','Protection','Tank',1)"
                )
                db.execute(
                    "INSERT INTO characters "
                    "(character_id, discord_id, name, realm, class_name, spec, "
                    "role, is_main) VALUES (8,42,' snore  mon ','Other',"
                    "'Warrior','Protection','Tank',0)"
                )
                db.execute(
                    "INSERT INTO gear_exports "
                    "(character_id,submitted_at,format,raw_text) "
                    "VALUES (8,'2026-11-01T20:00:00+00:00','wowsims_json','{}')"
                )
            raid.create_schema(path)
            raid.create_schema(path)
            with raid.database(path) as db:
                rows = db.execute(
                    "SELECT character_id, name FROM characters WHERE discord_id=42"
                ).fetchall()
                self.assertEqual(rows, [(7, "Snore Mon")])
                self.assertEqual(
                    db.execute("SELECT character_id FROM gear_exports").fetchone(),
                    (7,),
                )
                index = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_raid_characters_member_name'"
                ).fetchone()
                self.assertIsNotNone(index)


if __name__ == "__main__":
    unittest.main()
