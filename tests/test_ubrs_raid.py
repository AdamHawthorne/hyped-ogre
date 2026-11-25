"""UBRS is a first-class 10-player raid with independent Sheets assignments."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import raid_poster, raid_schedule as schedule, raid_signup as raid
from cogs import raid_sheets_v3 as sheet
from cogs import forever_raid_buffs as buffs


class UBRSRaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.event = schedule.save_raid(
            self.path, 1, "2026-12-09 20:00", "UBRS (10m)",
            wowsims_policy="optional",
        )
        raid.save_character(42, "Big Snore", "Warrior", "Protection", "Tank", self.path)
        character = raid.member_characters(42, self.path)[0][0]
        raid.save_signup(1, 42, character, self.path)

    def test_ubrs_is_a_ten_player_instance_option_and_poster_header(self):
        self.assertIn("UBRS (10m)", raid_poster.INSTANCES)
        self.assertIn("UBRS (10m)", schedule.raid_panel_intro(self.event))
        self.assertEqual(
            raid_poster.roster_preview_pages(
                self.event, [("42", "Big Snore", "UBRS", "Warrior", "Tank", "Protection")]
            )[0].count("**UBRS**"), 1
        )

    def test_ubrs_sheet_assignment_is_independent_of_existing_instances(self):
        rows = sheet._header(
            self.event, sheet._rows(self.path, self.event, {42: "Snore"}),
            sheet.tab_title(self.event),
        )
        selected = next(row for row in rows if row[16] == "42")
        self.assertEqual(len(selected), 21)
        self.assertEqual(selected[20], "No")
        self.assertEqual(sheet._approved(rows, self.event), [])
        selected[9] = "Selected"
        selected[20] = "Yes"
        self.assertEqual(
            sheet._approved(rows, self.event),
            [("42", "Big Snore", "UBRS")],
        )
        preserved = sheet._rows(self.path, self.event, {}, old_rows=rows)
        self.assertEqual(next(row for row in preserved if row[16] == "42")[20], "Yes")

    def test_confirmed_roster_has_one_ubrs_ten_player_group(self):
        groups = schedule.roster_groups(
            self.event, [("Big Snore", "UBRS", "Warrior", "Tank", "Protection")],
        )
        self.assertEqual([heading for heading, _ in groups], ["UBRS · 10-player"])
        self.assertEqual(len(groups[0][1]), 1)
        self.assertIn("Big Snore", groups[0][1][0])
        confirmed = schedule.raid_panel_text(
            {**self.event, "roster_published": 1},
            [("Big Snore", "UBRS", "Warrior", "Tank", "Protection")],
        )
        self.assertIn("**UBRS (1)**", confirmed)
        self.assertIn("**Tanks (1)**", confirmed)
        self.assertNotIn("Barrows (", confirmed)

    def test_ubrs_and_barrows_can_be_scheduled_together_without_overlap(self):
        event = schedule.save_raid(
            self.path, 1, "2026-12-09 20:00", "Barrows (10m)",
            "Hyjal (20m)", "UBRS (10m)", wowsims_policy="optional",
        )
        rows = sheet._header(
            event, sheet._rows(self.path, event, {}), sheet.tab_title(event),
        )
        player = next(row for row in rows if row[16] == "42")
        player[9] = "Selected"
        player[11], player[12], player[20] = "Yes", "🔸", "Yes"
        self.assertEqual(sheet._approved(rows, event), [
            ("42", "Big Snore", "Hyjal, Barrows 🔸, UBRS")
        ])
        groups = schedule.roster_groups(
            event, [("Big Snore", "Hyjal, Barrows 🔸, UBRS",
                     "Warrior", "Tank", "Protection")],
        )
        self.assertEqual(
            [title for title, _ in groups],
            ["Hyjal · 20-player", "Barrows · 10-player 🔸",
             "Barrows · 10-player 🔹", "UBRS · 10-player"],
        )
        coverage = buffs.instance_summaries(rows[7:], event)
        self.assertEqual(len(coverage), 4)
        self.assertIn("UBRS", coverage[3][0])

    def test_old_twenty_column_sheet_preserves_names_and_leadership_choices(self):
        # Reproduce the physical order of an existing 20-column raid tab.
        old_order = (0, 1, 2, 3, 4, 5, 9, 11, 12, 13,
                     10, 15, 19, 6, 14, 16, 17, 18, 7, 8)
        source = [""] * 20
        source[0], source[1] = "Snore", "Big Snore"
        source[9], source[10], source[11] = "Selected", "Yes", "No"
        source[14] = "Keep current team"
        source[16], source[17] = "42", "1"
        headers = [sheet.COLUMNS[i] for i in old_order]
        old_values = [source[i] for i in old_order]
        data = [[""] * 20 for _ in range(6)] + [headers, old_values]
        with patch.object(sheet.raid_sheets, "_json", return_value={"values": data}):
            imported = sheet._read(object(), sheet.tab_title(self.event))
        imported_row = imported[7]
        self.assertEqual(imported_row[1], "Big Snore")
        self.assertEqual(imported_row[14], "Keep current team")
        self.assertEqual((imported_row[16], imported_row[17]), ("42", "1"))
        self.assertEqual(imported_row[20], "")
        self.assertEqual(sheet._display(sheet._header(self.event, [], "")[6])[10], "UBRS")
        self.assertEqual(sheet._display(imported_row)[15], "Keep current team")


if __name__ == "__main__":
    unittest.main()
