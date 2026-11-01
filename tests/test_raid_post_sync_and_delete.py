"""Posting confirmation, permitted sync channel and exact Sheets deletion."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs import raid_signup as raid, raid_schedule as schedule
from cogs import raid_sheets_v3 as sheets


class RaidSheetDeletionTests(unittest.TestCase):
    def test_delete_only_requested_raid_sheet_by_exact_number(self):
        session = SimpleNamespace(close=Mock())
        listed = {
            "Raid #1 - Wed 09 Dec 2026": {"sheetId": 101},
            "Raid #10 - Wed 16 Dec 2026": {"sheetId": 110},
            "Forever Gear": {"sheetId": 210},
            "Forever Roster": {"sheetId": 220},
            "RM - Raid History": {"sheetId": 230},
        }
        with patch.object(sheets.raid_sheets, "configured", return_value=True), \
             patch.object(sheets.raid_sheets, "_session", return_value=session), \
             patch.object(sheets, "_list_tabs", return_value=listed), \
             patch.object(sheets.raid_sheets, "_json", return_value={}) as api:
            self.assertTrue(sheets.delete_raid_tab(1))
            self.assertEqual(api.call_count, 1)
            call = api.call_args.kwargs
            self.assertEqual(call["json"]["requests"], [
                {"deleteSheet": {"sheetId": 101}}
            ])
            session.close.assert_called_once()

    def test_no_match_does_not_delete_any_other_tabs(self):
        with patch.object(sheets.raid_sheets, "configured", return_value=True), \
             patch.object(sheets.raid_sheets, "_session", return_value=Mock()), \
             patch.object(sheets, "_list_tabs", return_value={
                 "Raid #10 - Wed 16 Dec 2026": {"sheetId": 110},
             }), \
             patch.object(sheets.raid_sheets, "_json") as api:
            self.assertFalse(sheets.delete_raid_tab(1))
            api.assert_not_called()

    def test_duplicate_matching_tabs_cannot_be_deleted_automatically(self):
        with patch.object(sheets.raid_sheets, "configured", return_value=True), \
             patch.object(sheets.raid_sheets, "_session", return_value=Mock()), \
             patch.object(sheets, "_list_tabs", return_value={
                 "Raid #1 - Wed 09 Dec 2026": {"sheetId": 101},
                 "Raid #1 - Wed 16 Dec 2026": {"sheetId": 102},
             }), \
             patch.object(sheets.raid_sheets, "_json") as api:
            with self.assertRaisesRegex(RuntimeError, "More than one"):
                sheets.delete_raid_tab(1)
            api.assert_not_called()


class RaidSyncPermissionTests(unittest.IsolatedAsyncioTestCase):
    def test_only_owner_and_only_ogre_or_raid_signup_channel(self):
        cog = raid.RaidPrototype(SimpleNamespace())
        with patch.object(raid, "OWNER_ID", 42), \
             patch.object(raid, "GUILD_ID", 77):
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.RAID_CHANNEL_ID
            )
            self.assertTrue(cog.allowed_sync(interaction))
            interaction.channel_id = raid.TEST_CHANNEL_ID
            self.assertTrue(cog.allowed_sync(interaction))
            interaction.channel_id = 555
            self.assertFalse(cog.allowed_sync(interaction))
            interaction.channel_id = raid.RAID_CHANNEL_ID
            interaction.user.id = 43
            self.assertFalse(cog.allowed_sync(interaction))
            interaction.user.id = 42
            interaction.guild_id = 78
            self.assertFalse(cog.allowed_sync(interaction))

    async def test_sync_command_from_raid_channel_is_private(self):
        cog = raid.RaidPrototype(SimpleNamespace())
        cog.sync_raid_sheets = AsyncMock(return_value=({"raid_tabs": 2}, {1}))
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), guild_id=77,
            channel_id=raid.RAID_CHANNEL_ID,
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch.object(raid, "OWNER_ID", 42), \
             patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid.raid_sheets, "configured", return_value=True):
            await cog.sync.callback(cog, interaction)
        cog.sync_raid_sheets.assert_awaited_once()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertIn("Ogre synced", interaction.followup.send.await_args.args[0])

    def test_wowsims_precedes_optional_check_link(self):
        event = {
            "raid_id": 1,
            "raid_at": "2026-12-09T21:00:00+00:00",
            "instance_1": "Hyjal (20m)",
            "instance_2": "Barrows (10m)",
            "instance_3": None,
            "check_message_url":
                "https://discord.com/channels/1111111111111111/"
                "2222222222222222/3333333333333333",
        }
        intro = schedule.raid_panel_intro(event)
        self.assertLess(intro.index("**WoWSims:**"),
                        intro.index("**Check:**"))


class RaidDeleteSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sheet_failure_preserves_raid_and_discord_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            schedule.record_panel(path, 1, raid.RAID_CHANNEL_ID, 999)
            panel = SimpleNamespace(
                author=SimpleNamespace(id=555), delete=AsyncMock()
            )
            channel = Mock(spec=discord.TextChannel)
            channel.guild = SimpleNamespace(id=77)
            channel.fetch_message = AsyncMock(return_value=panel)
            bot = SimpleNamespace(
                user=SimpleNamespace(id=555),
                get_channel=lambda _id: channel,
            )
            cog = raid.RaidPrototype(bot)
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(defer=AsyncMock(),
                                         send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            with patch.object(raid, "RAID_DB", path), \
                 patch.object(raid, "OWNER_ID", 42), \
                 patch.object(raid, "GUILD_ID", 77), \
                 patch.object(raid.raid_sheets, "configured", return_value=True), \
                 patch.object(raid.raid_sheets_v3, "delete_raid_tab",
                              side_effect=RuntimeError("Sheets offline")):
                await cog.delete_raid_from_poster(interaction, 1)
            self.assertIsNotNone(schedule.read_raid(path, 1))
            panel.delete.assert_not_awaited()
            self.assertIn("Sheets offline",
                          interaction.followup.send.await_args.args[0])

    async def test_delete_sheet_then_discord_and_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            schedule.record_panel(path, 1, raid.RAID_CHANNEL_ID, 999)
            panel = SimpleNamespace(
                author=SimpleNamespace(id=555), delete=AsyncMock()
            )
            channel = Mock(spec=discord.TextChannel)
            channel.guild = SimpleNamespace(id=77)
            channel.fetch_message = AsyncMock(return_value=panel)
            bot = SimpleNamespace(
                user=SimpleNamespace(id=555),
                get_channel=lambda _id: channel,
            )
            cog = raid.RaidPrototype(bot)
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                message=SimpleNamespace(edit=AsyncMock()),
                response=SimpleNamespace(defer=AsyncMock(),
                                         send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            with patch.object(raid, "RAID_DB", path), \
                 patch.object(raid, "OWNER_ID", 42), \
                 patch.object(raid, "GUILD_ID", 77), \
                 patch.object(raid.raid_sheets, "configured", return_value=True), \
                 patch.object(raid.raid_sheets_v3, "delete_raid_tab",
                              return_value=True) as delete_tab:
                await cog.delete_raid_from_poster(interaction, 1)
            delete_tab.assert_called_once_with(1)
            panel.delete.assert_awaited_once()
            self.assertIsNone(schedule.read_raid(path, 1))
            self.assertIn("Google Sheets raid tab was deleted",
                          interaction.followup.send.await_args.args[0])
