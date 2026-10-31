"""Regression tests: optional pings do not lock the live raid roster."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import discord

from cogs import raid_acknowledgements as ack
from cogs import raid_schedule as schedule
from cogs import raid_signup as raid
from cogs import raid_sheets_v3 as sheet


class RaidAcknowledgementTests(unittest.TestCase):
    def setup_raid(self, path):
        raid.create_schema(path)
        schedule.ensure_raid_fields(path)
        event = schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
        raid.save_character(42, "Snore", "Warrior", "Protection", "Tank", path)
        character_id = raid.member_characters(42, path)[0][0]
        raid.save_signup(1, 42, character_id, path)
        return event

    def test_publishing_does_not_lock_signups_or_poster_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            event = schedule.raid_status(
                path, 1, "confirm",
                confirmed_entries=[("42", "Snore", "Hyjal", "Warrior", "Tank", "Protection")]
            )
            self.assertEqual(event["signup_open"], 1)
            self.assertEqual(event["roster_published"], 1)
            raid.save_character(43, "New Mage", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(43, path)[0][0]
            raid.save_signup(1, 43, char_id, path)
            self.assertIn(("New Mage", "", "Mage", "DPS", "Frost"),
                          schedule.read_signup_roster(path, 1))
            changed = schedule.save_raid(path, 1, "2026-12-09 21:00",
                                         "Hyjal (20m)", "Barrows (10m)")
            self.assertEqual(changed["roster_published"], 1)
            self.assertEqual(changed["signup_open"], 1)
            view = schedule.RaidEventPanel(
                changed, on_signup=lambda _: None
            )
            attend = next(x for x in view.walk_children()
                          if isinstance(x, discord.ui.Button) and x.label == "Attend")
            self.assertFalse(attend.disabled)

    def test_acknowledgement_only_removes_name_from_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            schedule.raid_status(path, 1, "confirm",
                                 confirmed_entries=[("42", "Snore", "Hyjal")])
            ack.record_announcement(path, 1, 123, 456,
                                    [("42", "Snore"), ("43", "Mage")])
            self.assertEqual(ack.pending(path, 1),
                             [("42", "Snore"), ("43", "Mage")])
            msg = ack.render(1, ack.pending(path, 1), 2)
            self.assertIn("<@42>", msg)
            self.assertIn("<@43>", msg)
            self.assertIsNone(ack.acknowledge(path, 123, 456, 777))
            self.assertIsNone(ack.acknowledge(path, 123, 999, 42))
            self.assertEqual(ack.acknowledge(path, 123, 456, 42), 1)
            self.assertIsNone(ack.acknowledge(path, 123, 456, 42))
            self.assertEqual(ack.dirty_announcements(path), [1])
            msg = ack.render(1, ack.pending(path, 1), 2)
            self.assertNotIn("<@42>", msg)
            self.assertIn("<@43>", msg)
            ack.mark_refreshed(path, 1)
            self.assertEqual(ack.dirty_announcements(path), [])
            self.assertEqual(schedule.read_raid(path, 1)["signup_open"], 1)
            self.assertEqual(len(schedule.confirmed_roster(path, 1)), 1)
            self.assertEqual(ack.acknowledge(path, 123, 456, 43), 1)
            msg = ack.render(1, ack.pending(path, 1), 2)
            self.assertIn("All 2 acknowledged", msg)
            self.assertNotIn("<@43>", msg)
            with self.assertRaisesRegex(ValueError, "already announced"):
                ack.record_announcement(path, 1, 123, 457, [("42", "Snore")])

    def test_archiving_closes_signups_but_acknowledgement_is_still_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            schedule.raid_status(path, 1, "confirm",
                                 confirmed_entries=[("42", "Snore", "Hyjal")])
            ack.record_announcement(path, 1, 123, 456, [("42", "Snore")])
            archived = schedule.raid_status(path, 1, "archive")
            self.assertEqual(archived["signup_open"], 0)
            self.assertEqual(ack.acknowledge(path, 123, 456, 42), 1)
            self.assertEqual(ack.pending(path, 1), [])

    def test_legacy_confirmed_raid_migrates_to_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.setup_raid(path)
            with schedule.database(path) as con:
                con.execute("UPDATE raids SET roster_published=1, signup_open=0 "
                            "WHERE raid_id=1")
            schedule.ensure_raid_fields(path)
            self.assertEqual(schedule.read_raid(path, 1)["signup_open"], 1)

    def test_published_roster_follows_new_sheet_selections(self):
        """Published teams update without reopening or repinging any members."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.setup_raid(path)
            schedule.record_panel(path, 1, 333, 444)
            schedule.raid_status(path, 1, "confirm",
                                 confirmed_entries=[("42", "Snore", "Hyjal")])
            raid.save_character(43, "New Mage", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(43, path)[0][0]
            raid.save_signup(1, 43, char_id, path)
            event = schedule.read_raid(path, 1)
            body = sheet._rows(path, event, {})
            names = {r[1]: r for r in body if r[16]}
            names["Snore"][9:14] = ["Selected", "Yes", "Yes", "No", "No"]
            names["New Mage"][9:14] = ["Selected", "Yes", "Yes", "No", "No"]
            rows = sheet._header(event, body, sheet.tab_title(event))
            session = MagicMock()
            with patch.object(sheet.raid_sheets, "configured", return_value=True), \
                 patch.object(sheet.raid_sheets, "_session", return_value=session), \
                 patch.object(sheet.raid_sheets, "_raid_snapshot",
                              return_value=([], {1: event}, [], [], [])), \
                 patch.object(sheet, "_list_tabs",
                              return_value={sheet.tab_title(event): {"sheetId": 1}}), \
                 patch.object(sheet, "_one", return_value=(0, rows)) as sync_tab, \
                 patch.object(sheet, "_sync_permanent"), \
                 patch.object(sheet, "_hide_legacy"):
                _, changed = sheet.sync_once(path)
                self.assertEqual(changed, {1})
                self.assertEqual([r[0] for r in
                                  schedule.read_public_roster(path, 1)],
                                 ["Snore", "New Mage"])
                next(row for row in rows if row[1] == "Snore")[9] = "Not selected"
                sync_tab.return_value = (0, rows)
                _, changed = sheet.sync_once(path)
                self.assertEqual(changed, {1})
                self.assertEqual(schedule.read_public_roster(path, 1),
                                 [("New Mage", "Hyjal")])
            self.assertEqual(schedule.read_raid(path, 1)["signup_open"], 1)
