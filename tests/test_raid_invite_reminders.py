"""Raid reminder is a separate no-ping post 15 minutes before raid start."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import raid_acknowledgements as ack
from cogs import raid_schedule as schedule
from cogs import raid_signup as signup
from cogs import raid_sheets_v3 as sheets
from hyped_ogre.features.classrooms.config import GUILD_ID
from hyped_ogre.features.raids import announcer
from hyped_ogre.features.raids import invite_reminders as reminders
from hyped_ogre.features.raids.leaders import set_group_leader


class RaidInviteReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "raids.db")
        signup.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 2, "2026-11-04 20:00",
                           "Hyjal (20m)", "Barrows (10m)", "Ony (40m)")
        schedule.record_panel(self.path, 2, 333, 444)
        self.selected = [
            ("43", "Razti Mage", "Hyjal, Barrows 🔹", "Mage", "DPS", "Frost"),
            ("42", "Snore Mon", "Ony, Barrows 🔸", "Warrior", "Tank", "Protection"),
        ]
        schedule.raid_status(self.path, 2, "confirm",
                             confirmed_entries=self.selected)
        sheets._save_public_for_one(
            self.path, 2,
            [(name, assignment, klass, role, spec, member)
             for member, name, assignment, klass, role, spec in self.selected],
        )
        ack.record_announcement(self.path, 2, 333, 777,
                                [("42", "Snore Mon"), ("43", "Razti Mage")])
        set_group_leader(self.path, 2, "Hyjal", 43)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.guild = SimpleNamespace(id=GUILD_ID, me=object())
        self.channel.permissions_for.return_value = SimpleNamespace(send_messages=True)
        self.channel.send = AsyncMock(return_value=SimpleNamespace(id=888))
        self.bot = SimpleNamespace(
            get_channel=MagicMock(return_value=self.channel),
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        self.start = datetime.fromisoformat(
            schedule.read_raid(self.path, 2)["raid_at"]
        ).astimezone(timezone.utc)

    def test_exact_simple_message_without_mention_markup(self):
        self.assertEqual(
            reminders.invite_text(2, ["/w Snore Mon inv", "/w Razti Mage inv"]),
            "Raid starting, good luck.\n\n/w Snore Mon inv\n/w Razti Mage inv",
        )

    def test_window_only_t_minus_15_until_start(self):
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            early = list(reminders.due_invites(
                self.path, now=self.start - timedelta(minutes=16)))
            start = list(reminders.due_invites(
                self.path, now=self.start - timedelta(minutes=15)))
            late = list(reminders.due_invites(
                self.path, now=self.start - timedelta(minutes=1)))
            ended = list(reminders.due_invites(self.path, now=self.start))
        self.assertEqual(early, [])
        self.assertEqual(len(start), 1)
        self.assertEqual(len(late), 1)
        self.assertEqual(ended, [])
        self.assertEqual(start[0][1], ["/w Snore Mon inv", "/w Razti Mage inv"])

    async def test_send_once_with_no_user_or_role_pings(self):
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            at = self.start - timedelta(minutes=15)
            sent = await reminders.send_due_invites(
                self.bot, self.path, GUILD_ID, now=at
            )
            retried = await reminders.send_due_invites(
                self.bot, self.path, GUILD_ID, now=at + timedelta(seconds=30)
            )
        self.assertEqual(sent, [2])
        self.assertEqual(retried, [])
        self.channel.send.assert_awaited_once()
        kwargs = self.channel.send.await_args.kwargs
        self.assertEqual(kwargs["content"],
            "Raid starting, good luck.\n\n/w Snore Mon inv\n/w Razti Mage inv")
        mentions = kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.users)
        with schedule.database(self.path) as con:
            row = con.execute(
                "SELECT message_id, posted_at FROM raid_invite_posts "
                "WHERE raid_id=2"
            ).fetchone()
        self.assertEqual(row[0], 888)
        self.assertTrue(row[1])

    async def test_restart_safe_if_discord_send_fails(self):
        self.channel.send.side_effect = discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "Missing Access"
        )
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            at = self.start - timedelta(minutes=15)
            self.assertEqual(await reminders.send_due_invites(
                self.bot, self.path, GUILD_ID, now=at), [])
            self.channel.send.side_effect = None
            self.assertEqual(await reminders.send_due_invites(
                self.bot, self.path, GUILD_ID, now=at + timedelta(seconds=30)), [])
        self.assertEqual(self.channel.send.await_count, 1)

    def test_no_invites_for_withdrawn_or_archived_raid(self):
        ack.retract(self.path, 2)
        self.assertEqual(list(reminders.due_invites(
            self.path, now=self.start - timedelta(minutes=15))), [])
        with schedule.database(self.path) as con:
            con.execute("UPDATE raid_ack_announcements SET retracted_at=NULL "
                        "WHERE raid_id=2")
        schedule.raid_status(self.path, 2, "archive")
        self.assertEqual(list(reminders.due_invites(
            self.path, now=self.start - timedelta(minutes=15))), [])

    def test_no_invites_before_announcement(self):
        with schedule.database(self.path) as con:
            con.execute("DELETE FROM raid_ack_members WHERE raid_id=2")
            con.execute("DELETE FROM raid_ack_announcements WHERE raid_id=2")
        self.assertEqual(list(reminders.due_invites(
            self.path, now=self.start - timedelta(minutes=15))), [])

    async def test_other_guild_never_receives_message_or_claim(self):
        self.channel.guild.id = -1
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            self.assertEqual(await reminders.send_due_invites(
                self.bot, self.path, GUILD_ID,
                now=self.start - timedelta(minutes=15)), [])
        self.channel.send.assert_not_awaited()
        with schedule.database(self.path) as con:
            count = con.execute("SELECT COUNT(*) FROM raid_invite_posts").fetchone()[0]
        self.assertEqual(count, 0)

    async def test_confirmation_announcement_has_no_early_invites(self):
        # The separate V1 confirm now only collects reactions.
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            text = ack.render(2, [("42", "Snore Mon")], 1)
        self.assertIn("React below to confirm raid spot", text)
        self.assertNotIn("/w", text)
        self.assertNotIn("Raid starting", text)


if __name__ == "__main__":
    unittest.main()
