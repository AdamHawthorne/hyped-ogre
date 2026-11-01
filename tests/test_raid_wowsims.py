"""WoWSims export requirement and paste-only raid registration."""
import json
import tempfile
import unittest
from pathlib import Path

import discord

from cogs import raid_schedule, raid_signup as raid
from wowsims_helper import example_export


class WoWSimsTests(unittest.TestCase):
    def test_wowsims_required_for_first_signup_but_reused_for_later_raids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid_schedule.save_raid(path, 2, "2026-12-16 21:00", "Hyjal (20m)")
            raid.save_character(42, "Warriorname", "Warrior", "Protection", "Tank", path)
            char_id = raid.member_characters(42, path)[0][0]
            with self.assertRaisesRegex(ValueError, "WoWSims export first"):
                raid.save_signup(1, 42, char_id, path)
            self.assertFalse(raid.has_wowsims_export(42, char_id, path))
            raw = example_export("Warriorname", "warrior")
            raid.save_wowsims_export(42, char_id, raw, path)
            self.assertTrue(raid.has_wowsims_export(42, char_id, path))
            raid.save_signup(1, 42, char_id, path)
            raid.save_signup(2, 42, char_id, path)
            self.assertEqual(raid.my_signup(2, 42, path),
                             ("Warriorname", "Available"))
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT format FROM gear_exports WHERE character_id=?",
                    (char_id,)).fetchone()[0], "wowsims_json")
            # A newer export replaces the effective snapshot without another signup.
            raid.save_wowsims_export(42, char_id, raw, path)
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM gear_exports WHERE character_id=?",
                    (char_id,)).fetchone()[0], 2)

    def test_wowsims_rejects_wrong_character_legacy_simc_and_random_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(42, "Horrible", "Rogue", "Combat", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            for raw in ("player=Horrible\nlevel=60\n" * 5,
                        '{"random":"stuff"}', "not valid json"):
                with self.assertRaises(ValueError):
                    raid.save_wowsims_export(42, char_id, raw, path)
            with self.assertRaisesRegex(ValueError, "correct character"):
                raid.save_wowsims_export(42, char_id,
                                        example_export("Other", "rogue"), path)
            with self.assertRaises(ValueError):
                raid.save_wowsims_export(7, char_id,
                                        example_export("Horrible", "rogue"), path)
            self.assertFalse(raid.has_wowsims_export(42, char_id, path))

    def test_panel_and_paste_instructions_and_no_text_file_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)",
                roster_announce="2026-12-08 21:00",
            )
            panel = raid_schedule.RaidEventPanel(event)
            buttons = [item.label for item in panel.walk_children()
                       if isinstance(item, discord.ui.Button)]
            self.assertIn("WoWSims", buttons)
            self.assertNotIn("SimC", buttons)
            modal = raid.WoWSimsExportModal(42, 123, "Horrible", raid_id=1)
            self.assertEqual(modal.export_text.max_length, 4000)
            self.assertTrue(modal.export_text.required)
            self.assertFalse(any(isinstance(c, discord.ui.FileUpload)
                                 for c in modal.children))

    def test_existing_legacy_signup_keeps_spot_and_can_update_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid.save_character(42, "Oldsign", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO signups (raid_id,discord_id,character_id,availability,"
                    "note,submitted_at) VALUES (1,42,?,'Available','Old','2026-10-31T12:00:00+00:00')",
                    (char_id,),
                )
            raid.save_signup(1, 42, char_id, path, note="New note")
            self.assertEqual(raid.signup_note_details(1, 42, path)[2], "New note")
            self.assertFalse(raid.has_wowsims_export(42, char_id, path))
            raid.save_character(42, "Newalt", "Rogue", "Combat", "DPS", path)
            other = next(row[0] for row in raid.member_characters(42, path)
                         if row[1] == "Newalt")
            with self.assertRaisesRegex(ValueError, "WoWSims export first"):
                raid.save_signup(1, 42, other, path)
