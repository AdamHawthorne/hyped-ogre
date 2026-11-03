"""Keep public Attend open through roster confirmation until raid completion."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs import raid_schedule as schedule, raid_signup as raid, raid_poster
from wowsims_helper import signup_with_export


class RaidSignupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_raid(self, path):
        raid.create_schema(path)
        schedule.ensure_raid_fields(path)
        # Dates intentionally in the past: expiry of the announcement or
        # raid start time must never close sign-ups automatically.
        event = schedule.save_raid(
            path, 1, "2026-10-25 20:00", "Hyjal (20m)",
            roster_announce="2026-10-24 20:00"
        )
        raid.save_character(42, "Snore", "Warrior", "Protection", "Tank", path)
        char_id = raid.member_characters(42, path)[0][0]
        signup_with_export(1, 42, char_id, path)
        return event

    @staticmethod
    def button_disabled(event, label):
        panel = schedule.RaidEventPanel(
            event, on_signup=AsyncMock(), on_gear=AsyncMock()
        )
        button = next(
            child for child in panel.walk_children()
            if isinstance(child, discord.ui.Button) and child.label == label
        )
        return button.disabled

    @staticmethod
    def attend_disabled(event):
        return RaidSignupLifecycleTests.button_disabled(event, "Attend")

    @staticmethod
    def wowsims_disabled(event):
        return RaidSignupLifecycleTests.button_disabled(event, "WoWSims")

    async def test_roster_publication_and_elapsed_dates_never_close_attend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            event = self.make_raid(path)
            self.assertFalse(self.attend_disabled(event))
            self.assertFalse(self.wowsims_disabled(event))
            confirmed = schedule.raid_status(
                path, 1, "confirm",
                confirmed_entries=[("42", "Snore", "Hyjal")]
            )
            self.assertTrue(confirmed["roster_published"])
            self.assertEqual(confirmed["signup_open"], 1)
            self.assertFalse(self.attend_disabled(confirmed))
            self.assertFalse(self.wowsims_disabled(confirmed))
            # A new person can register after the public roster is selected.
            raid.save_character(43, "LateMage", "Mage", "Frost", "DPS", path)
            new_char = raid.member_characters(43, path)[0][0]
            signup_with_export(1, 43, new_char, path)
            self.assertEqual(raid.my_signup(1, 43, path),
                             ("LateMage", "Available"))
            # Sheet/raid edits and repeating confirmation cannot close Attend.
            edited = schedule.save_raid(
                path, 1, "2026-10-25 20:00", "Hyjal (20m)",
                "Barrows (10m)"
            )
            self.assertFalse(self.attend_disabled(edited))
            schedule.raid_status(
                path, 1, "confirm",
                confirmed_entries=[("42", "Snore", "Hyjal"),
                                   ("43", "LateMage", "Barrows")]
            )
            self.assertFalse(self.attend_disabled(schedule.read_raid(path, 1)))
            self.assertFalse(self.wowsims_disabled(schedule.read_raid(path, 1)))

    async def test_complete_raid_closes_attend_and_rejects_new_signups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.make_raid(path)
            schedule.raid_status(
                path, 1, "confirm",
                confirmed_entries=[("42", "Snore", "Hyjal")]
            )
            cog = SimpleNamespace(
                allowed=lambda _interaction: True,
                raid_db_path=lambda: path,
                sync_raid_sheets=AsyncMock(),
                refresh_saved_panel=AsyncMock(),
                cleanup_completed_announcement=AsyncMock()
            )
            private_poster = SimpleNamespace(edit=AsyncMock())
            view = raid.RaidCompletionConfirm(
                cog, 1, "", "Raid complete",
                poster_owner_id=42, poster_message=private_poster,
                poster_draft=raid_poster.draft_from_event(
                    schedule.read_raid(path, 1)
                )
            )
            button = next(
                child for child in view.children
                if isinstance(child, discord.ui.Button)
                and child.label == "Confirm completion"
            )
            interaction = SimpleNamespace(
                response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
                edit_original_response=AsyncMock()
            )
            with patch.object(raid, "RAID_DB", path), \
                 patch.object(raid_sheets_v3, "attendance_complete", return_value=1):
                await button.callback(interaction)
            closed = schedule.read_raid(path, 1)
            self.assertIsNotNone(closed["archived_at"])
            self.assertEqual(closed["signup_open"], 0)
            self.assertTrue(self.attend_disabled(closed))
            self.assertTrue(self.wowsims_disabled(closed))
            cog.refresh_saved_panel.assert_awaited_once_with(1)
            private_poster.edit.assert_awaited_once()
            self.assertIn(
                "Completed / archived",
                private_poster.edit.await_args.kwargs["view"].description()
            )
            interaction.followup.send.assert_awaited_once()
            raid.save_character(43, "LateMage", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(43, path)[0][0]
            with self.assertRaisesRegex(ValueError, "aren't open"):
                signup_with_export(1, 43, char_id, path)

    async def test_sheets_failure_during_completion_still_refreshes_closed_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            self.make_raid(path)
            schedule.raid_status(
                path, 1, "confirm",
                confirmed_entries=[("42", "Snore", "Hyjal")]
            )
            cog = SimpleNamespace(
                allowed=lambda _interaction: True,
                sync_raid_sheets=AsyncMock(side_effect=RuntimeError("Sheets offline")),
                refresh_saved_panel=AsyncMock(),
                cleanup_completed_announcement=AsyncMock()
            )
            view = raid.RaidCompletionConfirm(cog, 1, "", "")
            button = next(
                child for child in view.children
                if isinstance(child, discord.ui.Button)
                and child.label == "Confirm completion"
            )
            interaction = SimpleNamespace(
                response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
                edit_original_response=AsyncMock()
            )
            with patch.object(raid, "RAID_DB", path), \
                 patch.object(raid_sheets_v3, "attendance_complete", return_value=1):
                await button.callback(interaction)
            self.assertIsNotNone(schedule.read_raid(path, 1)["archived_at"])
            self.assertTrue(self.attend_disabled(schedule.read_raid(path, 1)))
            self.assertTrue(self.wowsims_disabled(schedule.read_raid(path, 1)))
            cog.refresh_saved_panel.assert_awaited_once_with(1)
            self.assertIn("Sheets offline", interaction.followup.send.await_args.args[0])


# Import after the class body so the tests use the real Sheets module.
from cogs import raid_sheets_v3
