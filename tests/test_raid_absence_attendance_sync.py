"""Offline guards for attendance defaults and completed raid announcement cleanup."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import raid_schedule as schedule, raid_signup as raid
from cogs import raid_sheets_v3 as sheets
from cogs import raid_acknowledgements as ack
from hyped_ogre.features.raids import announcer


class AbsenceAttendanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 21:00", "Hyjal (20m)")
        schedule.record_panel(self.path, 1, 100, 200)
        schedule.raid_status(self.path, 1, "confirm",
                             confirmed_entries=[("42", "Snore", "Hyjal")])
        raid.save_character(42, "Snore", "Warrior", "Protection", "Tank", self.path)
        self.cid = raid.member_characters(42, self.path)[0][0]
        self.event = schedule.read_raid(self.path, 1)
        self.session = object()

    def player_row(self, *, absent):
        row = [""] * len(sheets.COLUMNS)
        row[0], row[1], row[2], row[4] = "Snore", "Snore", "Warrior", "Tank"
        row[8] = "Absent" if absent else "Signed"
        row[9], row[16], row[17] = ("Not selected" if absent else "Selected"), "42", str(self.cid)
        return row

    def permanent(self, player, existing=None):
        attendance = [list(sheets.COLUMNS[:15])]
        if existing is not None:
            row = [""] * 15
            row[0], row[2], row[4], row[9] = "1", "42", str(self.cid), existing
            attendance.append(row)
        roster_header = [""] * 15
        roster_header[8] = "Last 4 completed raids"
        with patch.object(sheets.raid_sheets, "_raid_snapshot",
                          return_value=([], {1: self.event}, [], [], [])), \
             patch.object(sheets, "_permanent_read",
                          side_effect=[[roster_header], attendance]), \
             patch.object(sheets, "_permanent_write") as write, \
             patch.object(sheets, "_permanent_write_cell") as cell:
            sheets._sync_permanent(self.session, self.path, {}, {1: [player]})
        return write.call_args_list, cell.call_args_list

    def test_new_absent_defaults_to_absent_not_pending(self):
        writes, cells = self.permanent(self.player_row(absent=True))
        self.assertEqual(len(writes), 1)
        args = writes[0].args
        self.assertEqual(args[1], "Forever Attendance")
        self.assertEqual(args[3][9], "Absent")
        self.assertEqual(args[3][4], str(self.cid))
        self.assertEqual(cells, [])

    def test_new_selected_defaults_to_pending(self):
        writes, _ = self.permanent(self.player_row(absent=False))
        self.assertEqual(writes[0].args[3][9], "Pending")

    def test_existing_pending_becomes_absent_without_overwriting_notes(self):
        writes, cells = self.permanent(self.player_row(absent=True), existing="Pending")
        self.assertEqual(writes, [])
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].args[1:], ("Forever Attendance", "J6", "Absent"))

    def test_manually_chosen_excused_attended_and_absent_are_preserved(self):
        for status in ("Excused", "Attended", "Absent"):
            with self.subTest(status=status):
                writes, cells = self.permanent(self.player_row(absent=True), existing=status)
                self.assertEqual(writes, [])
                self.assertEqual(cells, [])

    def test_existing_completed_raid_pending_is_backfilled_from_absence(self):
        signup = {
            "raid_id": 1, "discord_id": 42, "character_id": self.cid,
            "character": "Snore", "class_name": "Warrior", "role": "Tank",
        }
        event = schedule.raid_status(self.path, 1, "archive")
        collected = {}
        sheets._append_reported_absences(
            collected, {1: event}, [signup],
            [{"raid_id": 1, "discord_id": 42}], {42: "Snore"},
        )
        self.assertEqual(len(collected[1]), 1)
        self.assertEqual(collected[1][0][8], "Absent")
        writes, cells = self.permanent(collected[1][0], existing="Pending")
        self.assertEqual(writes, [])
        self.assertEqual([c.args[1:] for c in cells],
                         [("Forever Attendance", "J6", "Absent")])
        # Retry never generates a second row.
        sheets._append_reported_absences(
            collected, {1: event}, [signup],
            [{"raid_id": 1, "discord_id": 42}], {},
        )
        self.assertEqual(len(collected[1]), 1)

    def test_absence_only_player_is_carried_to_permanent_attendance(self):
        # An absent member need not have an existing selected raid spot.
        absent = self.player_row(absent=True)
        absent[17] = ""  # member might not yet have chosen a character
        with patch.object(sheets.raid_sheets, "_raid_snapshot",
                          return_value=([], {1: self.event}, [], [], [])), \
             patch.object(sheets.raid_sheets, "configured", return_value=True), \
             patch.object(sheets.raid_sheets, "_session", return_value=MagicMock()), \
             patch.object(sheets, "_list_tabs", return_value={}), \
             patch.object(sheets, "_one", return_value=(1, [[""] * 20] * 7 + [absent])), \
             patch.object(sheets, "_sync_permanent") as permanent, \
             patch.object(sheets, "_sync_gear_tab"), \
             patch.object(sheets, "_hide_legacy"), \
             patch.object(sheets, "_hide_archived"):
            sheets.sync_once(self.path)
        captured = permanent.call_args.args[3]
        self.assertEqual(captured[1][0][8], "Absent")
        self.assertEqual(captured[1][0][16], "42")


class CompletedAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 21:00", "Hyjal (20m)")
        schedule.record_panel(self.path, 1, 111, 222)
        schedule.raid_status(self.path, 1, "confirm",
                             confirmed_entries=[("42", "Snore", "Hyjal")])
        ack.record_announcement(self.path, 1, 111, 333, [("42", "Snore")])
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.guild = SimpleNamespace(id=99)
        self.message = SimpleNamespace(author=SimpleNamespace(id=999),
                                       delete=AsyncMock())
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_channel=lambda channel_id: self.channel if channel_id == 111 else None,
            fetch_channel=AsyncMock(side_effect=AssertionError("wrong channel")),
        )

    async def test_archive_deletes_only_separate_ack_and_retains_v2_post(self):
        schedule.raid_status(self.path, 1, "archive")
        self.assertEqual(ack.dirty_announcements(self.path), [1])
        await announcer.refresh(self.bot, self.path, 1, 99)
        self.channel.fetch_message.assert_awaited_once_with(333)
        self.message.delete.assert_awaited_once()
        self.assertEqual(schedule.read_raid(self.path, 1)["panel_message_id"], 222)
        self.assertEqual(ack.announcement(self.path, 1), (111, 333))
        self.assertEqual(ack.dirty_announcements(self.path), [])
        self.assertIsNone(ack.acknowledge(self.path, 111, 333, 42))

    async def test_older_archived_announcement_is_cleaned_once_after_restart(self):
        schedule.raid_status(self.path, 1, "archive")
        # Simulate an earlier release that archived the raid but did not
        # queue deletion of its separate roster acknowledgement message.
        with schedule.database(self.path) as con:
            con.execute("UPDATE raid_ack_announcements SET needs_refresh=0 "
                        "WHERE raid_id=1")
        schedule.ensure_raid_fields(self.path)
        self.assertEqual(ack.dirty_announcements(self.path), [1])
        await announcer.refresh(self.bot, self.path, 1, 99)
        self.message.delete.assert_awaited_once()
        with schedule.database(self.path) as con:
            deleted_at = con.execute(
                "SELECT deleted_at FROM raid_ack_announcements WHERE raid_id=1"
            ).fetchone()[0]
        self.assertTrue(deleted_at)
        schedule.ensure_raid_fields(self.path)  # Another restart
        self.assertEqual(ack.dirty_announcements(self.path), [])

    async def test_missing_ack_is_already_clean_and_does_not_delete_v2(self):
        schedule.raid_status(self.path, 1, "archive")
        response = MagicMock(status=404, reason="Not Found")
        self.channel.fetch_message.side_effect = discord.NotFound(response, "Unknown message")
        await announcer.refresh(self.bot, self.path, 1, 99)
        self.message.delete.assert_not_awaited()
        self.assertEqual(ack.dirty_announcements(self.path), [])
        self.assertEqual(schedule.read_raid(self.path, 1)["panel_message_id"], 222)

    async def test_failure_does_not_mark_done_and_can_retry(self):
        schedule.raid_status(self.path, 1, "archive")
        self.message.delete.side_effect = discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "No permission")
        with self.assertRaises(discord.Forbidden):
            await announcer.refresh(self.bot, self.path, 1, 99)
        self.assertEqual(ack.dirty_announcements(self.path), [1])
        self.message.delete.side_effect = None
        await announcer.retry_dirty(self.bot, self.path, 99)
        self.assertEqual(ack.dirty_announcements(self.path), [])
        self.assertEqual(self.message.delete.await_count, 2)

    async def test_not_ogre_authored_announcement_is_not_deleted(self):
        schedule.raid_status(self.path, 1, "archive")
        self.message.author.id = 765
        with self.assertRaisesRegex(ValueError, "not authored by Ogre"):
            await announcer.refresh(self.bot, self.path, 1, 99)
        self.message.delete.assert_not_awaited()
        self.assertEqual(ack.dirty_announcements(self.path), [1])


class CompletionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_syncs_absences_before_archiving_and_cleans_ack(self):
        from cogs import raid_signup as signup
        calls = []
        cog = SimpleNamespace(
            allowed=lambda _interaction: True,
            sync_raid_sheets=AsyncMock(side_effect=lambda: calls.append("sync")),
            cleanup_completed_announcement=AsyncMock(
                side_effect=lambda _raid_id: calls.append("ack")),
            refresh_saved_panel=AsyncMock(side_effect=lambda _raid_id: calls.append("panel")),
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        view = signup.RaidCompletionConfirm(cog, 1, "", "")
        button = next(b for b in view.children
                      if isinstance(b, discord.ui.Button)
                      and b.label == "Confirm completion")
        with patch.object(signup.raid_sheets_v3, "attendance_complete",
                          side_effect=lambda *_: calls.append("attendance") or 3), \
             patch.object(signup, "raid_status",
                          side_effect=lambda *_args, **_kw: calls.append("archive")), \
             patch.object(signup, "refresh_management_poster", new_callable=AsyncMock):
            await button.callback(interaction)
        self.assertEqual(calls, ["sync", "attendance", "archive", "ack", "sync", "panel"])
        cog.cleanup_completed_announcement.assert_awaited_once_with(1)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
