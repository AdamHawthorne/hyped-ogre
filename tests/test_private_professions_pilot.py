"""Safety checks for explicitly approved public professions release."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs import raid_signup as raid
from hyped_ogre.features.characters import professions_channel as feature


class PublicProfessionsTests(unittest.IsolatedAsyncioTestCase):
    def build(self, channels, *, current=None, visible=False):
        overwrite = SimpleNamespace(view_channel=visible)
        guild = SimpleNamespace(
            text_channels=channels, default_role=MagicMock(), id=feature.GUILD_ID
        )
        for channel in channels:
            channel.name = feature.CHANNEL_NAME
            channel.overwrites_for = MagicMock(return_value=overwrite)
            channel.set_permissions = AsyncMock()
            channel.delete = AsyncMock()
        bot = SimpleNamespace(get_guild=lambda _: guild)
        board = feature.ProfessionBoard(bot)
        return board, channels, overwrite

    async def test_existing_hidden_channel_reopened_without_deletion(self):
        chan = SimpleNamespace(id=333)
        board, _, overwrite = self.build([chan], visible=False)
        with (
            patch.object(feature.crafting, "channel", return_value=(raid.TEST_CHANNEL_ID, 88)),
            patch.object(feature.crafting, "set_channel") as update,
        ):
            self.assertTrue(await board.activate_existing_public_channel())
        self.assertTrue(overwrite.view_channel)
        chan.set_permissions.assert_awaited_once()
        chan.delete.assert_not_awaited()
        self.assertEqual(update.call_args.args[-2:], (333, 0))

    async def test_public_channel_never_recreated_if_already_selected(self):
        chan = SimpleNamespace(id=333)
        board, _, overwrite = self.build([chan], visible=False)
        with (
            patch.object(feature.crafting, "channel", return_value=(333, 999)),
            patch.object(feature.crafting, "set_channel") as update,
        ):
            self.assertTrue(await board.activate_existing_public_channel())
        self.assertTrue(overwrite.view_channel)
        update.assert_not_called()
        chan.delete.assert_not_awaited()

    async def test_no_public_channel_never_creates_or_deletes(self):
        board, _, _ = self.build([])
        with patch.object(feature.crafting, "channel", return_value=None):
            self.assertFalse(await board.activate_existing_public_channel())
        self.assertFalse(hasattr(board, "ensure_channel"))

    async def test_duplicate_channel_names_do_not_select_one_at_random(self):
        one, two = SimpleNamespace(id=333), SimpleNamespace(id=444)
        board, _, _ = self.build([one, two])
        with patch.object(feature.crafting, "channel", return_value=None):
            self.assertFalse(await board.activate_existing_public_channel())
        one.set_permissions.assert_not_awaited()
        two.set_permissions.assert_not_awaited()
        one.delete.assert_not_awaited()
        two.delete.assert_not_awaited()

    async def test_legacy_test_channel_is_not_publicly_selected(self):
        chan = SimpleNamespace(id=raid.TEST_CHANNEL_ID)
        board, _, _ = self.build([chan])
        with patch.object(feature.crafting, "channel", return_value=None):
            self.assertFalse(await board.activate_existing_public_channel())


if __name__ == "__main__":
    unittest.main()
