"""Custom :check: emoji and completion tracking, entirely in private Ogre."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs import raid_signup as raid
from hyped_ogre.features.characters import professions_channel as feature


class CustomCheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.emoji = SimpleNamespace(name="check", id=9999)
        self.guild = SimpleNamespace(
            id=feature.GUILD_ID, emojis=[self.emoji])
        self.bot = MagicMock()
        self.bot.user.id = 77777
        self.bot.get_guild.return_value = self.guild
        self.board = feature.ProfessionBoard(self.bot)

    async def test_new_crafting_request_uses_custom_emoji_not_unicode(self):
        reply = SimpleNamespace(id=5500)
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            guild=self.guild,
            channel=SimpleNamespace(id=raid.TEST_CHANNEL_ID),
            content="Can anyone do +100 hp on chest?",
            id=123,
            created_at=SimpleNamespace(timestamp=lambda: 1795460000),
            reply=AsyncMock(return_value=reply),
            add_reaction=AsyncMock(),
        )
        with (
            patch.object(feature.crafting, "channel", return_value=(
                raid.TEST_CHANNEL_ID, 8888)),
            patch.object(feature, "answer", return_value="Matched recipe"),
            patch.object(feature.crafting, "store_request") as saved,
        ):
            await self.board.on_message(message)
        message.add_reaction.assert_awaited_once_with(self.emoji)
        message.reply.assert_awaited_once()
        self.assertEqual(saved.call_args.kwargs["reply_id"], 5500)

    async def test_completion_requires_matching_custom_check_id(self):
        payload = SimpleNamespace(
            guild_id=feature.GUILD_ID,
            channel_id=raid.TEST_CHANNEL_ID,
            user_id=12345,
            message_id=123,
            emoji=SimpleNamespace(id=9999),
        )
        with (
            patch.object(feature.crafting, "request_exists", return_value=True),
            patch.object(feature.crafting, "resolve_request") as resolved,
        ):
            await self.board.on_raw_reaction_add(payload)
            self.assertEqual(resolved.call_count, 1)
            payload.emoji.id = 888
            await self.board.on_raw_reaction_add(payload)
            self.assertEqual(resolved.call_count, 1)


if __name__ == "__main__":
    unittest.main()
