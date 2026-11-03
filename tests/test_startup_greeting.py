"""Private randomized startup greeting: no reconnection spam or public posts."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from hyped_ogre.features.startup_greeting import (
    OGRE_CONTROL_CHANNEL_ID,
    OGRE_WAKEUPS,
    StartupGreeting,
)


GUILD_ID = 451518969417957376


class StartupGreetingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = OGRE_CONTROL_CHANNEL_ID
        self.channel.guild = SimpleNamespace(id=GUILD_ID)
        self.channel.send = AsyncMock()
        self.bot = SimpleNamespace(
            get_channel=MagicMock(return_value=self.channel),
            fetch_channel=AsyncMock(return_value=self.channel),
        )
        self.greeting = StartupGreeting(GUILD_ID)

    async def test_private_channel_and_no_pings(self):
        with patch("hyped_ogre.features.startup_greeting.secrets.choice",
                   return_value="Oi! Ogre back again."):
            self.assertTrue(await self.greeting.announce_once(self.bot))
        self.bot.get_channel.assert_called_once_with(1564594901746716713)
        self.bot.fetch_channel.assert_not_awaited()
        self.channel.send.assert_awaited_once()
        args = self.channel.send.await_args
        self.assertEqual(args.args, ("Oi! Ogre back again.",))
        mentions = args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

    async def test_on_ready_twice_does_not_send_again(self):
        self.assertTrue(await self.greeting.announce_once(self.bot))
        self.assertFalse(await self.greeting.announce_once(self.bot))
        self.channel.send.assert_awaited_once()

    async def test_concurrent_ready_calls_are_deduplicated(self):
        import asyncio
        states = await asyncio.gather(
            self.greeting.announce_once(self.bot),
            self.greeting.announce_once(self.bot),
            self.greeting.announce_once(self.bot),
        )
        self.assertEqual(states.count(True), 1)
        self.channel.send.assert_awaited_once()

    async def test_missing_cache_uses_fixed_channel_lookup(self):
        self.bot.get_channel.return_value = None
        self.assertTrue(await self.greeting.announce_once(self.bot))
        self.bot.fetch_channel.assert_awaited_once_with(OGRE_CONTROL_CHANNEL_ID)
        self.channel.send.assert_awaited_once()

    async def test_never_posts_in_another_guild(self):
        self.channel.guild.id = 999
        self.assertFalse(await self.greeting.announce_once(self.bot))
        self.channel.send.assert_not_awaited()

    async def test_network_error_does_not_crash_startup_or_retry(self):
        self.channel.send.side_effect = discord.Forbidden(
            MagicMock(status=403, reason="Forbidden"), "Missing Access",
        )
        self.assertFalse(await self.greeting.announce_once(self.bot))
        self.assertFalse(await self.greeting.announce_once(self.bot))
        self.assertEqual(self.channel.send.await_count, 1)

    def test_randomised_responses_are_short_ogre_speak(self):
        self.assertGreaterEqual(len(OGRE_WAKEUPS), 8)
        self.assertEqual(len(OGRE_WAKEUPS), len(set(OGRE_WAKEUPS)))
        for line in OGRE_WAKEUPS:
            self.assertTrue(10 <= len(line) <= 150)
            self.assertNotIn("@everyone", line)
            self.assertNotIn("@here", line)

    async def test_disabled_when_guild_is_not_configured(self):
        self.assertFalse(await StartupGreeting(0).announce_once(self.bot))
        self.channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
