"""Same-object compatibility checks for all four roll helper modules.

Coin/gamble ledger tests and reaction tests still exercise original APIs.
"""

import importlib
import unittest
from datetime import date
from types import SimpleNamespace

import discord

from cogs import roll_coins, roll_gamble, roll_panels, roll_reactions
from hyped_ogre.features.rolls import coins, gamble, panels, reactions


class RollHelperModuleMigrationTests(unittest.TestCase):
    def test_all_four_legacy_modules_are_exact_canonical_objects(self):
        pairs = (
            ("cogs.roll_coins", roll_coins, coins),
            ("cogs.roll_gamble", roll_gamble, gamble),
            ("cogs.roll_panels", roll_panels, panels),
            ("cogs.roll_reactions", roll_reactions, reactions),
        )
        for path, old, new in pairs:
            with self.subTest(path=path):
                self.assertIs(old, new)
                self.assertIs(importlib.import_module(path), new)
        self.assertIs(gamble.coin_total, coins.coin_total)
        self.assertIs(gamble.UK, coins.UK)
        self.assertIs(panels.option_emoji, roll_panels.option_emoji)
        self.assertIs(reactions.RollReactions, roll_reactions.RollReactions)

    def test_coin_prizes_preserve_nonretroactive_sunday_double(self):
        self.assertEqual(coins.daily_coin_prize(date(2026, 10, 25)), 1)
        self.assertEqual(coins.daily_coin_prize("2026-11-01"), 2)
        self.assertEqual(coins.daily_coin_prize("2026-11-02"), 1)
        self.assertEqual(coins.daily_coin_prize(date(2026, 11, 8)), 2)
        self.assertTrue(issubclass(gamble.GambleError, ValueError))

    def test_persistent_roll_and_ledger_buttons_retain_custom_ids(self):
        async def action(_interaction):
            pass
        panel = roll_panels.DailyRollPanel(
            day="2026-11-01", winner_text="Test", tagline="Daily rolls",
            on_roll=action, on_leaderboard=action, on_stats=action,
            on_gamble=action, preview=False,
        )
        self.assertIsInstance(panel, discord.ui.LayoutView)
        self.assertTrue(panel.is_persistent())
        ids = {
            c.custom_id for c in panel.walk_children()
            if isinstance(c, discord.ui.Button)
        }
        self.assertEqual(ids, {
            "hyped_ogre_daily_roll_v2", "hyped_ogre_coin_gamble_v1",
            "hyped_ogre_coin_leaderboard", "hyped_ogre_roll_stats",
        })
        self.assertIs(panel.__class__, panels.DailyRollPanel)
        preview = panels.DailyRollPanel(
            day="2026-11-02", winner_text="Test", tagline="Preview",
            on_roll=action, on_leaderboard=action, on_stats=action,
            preview=True,
        )
        self.assertEqual(preview.timeout, 60)

    def test_reaction_coordinator_still_uses_single_lock(self):
        state = reactions.RollReactions(
            bot=SimpleNamespace(), channel_id=123,
            db=lambda: None, today_range=lambda: (0, 1),
            log_error=lambda message: None, guild_id=77,
        )
        self.assertEqual(state.channel_id, 123)
        self.assertEqual(state.guild_id, 77)
        self.assertIsInstance(state, roll_reactions.RollReactions)
        self.assertIsNotNone(state.lock)


if __name__ == "__main__":
    unittest.main()
