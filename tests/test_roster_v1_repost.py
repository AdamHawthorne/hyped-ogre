"""Private tests for the V1 repost/ping workflow.

The public roster must never ping members on ordinary refreshes, and a
manual repost must retire the previously tracked board and announcement.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from cogs import raid_signup as raid
from hyped_ogre.features.characters import hub
from hyped_ogre.features.classrooms.config import GUILD_ID


class FakeChannel:
    def __init__(self, guild):
        self.id = 321321
        self.guild = guild
        self.send = AsyncMock()
        self.get_partial_message = MagicMock()
        self.permissions_for = MagicMock(
            return_value=SimpleNamespace(mention_everyone=True)
        )


class PostV1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raids.db")
        raid.create_schema(self.path)
        with raid.database(self.path) as con:
            con.execute(
                "CREATE TABLE character_panels ("
                "guild_id INTEGER NOT NULL, channel_id INTEGER PRIMARY KEY,"
                "message_id INTEGER NOT NULL,page_index INTEGER NOT NULL DEFAULT 0)"
            )
            con.execute(
                "CREATE TABLE character_panel_parts ("
                "channel_id INTEGER NOT NULL,position INTEGER NOT NULL,"
                "message_id INTEGER NOT NULL,PRIMARY KEY(channel_id,position))"
            )
            con.execute(
                "CREATE TABLE character_panel_announcements ("
                "channel_id INTEGER PRIMARY KEY,message_id INTEGER NOT NULL)"
            )
        role = SimpleNamespace(
            name="Forever", mention="<@&777>", mentionable=True
        )
        self.guild = SimpleNamespace(
            owner_id=42, roles=[role], me=SimpleNamespace()
        )
        self.channel = FakeChannel(self.guild)
        self.interaction = SimpleNamespace(
            guild_id=GUILD_ID,
            guild=self.guild,
            user=SimpleNamespace(id=42, roles=[]),
            channel=self.channel,
            response=SimpleNamespace(
                defer=AsyncMock(), send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog = hub.CharacterHub(SimpleNamespace())
        self.patches = [
            patch.object(hub.raid, "RAID_DB", self.path),
            patch.object(hub.discord, "TextChannel", FakeChannel),
            patch.object(hub.armory, "refresh", new_callable=AsyncMock),
            patch.object(hub, "rows", return_value=[]),
            patch.object(hub, "render", return_value=[discord.Embed()]),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.old_message = SimpleNamespace(
            delete=AsyncMock(), edit=AsyncMock()
        )
        self.channel.get_partial_message.return_value = self.old_message

    def messages(self, ids):
        self.channel.send.side_effect = [
            SimpleNamespace(id=number, delete=AsyncMock())
            for number in ids
        ]

    async def call(self, *, repost=False, ping_forever=True):
        await hub.CharacterHub.post_characters.callback(
            self.cog, self.interaction,
            repost=repost, ping_forever=ping_forever,
        )

    def saved(self):
        with raid.database(self.path) as con:
            board = con.execute(
                "SELECT message_id FROM character_panels "
                "WHERE channel_id=?", (self.channel.id,),
            ).fetchone()
            notification = con.execute(
                "SELECT message_id FROM character_panel_announcements "
                "WHERE channel_id=?", (self.channel.id,),
            ).fetchone()
            return (board[0] if board else None,
                    notification[0] if notification else None)

    async def test_new_post_pings_once_and_refresh_never_pings(self):
        self.messages([1001, 1002])
        await self.call()
        self.assertEqual(self.saved(), (1002, 1001))
        self.assertEqual(self.channel.send.await_count, 2)
        notice = self.channel.send.await_args_list[0]
        self.assertIn("<@&777> Ogre wants yer main!", notice.kwargs["content"])
        self.assertIn("**Rostered**", notice.kwargs["content"])
        self.assertIn("raid sign-ups", notice.kwargs["content"])
        self.assertEqual(
            notice.kwargs["allowed_mentions"].roles, [self.guild.roles[0]]
        )

        # The follow-up /postcharacters without options edits the old board.
        with patch.object(self.cog, "refresh", new_callable=AsyncMock) as refresh:
            await self.call()
        refresh.assert_awaited_once()
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertEqual(self.saved(), (1002, 1001))

    async def test_repost_replaces_old_board_and_announcement(self):
        self.messages([1001, 1002])
        await self.call()
        self.messages([2001, 2002])
        await self.call(repost=True)
        self.assertEqual(self.saved(), (2002, 2001))
        self.assertEqual(self.channel.send.await_count, 4)
        removed = [call.args[0] for call in self.channel.get_partial_message.call_args_list]
        self.assertIn(1001, removed)
        self.assertIn(1002, removed)
        self.assertEqual(self.old_message.delete.await_count, 2)

    async def test_repost_without_ping_removes_old_announcement(self):
        self.messages([1001, 1002])
        await self.call()
        self.messages([3002])
        await self.call(repost=True, ping_forever=False)
        self.assertEqual(self.saved(), (3002, None))
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertIn(1001, [
            call.args[0] for call in self.channel.get_partial_message.call_args_list
        ])

    async def test_missing_role_rejects_new_announcement_without_writes(self):
        self.guild.roles = []
        await self.call()
        self.assertEqual(self.saved(), (None, None))
        self.channel.send.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()
        self.assertIn("@Forever", self.interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
