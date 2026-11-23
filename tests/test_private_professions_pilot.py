"""Regression protection: professions never auto-create or post publicly."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs import raid_signup as raid
from hyped_ogre.features.characters import professions_channel as feature


class PrivateProfessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_bot_only_channel_is_removed_and_detached(self):
        message = SimpleNamespace(author=SimpleNamespace(bot=True))
        async def history(*, limit=None):
            yield message
        old = SimpleNamespace(
            id=333, name=feature.CHANNEL_NAME,
            topic=feature.AUTO_CREATED_TOPIC, history=history,
            set_permissions=AsyncMock(), delete=AsyncMock(),
        )
        guild = SimpleNamespace(
            text_channels=[old], default_role=MagicMock(),
            get_channel=lambda ident: old if ident == 333 else None,
        )
        bot = SimpleNamespace(get_guild=lambda ident: guild)
        board = feature.ProfessionBoard(bot)
        with (patch.object(feature.crafting, "channel", return_value=(333, 44)),
              patch.object(feature.crafting, "set_channel") as update):
            self.assertTrue(await board.retract_unapproved_channel())
        old.set_permissions.assert_awaited_once()
        old.delete.assert_awaited_once()
        self.assertEqual(
            update.call_args.args[-2:], (raid.TEST_CHANNEL_ID, 0),
        )

    async def test_public_channel_with_human_messages_is_hidden_not_deleted(self):
        async def history(*, limit=None):
            yield SimpleNamespace(author=SimpleNamespace(bot=False))
        old = SimpleNamespace(
            id=333, name=feature.CHANNEL_NAME,
            topic=feature.AUTO_CREATED_TOPIC, history=history,
            set_permissions=AsyncMock(), delete=AsyncMock(),
        )
        guild = SimpleNamespace(
            text_channels=[old], default_role=MagicMock(),
            get_channel=lambda ident: old if ident == 333 else None,
        )
        board = feature.ProfessionBoard(
            SimpleNamespace(get_guild=lambda ident: guild))
        with (patch.object(feature.crafting, "channel", return_value=(333, 44)),
              patch.object(feature.crafting, "set_channel")):
            await board.retract_unapproved_channel()
        old.set_permissions.assert_awaited_once()
        old.delete.assert_not_awaited()

    async def test_channel_rejects_old_public_destination(self):
        board = feature.ProfessionBoard(SimpleNamespace(get_channel=MagicMock()))
        with patch.object(feature.crafting, "channel", return_value=(333, 44)):
            self.assertIsNone(await board._channel())
        board.bot.get_channel.assert_not_called()

    async def test_unapproved_channel_is_never_created(self):
        bot = MagicMock()
        bot.get_guild.return_value = SimpleNamespace(text_channels=[])
        board = feature.ProfessionBoard(bot)
        with patch.object(feature.crafting, "channel", return_value=None):
            self.assertTrue(await board.retract_unapproved_channel())
        self.assertFalse(hasattr(board, "ensure_channel"))
        bot.create_text_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
