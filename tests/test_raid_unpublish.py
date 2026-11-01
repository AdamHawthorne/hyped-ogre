"""Unpublish is a reversible public state, never a second ping or attendance change."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs import raid_acknowledgements as ack
from cogs import raid_poster
from cogs import raid_schedule as schedule
from cogs import raid_signup as raid
from wowsims_helper import signup_with_export


class RaidUnpublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "raid.db")
        raid.create_schema(self.db)
        schedule.ensure_raid_fields(self.db)
        schedule.save_raid(self.db, 1, "2026-12-09 21:00", "Hyjal (20m)")
        raid.save_character(42, "Snore", "Warrior", "Protection", "Tank", self.db)
        self.char_id = raid.member_characters(42, self.db)[0][0]
        signup_with_export(1, 42, self.char_id, self.db)
        schedule.raid_status(self.db, 1, "confirm",
                             confirmed_entries=[("42", "Snore", "Hyjal")])

    def test_retraction_cannot_be_acked_and_cannot_reping(self):
        ack.record_announcement(self.db, 1, 555, 777, [("42", "Snore")])
        self.assertTrue(ack.retract(self.db, 1))
        self.assertFalse(ack.retract(self.db, 1))
        self.assertTrue(ack.is_retracted(self.db, 1))
        self.assertIn("Roster withdrawn", ack.render_retracted(1))
        self.assertNotIn("<@42>", ack.render_retracted(1))
        self.assertIsNone(ack.acknowledge(self.db, 555, 777, 42))
        self.assertIsNone(ack.members(self.db, 1)[0][2])
        with self.assertRaisesRegex(ValueError, "already announced"):
            ack.record_announcement(self.db, 1, 555, 778, [("42", "Snore")])

    async def test_manage_raid_unpublish_confirm_retracts_announcement(self):
        ack.record_announcement(self.db, 1, 555, 777, [("42", "Snore")])
        message = SimpleNamespace(content=ack.render(1, ack.pending(self.db, 1), 1),
                                  edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = SimpleNamespace(get_channel=lambda channel_id: channel)
        cog = raid.RaidPrototype(bot)
        cog.allowed = Mock(return_value=True)
        cog.sync_raid_sheets = AsyncMock()
        cog.refresh_saved_panel = AsyncMock()
        response = SimpleNamespace(
            send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        with patch.object(raid, "RAID_DB", self.db):
            private_poster = SimpleNamespace(edit=AsyncMock())
            manage = raid.RaidManageView(
                cog, 1, poster_owner_id=42, poster_message=private_poster,
                poster_draft=raid_poster.draft_from_event(
                    schedule.read_raid(self.db, 1)
                ),
            )
            button = next(
                b for b in manage.children
                if isinstance(b, discord.ui.Button) and b.label == "Unpublish Roster"
            )
            await button.callback(interaction)
            prompt = response.send_message.await_args
            self.assertTrue(prompt.kwargs["ephemeral"])
            self.assertIn("Unpublish Raid #1", prompt.args[0])
            confirm_view = prompt.kwargs["view"]
            self.assertIsInstance(confirm_view, raid.RaidUnpublishConfirmation)
            self.assertTrue(schedule.read_raid(self.db, 1)["roster_published"])
            confirm = next(
                b for b in confirm_view.children
                if isinstance(b, discord.ui.Button)
                and b.label == "Confirm unpublish"
            )
            await confirm.callback(interaction)
        event = schedule.read_raid(self.db, 1)
        self.assertEqual(event["roster_published"], 0)
        self.assertEqual(event["signup_open"], 1)
        self.assertEqual(raid.my_signup(1, 42, self.db),
                         ("Snore", "Available"))
        self.assertFalse(schedule.confirmed_roster(self.db, 1))
        self.assertTrue(ack.is_retracted(self.db, 1))
        self.assertFalse(ack.dirty_announcements(self.db))
        self.assertIn("Roster withdrawn", message.edit.await_args.kwargs["content"])
        self.assertNotIn("<@42>", message.edit.await_args.kwargs["content"])
        mentions = message.edit.await_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.users)
        cog.sync_raid_sheets.assert_awaited_once()
        cog.refresh_saved_panel.assert_awaited_once_with(1)
        private_poster.edit.assert_awaited_once()
        self.assertIn(
            "Collecting sign-ups",
            private_poster.edit.await_args.kwargs["view"].description(),
        )
        # Republish can still happen later, but the old ping stays retracted.
        schedule.raid_status(self.db, 1, "confirm",
                             confirmed_entries=[("42", "Snore", "Hyjal")])
        self.assertTrue(ack.is_retracted(self.db, 1))
        self.assertIsNone(ack.acknowledge(self.db, 555, 777, 42))

    async def test_unpublish_is_rejected_after_archive(self):
        schedule.raid_status(self.db, 1, "archive")
        cog = raid.RaidPrototype(SimpleNamespace())
        cog.allowed = Mock(return_value=True)
        view = raid.RaidManageView(cog, 1)
        button = next(
            b for b in view.children
            if isinstance(b, discord.ui.Button) and b.label == "Unpublish Roster"
        )
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(response=response)
        with patch.object(raid, "RAID_DB", self.db):
            await button.callback(interaction)
        self.assertIn("active raid", response.send_message.await_args.args[0])
