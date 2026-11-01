"""Offline safeguards for moving the optional Google Sheets integration.

No credentials, Google requests, Discord token or production data are used.
"""

import importlib
from unittest.mock import patch
import unittest

from cogs import raid_sheets as legacy
from cogs import raid_sheets_v3 as dashboard
from cogs import raid_signup as signup
from hyped_ogre.integrations import sheets as canonical


class RaidSheetsBridgeMigrationTests(unittest.TestCase):
    def test_old_import_is_same_canonical_module(self):
        self.assertIs(legacy, canonical)
        self.assertIs(importlib.import_module("cogs.raid_sheets"), canonical)
        self.assertIs(dashboard.raid_sheets, canonical)
        self.assertIs(signup.raid_sheets, canonical)
        for name in (
            "SPREADSHEET_ID", "API", "SHEETS", "READ_RANGES",
            "configured", "_session", "_json", "_read_tabs", "_raid_snapshot",
            "character_discord_ids", "_desired_rows", "_sync_rows",
            "_public_rows", "_save_public", "sync_once",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_missing_credentials_refuse_to_connect_or_touch_database(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(legacy, "_session") as connect, \
             patch.object(legacy, "_raid_snapshot") as database:
            self.assertFalse(canonical.configured())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                legacy.sync_once("must-not-open.db")
            connect.assert_not_called()
            database.assert_not_called()

    def test_legacy_patches_affect_shared_google_request_helper(self):
        with patch.object(legacy, "_json", return_value={"valueRanges": []}) as request:
            self.assertIs(dashboard.raid_sheets._json, request)
            with self.assertRaisesRegex(RuntimeError, "every RM tab"):
                canonical._read_tabs(object())
            request.assert_called_once()
            args = request.call_args.args
            self.assertEqual(args[1], "GET")
            self.assertIn("values:batchGet", args[2])

    def test_manual_roster_columns_and_pugs_remain_untouched(self):
        sheet_rows = [
            ["Raid ID", "Raid Date", "Discord ID", "Character ID",
             "Character", "Class", "Spec", "Assigned Role", "Selection",
             "Bench Notes", "Show on Discord"],
            ["1", "2026-12-09", "42", "7", "Old name", "Mage",
             "Fire", "DPS", "Selected", "Keep leadership note", "Yes"],
            ["1", "2026-12-09", "", "", "Pug Hero",
             "Warrior", "Fury", "DPS", "Selected", "", "Yes"],
        ]
        wanted = {
            "1:42": ["1", "2026-12-09", "42", "7",
                     "New name", "Mage", "Frost", "DPS"],
        }
        with patch.object(legacy, "_json", return_value={}) as request:
            changed = canonical._sync_rows(object(), "roster", sheet_rows, wanted)
        self.assertEqual(changed, 1)
        request.assert_called_once()
        _, method, _, = request.call_args.args
        self.assertEqual(method, "POST")
        updates = request.call_args.kwargs["json"]["data"]
        self.assertEqual(updates[0]["range"], "'RM - Roster Builder'!A2:H2")
        self.assertEqual(len(updates[0]["values"][0]), 8)
        self.assertNotIn("Keep leadership note", str(updates))
        self.assertNotIn("Pug Hero", str(updates))
        self.assertEqual(sheet_rows[1][9:], ["Keep leadership note", "Yes"])

    def test_hidden_benched_and_stale_rows_never_leak_publicly(self):
        header = ["Header"] * 15
        approved = [
            "1", "2026-12-09", "42", "7", "Snore", "Warrior",
            "Protection", "Tank", "Selected", "", "Yes",
            "", "Selected", "🔸", "Core",
        ]
        hidden = [*approved]
        hidden[4], hidden[10] = "Hidden person", "No"
        benched = [*approved]
        benched[4], benched[8] = "Benched", "Bench"
        pug = [*approved]
        pug[2], pug[3], pug[4], pug[14] = "", "", "Pug Hero", "PUG"
        public = canonical._public_rows(
            [header, approved, hidden, benched, pug],
            raid_dates={1: "2026-12-09"},
        )
        self.assertEqual(public[1], [
            ("Snore", "Hyjal, Barrows 🔸"),
            ("Pug Hero", "Hyjal, Barrows 🔸"),
        ])
        self.assertNotIn("Hidden person", str(public))
        self.assertNotIn("Benched", str(public))
        self.assertEqual(canonical._public_rows(
            [header, approved], raid_dates={1: "2026-12-10"},
        ), {})
        self.assertEqual(canonical._public_rows(
            [header, approved], unavailable={"1:42"},
        ), {})


if __name__ == "__main__":
    unittest.main()
