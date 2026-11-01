"""Offline regression guards for moving the private raid-sheet dashboard.

Never connect to Google or Discord. All sheet responses are in-memory mocks,
and any database work must use freshly created temporary paths.
"""

import importlib
from unittest.mock import patch
import unittest

from cogs import raid_sheets_v3 as legacy
from cogs import raid_signup
from cogs import raid_sheets
from hyped_ogre.features.raids import sheets_dashboard as canonical


class RaidSheetDashboardMigrationTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "raid_id": 7, "raid_at": "2026-12-09T21:00:00+00:00",
            "instance_1": "Hyjal (20m)", "instance_2": "Barrows (10m)",
            "instance_3": None, "custom_title": "Guild raid",
            "wcl_url": "https://www.warcraftlogs.com/reports/testreport",
            "archive_notes": "Bring consumables",
            "roster_published": 0, "archived_at": None,
        }

    def test_legacy_and_new_imports_share_exact_mutable_module(self):
        self.assertIs(legacy, canonical)
        self.assertIs(importlib.import_module("cogs.raid_sheets_v3"), canonical)
        self.assertIs(raid_signup.raid_sheets_v3, canonical)
        self.assertIs(canonical.raid_sheets, raid_sheets)
        for name in (
            "COLUMNS", "SECTIONS", "VALUES", "DISPLAY_ORDER",
            "_FORMATTED_SECTION_LAYOUTS", "_GEAR_TAB_SNAPSHOT",
            "tab_title", "_read", "_rows", "_approved", "_header",
            "_write", "_one", "read_selected", "sync_once",
            "delete_raid_tab", "attendance_candidates",
            "attendance_set", "attendance_complete",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_fixed_dashboard_cells_keep_g2_to_h5_and_metadata(self):
        body = []
        rows = canonical._header(
            self.event, body, canonical.tab_title(self.event),
        )
        self.assertEqual(len(canonical.COLUMNS), 20)
        self.assertEqual(len(rows[0]), 20)
        self.assertEqual(rows[0][0], "<Hyped> Raid #7 - Guild raid")
        self.assertEqual(rows[3][0], "Warcraft Logs")
        self.assertEqual(rows[3][1], self.event["wcl_url"])
        self.assertEqual(rows[3][2], "Raid Notes")
        self.assertEqual(rows[3][3], self.event["archive_notes"])
        self.assertEqual(rows[1][6], "Barrows 🔸 (9)")
        self.assertEqual(rows[2][6], "Barrows 🔹 (9)")
        self.assertEqual(rows[3][6], "Hyjal (9)")
        self.assertEqual(rows[4][6], "")
        self.assertEqual(rows[1][7], "No team selected")
        self.assertEqual(rows[4][0], "0 signed · 0 absent")
        self.assertEqual(rows[6], list(canonical.COLUMNS))

    def test_older_physical_column_layout_is_read_using_labels(self):
        headers = list(reversed(canonical.COLUMNS))
        headers[headers.index("Show")] = "Show on Discord"
        player = {
            "Discord Name": "Ogre", "Character": "Snore",
            "Class": "Warrior", "Role": "Tank", "Selection": "Bench",
            "Show": "No", "Barrows": "🔹",
            "Leadership Notes": "Preserve this choice",
            "Discord ID (internal)": "42",
        }
        displayed_player = [
            player.get("Show" if name == "Show on Discord" else name, "")
            for name in headers
        ]
        top = [[""] * 20 for _ in range(6)]
        top[1][6], top[1][7] = "Barrows 🔸 (9)", "No team selected"
        top[3][1], top[3][3] = "Custom Logs", "Notes from leader"
        with patch.object(
            canonical.raid_sheets, "_json",
            return_value={"values": top + [headers, displayed_player]},
        ) as api:
            read = legacy._read(object(), "Raid #7 - Wed 09 Dec 2026")
        api.assert_called_once()
        self.assertEqual(read[1][6:8], top[1][6:8])
        self.assertEqual(read[3][1], "Custom Logs")
        self.assertEqual(read[3][3], "Notes from leader")
        self.assertEqual(read[6], list(canonical.COLUMNS))
        self.assertEqual(read[7][1], "Snore")
        self.assertEqual(read[7][9], "Bench")
        self.assertEqual(read[7][10], "No")
        self.assertEqual(read[7][12], "🔹")
        self.assertEqual(read[7][14], "Preserve this choice")
        self.assertEqual(read[7][16], "42")

    def test_approved_players_exclude_hidden_absent_benched_or_unassigned(self):
        base = [""] * len(canonical.COLUMNS)
        base[1:5] = ["Snore", "Warrior", "Protection", "Tank"]
        base[8:14] = ["Signed", "Selected", "Yes", "Yes", "🔸", "No"]
        base[16] = "42"
        hidden = base.copy()
        hidden[1], hidden[16], hidden[10] = "Secret", "43", "No"
        benched = base.copy()
        benched[1], benched[16], benched[9] = "Bench", "44", "Bench"
        absent = base.copy()
        absent[1], absent[16], absent[8] = "Absent", "45", "Absent"
        not_assigned = base.copy()
        not_assigned[1], not_assigned[16] = "Unassigned", "46"
        not_assigned[11:14] = ["No", "No", "No"]
        header = [[""] * len(canonical.COLUMNS) for _ in range(7)]
        actual = canonical._approved(
            header + [base, hidden, benched, absent, not_assigned],
            self.event, include_details=True,
        )
        self.assertEqual(actual, [
            ("42", "Snore", "Hyjal, Barrows 🔸",
             "Warrior", "Tank", "Protection"),
        ])

    def test_writes_keep_metadata_in_fixed_cells_and_players_reordered(self):
        body = [[""] * len(canonical.COLUMNS) for _ in range(1)]
        body[0][0:5] = ["Ogre", "Snore", "Warrior", "Protection", "Tank"]
        body[0][8:14] = ["Signed", "Selected", "Yes", "Yes", "🔸", "No"]
        body[0][16] = "42"
        rows = canonical._header(self.event, body, canonical.tab_title(self.event))
        with patch.object(canonical.raid_sheets, "_json", return_value={}) as request, \
             patch.object(legacy, "_paint_sections") as paint:
            canonical._FORMATTED_SECTION_LAYOUTS.pop(7001, None)
            canonical._write(object(), canonical.tab_title(self.event), 7001, [], rows)
        request.assert_called_once()
        paint.assert_called_once()
        physical = request.call_args.kwargs["json"]["values"]
        self.assertEqual(physical[1][6], "Barrows 🔸 (9)")
        self.assertEqual(physical[3][1], self.event["wcl_url"])
        self.assertEqual(physical[3][3], "Bring consumables")
        self.assertEqual(physical[7][1], "Snore")
        self.assertEqual(physical[7][6], "Selected")
        self.assertEqual(physical[7][9], "No")

    def test_unconfigured_sync_never_requests_google_or_reads_database(self):
        with patch.object(canonical.raid_sheets, "configured", return_value=False), \
             patch.object(canonical.raid_sheets, "_session") as connect, \
             patch.object(canonical.raid_sheets, "_raid_snapshot") as snapshot:
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                legacy.sync_once("should-not-open.db")
            connect.assert_not_called()
            snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
