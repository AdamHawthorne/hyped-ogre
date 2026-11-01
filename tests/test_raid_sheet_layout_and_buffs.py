"""Raid sheet layout migration, editable metadata, Forever buff coverage."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from cogs import raid_sheets_v3 as sheets
from cogs import forever_raid_buffs as buffs
from cogs import raid_signup as raid, raid_schedule as schedule
from wowsims_helper import signup_with_export


OLD_LAYOUT = (0, 1, 2, 3, 4, 5, 6, 8, 9, 11, 12, 13, 10,
              15, 14, 16, 17, 18, 7, 19)


class LayoutAndBuffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.event = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00",
            "Hyjal (20m)", "Barrows (10m)", "Onyxia (40m)",
        )
        chars = (
            (42, "Snore Mon", "Warrior", "Protection", "Tank"),
            (43, "Arcane Mage", "Mage", "Arcane", "DPS"),
            (44, "Priest One", "Priest", "Holy", "Healer"),
            (45, "Shaman One", "Shaman", "Enhancement", "DPS"),
        )
        for did, name, klass, spec, role in chars:
            raid.save_character(did, name, klass, spec, role, self.path)
            cid = raid.member_characters(did, self.path)[0][0]
            signup_with_export(1, did, cid, self.path)

    def test_new_columns_put_notes_together_history_after_gear_status_at_end(self):
        labels = [sheets.COLUMNS[index] for index in sheets.DISPLAY_ORDER]
        self.assertEqual(labels[6:15], [
            "Selection", "Hyjal", "Barrows", "Ony", "Show",
            "WoWSims gear", "Past 3 | Next 3",
            "Sign-up Notes", "Leadership Notes",
        ])
        self.assertEqual(labels[-1], "Status")
        self.assertEqual(labels[15:19], [
            "Discord ID (internal)", "Character ID (internal)",
            "Absent At", "Signed At",
        ])

    def test_reads_old_sheet_by_column_names_and_retains_editable_notes(self):
        with schedule.database(self.path) as con:
            con.execute("UPDATE signups SET note=? WHERE raid_id=1 AND discord_id=42",
                        ("Sign-up note",))
        body = sheets._rows(self.path, self.event, {})
        member = next(row for row in body if row[16] == "42")
        member[6] = "Sign-up note"
        member[14] = "Leadership's manual note"
        member[9] = "Bench"
        member[10] = "No"
        member[11] = "Yes"
        member[12] = "🔸"
        member[19] = "Missing · Missing · Attend | Attend · Absent · Absent"
        full = sheets._header(self.event, body, sheets.tab_title(self.event))
        old_cells = [
            [r[idx] for idx in OLD_LAYOUT]
            for r in full
        ]
        old_cells[6][12] = "Show on Discord"
        with patch.object(sheets.raid_sheets, "_json",
                          return_value={"values": old_cells}):
            loaded = sheets._read(Mock(), "Raid #1 - Wed 09 Dec 2026")
        row = next(row for row in loaded if row[16] == "42")
        self.assertEqual(row[6], "Sign-up note")
        self.assertEqual(row[14], "Leadership's manual note")
        self.assertEqual(row[9], "Bench")
        self.assertEqual(row[10], "No")
        self.assertEqual(row[11:13], ["Yes", "🔸"])
        # The new view moves notes but doesn't overwrite the original values.
        updated = sheets._rows(
            self.path, self.event, {}, old_rows=loaded
        )
        later = next(r for r in updated if r[16] == "42")
        self.assertEqual(later[14], "Leadership's manual note")
        visible = sheets._display(later)
        self.assertEqual(visible[13], "Sign-up note")
        self.assertEqual(visible[14], "Leadership's manual note")
        self.assertEqual(visible[19], "Signed")

    def test_buff_dashboard_g2_h5_and_absence_of_coverage(self):
        body = sheets._rows(self.path, self.event, {})
        for row in body:
            if row[16] == "42":
                row[11] = "Yes"
                row[12] = "🔸"
            if row[16] == "43":
                row[12] = "🔹"
            if row[16] == "44":
                row[11] = "Yes"
            if row[16] == "45":
                row[12] = "🔸"
        top = sheets._header(self.event, body, sheets.tab_title(self.event))
        self.assertEqual(top[1][6], "Barrows 🔸 (2)")
        self.assertIn("Intellect", top[1][7])
        self.assertNotIn("Battle Shout", top[1][7])
        self.assertNotIn("Shaman Totems", top[1][7])
        self.assertEqual(top[2][6], "Barrows 🔹 (1)")
        self.assertNotIn("Intellect", top[2][7])
        self.assertIn("Fortitude", top[2][7])
        self.assertEqual(top[3][6], "Hyjal (2)")
        self.assertEqual(top[3][2], "Raid Notes")
        self.assertIn("Intellect", top[3][7])
        self.assertNotIn("Fortitude", top[3][7])
        self.assertEqual(top[4][6], "Ony (0)")
        self.assertEqual(top[4][7], "No team selected")
        self.assertIn("WoWSims", top[6][15])
        # Class names alone must not indicate a spec-only aura.
        self.assertIn("Trueshot Aura", top[1][7])

    def test_only_selected_signed_players_count_for_class_coverage(self):
        body = sheets._rows(self.path, self.event, {})
        members = [r for r in body if r[16]]
        mage = next(r for r in members if r[2] == "Mage")
        warrior = next(r for r in members if r[2] == "Warrior")
        warrior[12] = "🔸"
        mage[12] = "🔸"
        mage[9] = "Bench"
        self.assertIn("Intellect", buffs.instance_summaries(
            body, self.event
        )[0][1])
        mage[9] = "Selected"
        self.assertNotIn("Intellect", buffs.instance_summaries(
            body, self.event
        )[0][1])
        mage[8] = "Absent"
        self.assertIn("Intellect", buffs.instance_summaries(
            body, self.event
        )[0][1])

    def test_b4_wcl_and_d4_notes_are_editable_and_survive_completion(self):
        top = sheets._header(
            self.event, sheets._rows(self.path, self.event, {}),
            sheets.tab_title(self.event),
        )
        link = "https://classic.warcraftlogs.com/reports/abc123"
        top[3][1] = link
        top[3][3] = "Plan better interrupt assignments."
        self.assertIsNone(sheets._apply_wcl_from_sheet(
            self.path, self.event, top
        ))
        self.assertIsNone(sheets._apply_notes_from_sheet(
            self.path, self.event, top
        ))
        event = schedule.read_raid(self.path, 1)
        self.assertEqual(event["wcl_url"], link)
        self.assertEqual(event["archive_notes"],
                         "Plan better interrupt assignments.")
        with schedule.database(self.path) as con:
            con.execute("UPDATE raids SET raid_notes_sheet_synced=? "
                        "WHERE raid_id=1", (event["archive_notes"],))
        # An unchanged D4 must not overwrite a newer command-side edit.
        with schedule.database(self.path) as con:
            con.execute("UPDATE raids SET archive_notes=? WHERE raid_id=1",
                        ("More recent note",))
        event = schedule.read_raid(self.path, 1)
        self.assertIsNone(sheets._apply_notes_from_sheet(
            self.path, event, top
        ))
        self.assertEqual(schedule.read_raid(self.path, 1)["archive_notes"],
                         "More recent note")
        top[3][3] = "New sheet note"
        self.assertIsNone(sheets._apply_notes_from_sheet(
            self.path, event, top
        ))
        schedule.raid_status(self.path, 1, "confirm", confirmed_entries=[
            ("42", "Snore Mon", "Hyjal"),
        ])
        completed = schedule.raid_status(
            self.path, 1, "archive", notes="", wcl_url=""
        )
        self.assertEqual(completed["wcl_url"], link)
        self.assertEqual(completed["archive_notes"], "New sheet note")

    def test_completion_form_reuses_saved_wcl_and_notes(self):
        from cogs import raid_signup
        link = "https://www.warcraftlogs.com/reports/d3d4d5"
        schedule.set_wcl_url(self.path, 1, link)
        with schedule.database(self.path) as con:
            con.execute("UPDATE raids SET archive_notes=? WHERE raid_id=1",
                        ("Previously written review notes",))
        with patch.object(raid_signup, "RAID_DB", self.path):
            modal = raid_signup.RaidCompleteModal(Mock(), 1)
        self.assertEqual(modal.wcl.default, link)
        self.assertEqual(modal.notes.default,
                         "Previously written review notes")


if __name__ == "__main__":
    unittest.main()
