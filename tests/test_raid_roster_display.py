"""Raid display changes preserve collected sign-ups and private Sheet choices."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs import raid_poster as poster
from cogs import raid_schedule as schedule
from cogs import raid_sheets_v3 as sheets
from cogs import raid_signup as raid


class RaidRosterDisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.event = schedule.save_raid(
            self.path,
            1,
            "2026-12-09 21:00",
            "Hyjal (20m)",
            "Barrows (10m)",
            wowsims_policy="optional",
        )
        # The owner's character ID and sign-up time are deliberately later.
        for member, name, klass, spec, role in (
            (41, "Alpha", "Warrior", "Protection", "Tank"),
            (43, "Mage", "Mage", "Frost", "DPS"),
            (42, "Zulu", "Warrior", "Protection", "Tank"),
        ):
            raid.save_character(member, name, klass, spec, role, self.path)
            cid = raid.member_characters(member, self.path)[0][0]
            raid.save_signup(1, member, cid, self.path, note=f"Note {member}")
        owner = patch.dict(os.environ, {"OWNER_ID": "42"})
        owner.start()
        self.addCleanup(owner.stop)

    def snapshot(self):
        with schedule.database(self.path) as con:
            return {
                table: con.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "characters",
                    "signups",
                    "raid_absences",
                    "attendance",
                    "gear_exports",
                )
            }

    def panel(self, event):
        with patch.object(raid, "RAID_DB", self.path):
            return raid.RaidPrototype(SimpleNamespace()).make_panel(event)

    @staticmethod
    def panel_text(panel):
        return "\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )

    def selected_rows(self):
        body = sheets._rows(self.path, self.event, {})
        for row in body:
            if row[16]:
                row[11] = "Yes"
                row[12] = "🔹" if row[16] == "43" else "🔸"
                row[14] = "Keep leadership note"
        return sheets._header(self.event, body, sheets.tab_title(self.event))

    async def test_collecting_panel_matches_sheet_owner_order_and_adds_crown(self):
        before = self.snapshot()
        sheet_tanks = [row[1] for row in self.selected_rows() if row[4] == "Tank"]
        self.assertEqual(sheet_tanks, ["Zulu", "Alpha"])
        text = self.panel_text(self.panel(self.event))
        tanks = text.split("**Tanks (2)**", 1)[1].split("**Ranged", 1)[0]
        self.assertLess(tanks.index("Zulu"), tanks.index("Alpha"))
        self.assertIn("Zulu " + schedule.CREATOR_CROWN, tanks)
        self.assertEqual(text.count(schedule.CREATOR_CROWN), 1)
        self.assertEqual(self.snapshot(), before)

    async def test_creator_uses_member_id_not_character_name_or_lowest_id(self):
        sheets._save_public_for_one(
            self.path,
            1,
            [
                ("Same Name", "Hyjal", "Warrior", "Tank", "Protection", "41"),
                ("Same Name", "Hyjal", "Warrior", "Tank", "Protection", "42"),
            ],
        )
        entries = schedule.read_public_roster(self.path, 1, include_identity=True)
        groups = schedule.roster_groups(self.event, entries)
        names = dict(groups)["Hyjal · 20-player"]
        self.assertTrue(names[0].endswith("Same Name " + schedule.CREATOR_CROWN))
        self.assertNotIn(schedule.CREATOR_CROWN, names[1])
        # Guest and legacy rows with no saved identity cannot inherit a crown.
        self.assertNotIn(
            schedule.CREATOR_CROWN, schedule.roster_name_with_emojis("Zulu")
        )
        mixed = schedule.roster_groups(self.event, [("Guest", "Hyjal"), *entries])
        self.assertEqual(sum(len(names) for _, names in mixed), 3)

    async def test_sheet_sync_keeps_identity_and_is_stable_across_repeat_syncs(self):
        schedule.record_panel(self.path, 1, 777, 888)
        rows = self.selected_rows()
        selected = sheets._approved(rows, self.event, include_details=True)
        schedule.raid_status(self.path, 1, "confirm", confirmed_entries=selected)
        # Simulate an existing published cache from before member IDs were saved.
        sheets._save_public_for_one(self.path, 1, [entry[1:6] for entry in selected])
        previous_update = "2026-11-01T20:00:00+00:00"
        with schedule.database(self.path) as con:
            con.execute(
                "UPDATE raids SET roster_updated_at=? WHERE raid_id=1",
                (previous_update,),
            )
        before = self.snapshot()
        session = Mock()
        with (
            patch.object(sheets.raid_sheets, "configured", return_value=True),
            patch.object(sheets.raid_sheets, "_session", return_value=session),
            patch.object(
                sheets,
                "_list_tabs",
                return_value={sheets.tab_title(self.event): {"sheetId": 123}},
            ),
            patch.object(sheets, "_one", return_value=(0, rows)),
            patch.object(sheets, "_sync_permanent"),
            patch.object(sheets, "_sync_gear_tab"),
            patch.object(sheets, "_hide_legacy"),
        ):
            self.assertEqual(sheets.sync_once(self.path)[1], {1})
            self.assertEqual(sheets.sync_once(self.path)[1], set())
        public = schedule.read_public_roster(self.path, 1, include_identity=True)
        self.assertEqual({entry[5] for entry in public}, {"41", "42", "43"})
        self.assertEqual(
            schedule.read_raid(self.path, 1)["roster_updated_at"], previous_update
        )
        # Existing callers keep their historical 2- and 5-field tuples.
        self.assertTrue(
            all(len(row) == 2 for row in schedule.read_public_roster(self.path, 1))
        )
        self.assertTrue(
            all(
                len(row) == 5
                for row in schedule.read_public_roster(
                    self.path, 1, include_details=True
                )
            )
        )
        text = self.panel_text(self.panel(schedule.read_raid(self.path, 1)))
        self.assertEqual(text.count("Zulu " + schedule.CREATOR_CROWN), 2)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(
            all(row[14] == "Keep leadership note" for row in rows if row[16].isdigit())
        )

    async def test_identity_migration_keeps_existing_rows_and_is_repeatable(self):
        legacy = str(Path(self.tmp.name) / "legacy.db")
        raid.create_schema(legacy)
        with schedule.database(legacy) as con:
            con.execute(
                "INSERT INTO raids(raid_id,title,raid_at,signup_open) "
                "VALUES(1,'Test','2026-12-09T21:00:00+00:00',1)"
            )
            con.execute(
                "INSERT INTO raid_public_roster(raid_id,position,name,assignment) "
                "VALUES(1,0,'Saved Player','Hyjal')"
            )
            old = con.execute("SELECT * FROM raid_public_roster").fetchall()
        schedule.ensure_raid_fields(legacy)
        schedule.ensure_raid_fields(legacy)
        with schedule.database(legacy) as con:
            self.assertEqual(
                con.execute(
                    "SELECT raid_id,position,name,assignment FROM raid_public_roster"
                ).fetchall(),
                old,
            )
        self.assertEqual(
            schedule.read_public_roster(legacy, 1, include_identity=True),
            [("Saved Player", "Hyjal", None, None, None, "")],
        )

    async def test_preview_has_instance_headings_and_small_names_without_assignment_suffixes(
        self,
    ):
        selected = [
            ("41", "Alpha", "Hyjal, Barrows 🔸"),
            ("43", "Mage", "Hyjal, Barrows 🔹"),
            ("42", "Zulu", "Hyjal, Barrows 🔸"),
        ]
        text = "\n".join(poster.roster_preview_pages(self.event, selected))
        self.assertEqual(text.count("**Hyjal** 🔸🔹"), 1)
        self.assertEqual(text.count("**Barrows** 🔸"), 1)
        self.assertEqual(text.count("**Barrows** 🔹"), 1)
        self.assertEqual(text.count("-# Zulu " + schedule.CREATOR_CROWN), 2)
        self.assertIn("-# Mage", text)
        self.assertNotIn("Hyjal, Barrows", text)
        self.assertNotIn("**1.", text)
        self.assertLess(text.index("-# Zulu"), text.index("-# Alpha"))

    async def test_large_preview_shows_every_selected_name_within_message_limits(self):
        selected = [
            (
                str(1000 + n),
                f"Player{n:02d}" + "x" * 70,
                "Hyjal, Barrows " + ("🔸" if n % 2 else "🔹"),
            )
            for n in range(60)
        ]
        pages = poster.roster_preview_pages(self.event, selected)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 1650 for page in pages))
        self.assertTrue(all("…" not in page for page in pages))
        for entry in selected:
            self.assertEqual("\n".join(pages).count(entry[1]), 2)

    async def test_opening_confirmation_preview_does_not_publish_or_modify_signups(
        self,
    ):
        schedule.record_panel(self.path, 1, 777, 888)
        cog = SimpleNamespace(
            raid_db_path=lambda: self.path,
            allowed=lambda _: True,
            sync_raid_sheets=AsyncMock(),
            refresh_saved_panel=AsyncMock(),
        )
        view = poster.RaidPosterView(
            cog, 42, poster.draft_from_event(schedule.read_raid(self.path, 1))
        )
        button = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
            and item.label == "Preview / Confirm Roster"
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            message=SimpleNamespace(),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        selected = [
            ("42", "Zulu", "Hyjal, Barrows 🔸"),
            ("43", "Mage", "Hyjal, Barrows 🔹"),
        ]
        before = self.snapshot()
        with patch.object(sheets, "read_selected", return_value=selected):
            await button.callback(interaction)
        sent = interaction.followup.send.await_args
        self.assertIn("**Hyjal** 🔸🔹", sent.args[0])
        self.assertIsInstance(sent.kwargs["view"], poster.RaidRosterPreview)
        self.assertEqual(sent.kwargs["view"].snapshot, tuple(selected))
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertLessEqual(len(sent.args[0]), 2000)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(schedule.read_raid(self.path, 1)["roster_published"], 0)


if __name__ == "__main__":
    unittest.main()
