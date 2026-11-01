"""Offline migration checks for the Flameboard listener Cog."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from discord.ext import commands

from cogs import flameboard
from hyped_ogre.features.flameboard import events


class FlameboardModuleMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_class_and_listener_identity(self):
        self.assertIs(flameboard, events)
        self.assertIs(importlib.import_module("cogs.flameboard"), events)
        self.assertIs(flameboard.FlameboardEvents, events.FlameboardEvents)
        self.assertTrue(issubclass(events.FlameboardEvents, commands.Cog))

        called = AsyncMock()
        cog = flameboard.FlameboardEvents(
            bot=SimpleNamespace(), guild_id=77, sync_message=called,
        )
        await cog.on_raw_reaction_add(
            SimpleNamespace(guild_id=42, emoji="🔥", channel_id=1, message_id=2)
        )
        await cog.on_raw_reaction_add(
            SimpleNamespace(guild_id=77, emoji="⚠", channel_id=1, message_id=2)
        )
        called.assert_not_awaited()
        await cog.on_raw_reaction_add(
            SimpleNamespace(guild_id=77, emoji="🔥", channel_id=5, message_id=6)
        )
        await cog.on_raw_reaction_remove(
            SimpleNamespace(guild_id=77, emoji="😂", channel_id=7, message_id=8)
        )
        self.assertEqual(called.await_count, 2)
        called.assert_any_await(5, 6)
        called.assert_any_await(7, 8)


if __name__ == "__main__":
    unittest.main()
