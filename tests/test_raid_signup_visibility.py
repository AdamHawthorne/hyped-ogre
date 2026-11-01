"""Visibility controls hide public names at every raid stage, not signups."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cogs import raid_sheets_v3 as sheet, raid_schedule as schedule
from cogs import raid_signup as raid
from wowsims_helper import signup_with_export


class SignupVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 21:00",
                           "Hyjal (20m)", "Barrows (10m)")
        for member_id, name, klass, spec, role in (
            (42, "Snore Mon", "Warrior", "Protection", "Tank"),
            (43, "Fast Rogue", "Rogue", "Combat", "DPS"),
            (44, "Mage Player", "Mage", "Frost", "DPS"),
        ):
            raid.save_character(member_id, name, klass, spec, role, self.path)
            char_id = raid.member_characters(member_id, self.path)[0][0]
            signup_with_export(1, member_id, char_id, self.path)

    def visible(self):
        return [entry[0] for entry in schedule.read_signup_roster(self.path, 1)]

    def test_poster_no_hides_new_signups_until_sheet_sync(self):
        self.assertEqual(self.visible(),
                         ["Snore Mon", "Fast Rogue", "Mage Player"])
        with schedule.database(self.path) as con:
            con.execute(
                "UPDATE raids SET default_show_discord=0 WHERE raid_id=1"
            )
        self.assertEqual(self.visible(), [])
        self.assertEqual(raid.my_signup(1, 42, self.path),
                         ("Snore Mon", "Available"))
        self.assertEqual(len(schedule.read_signup_roster(self.path, 1)), 0)
        # A manual yes should show just that signup. No selection or team
        # assignment is required until leadership publishes the roster.
        event = schedule.read_raid(self.path, 1)
        body = sheet._rows(self.path, event, {})
        target = next(r for r in body if r[16] == "43")
        target[10] = "Yes"
        sheet._cache_signup_visibility(self.path, 1, body)
        self.assertEqual(self.visible(), ["Fast Rogue"])
        with schedule.database(self.path) as con:
            raw = con.execute("SELECT count(*) FROM signups").fetchone()[0]
        self.assertEqual(raw, 3)

    def test_manual_no_hides_existing_players_even_with_poster_yes(self):
        event = schedule.read_raid(self.path, 1)
        body = sheet._rows(self.path, event, {})
        target = next(r for r in body if r[16] == "42")
        target[10] = "No"
        sheet._cache_signup_visibility(self.path, 1, body)
        self.assertEqual(self.visible(), ["Fast Rogue", "Mage Player"])
        # A late signup inherits poster default without having to wait for
        # the next Sheets cycle. Previously hidden names stay hidden.
        raid.save_character(45, "Late Druid", "Druid", "Feral", "DPS",
                            self.path)
        char = raid.member_characters(45, self.path)[0][0]
        signup_with_export(1, 45, char, self.path)
        self.assertEqual(self.visible(),
                         ["Fast Rogue", "Mage Player", "Late Druid"])
        # Changing an instance or selection does not bypass privacy.
        event = schedule.raid_status(
            self.path, 1, "confirm",
            confirmed_entries=[("42", "Snore Mon", "Hyjal"),
                               ("43", "Fast Rogue", "Barrows")],
        )
        self.assertEqual(event["signup_open"], 1)

    def test_real_one_sync_reads_manual_no_and_updates_public_names(self):
        event = schedule.read_raid(self.path, 1)
        existing = sheet._header(
            event, sheet._rows(self.path, event, {}), sheet.tab_title(event)
        )
        first = next(r for r in existing if r[16] == "42")
        first[10] = "No"
        with patch.object(sheet, "_read", return_value=existing), \
             patch.object(sheet, "_write", return_value=0), \
             patch.object(sheet, "_apply_wcl_from_sheet", return_value=None):
            sheet._one(
                Mock(), self.path, event, {},
                {sheet.tab_title(event): {"sheetId": 1}},
            )
        self.assertEqual(self.visible(), ["Fast Rogue", "Mage Player"])
        # Turning the same player back on is reflected after the next sync.
        first[10] = "Yes"
        with patch.object(sheet, "_read", return_value=existing), \
             patch.object(sheet, "_write", return_value=0), \
             patch.object(sheet, "_apply_wcl_from_sheet", return_value=None):
            sheet._one(
                Mock(), self.path, event, {},
                {sheet.tab_title(event): {"sheetId": 1}},
            )
        self.assertEqual(self.visible(),
                         ["Snore Mon", "Fast Rogue", "Mage Player"])

    def test_migration_preserves_existing_registration(self):
        with schedule.database(self.path) as con:
            con.execute("UPDATE signups SET show_on_discord=0 "
                        "WHERE raid_id=1 AND discord_id=42")
        raid.create_schema(self.path)
        with schedule.database(self.path) as con:
            columns = {r[1] for r in con.execute("PRAGMA table_info(signups)")}
            self.assertIn("show_on_discord", columns)
            self.assertEqual(con.execute(
                "SELECT show_on_discord FROM signups "
                "WHERE raid_id=1 AND discord_id=42"
            ).fetchone()[0], 0)
        self.assertEqual(self.visible(), ["Fast Rogue", "Mage Player"])


if __name__ == "__main__":
    unittest.main()
