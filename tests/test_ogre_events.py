"""Ogre's rest day, coin drop and durable ledger regression tests."""
import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from hyped_ogre.features.rolls.coins import (
    ensure_coin_schema, coin_total, coin_standings,
)
from hyped_ogre.features.rolls.ogre_events import (
    previous_day_ogre_won, reserve_ogre_event, register_drop,
    claim_drop, is_unclaimed_drop,
)

UK = ZoneInfo("Europe/London")


class OgreEventsTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.execute(
            "CREATE TABLE rolls (user_id INTEGER, value INTEGER, "
            "actor_type TEXT, rolled_at INTEGER)"
        )
        ensure_coin_schema(self.con)
        # Ogre starts with one legitimately earned coin, so a drop is a
        # transfer rather than an extra coin created for a player.
        self.con.execute(
            """INSERT INTO roll_coin_awards
               (guild_id, roll_date, user_id, username, winning_roll,
                source, awarded_at, coins_awarded)
               VALUES (1, '2026-11-19', 0, 'Hyped Ogre', 99,
                       'rolls', 1795132800, 1)"""
        )
        self.addCleanup(self.con.close)
        self.now = datetime(2026, 11, 20, 12, tzinfo=UK)
        self.yesterday = datetime(2026, 11, 19, 12, tzinfo=UK)

    def roll(self, uid, value, offset=0, kind="user"):
        self.con.execute(
            "INSERT INTO rolls VALUES (?, ?, ?, ?)",
            (uid, value, kind, int(self.yesterday.timestamp()) + offset),
        )

    def test_ogre_wins_previous_day_and_takes_day_off(self):
        self.roll(15, 89)
        self.roll(0, 95, 5, "bot")
        self.assertTrue(previous_day_ogre_won(self.con, self.now))

    def test_human_gamble_can_cancel_ogre_rest(self):
        self.roll(0, 96, 0, "bot")
        self.roll(15, 12, 5)
        self.roll(15, 97, 10, "gamble")
        self.assertFalse(previous_day_ogre_won(self.con, self.now))

    def test_tie_uses_first_roll(self):
        self.roll(0, 90, 0, "bot")
        self.roll(15, 90, 5)
        self.assertTrue(previous_day_ogre_won(self.con, self.now))

    def test_member_first_tie_wins(self):
        self.roll(15, 90)
        self.roll(0, 90, 5, "bot")
        self.assertFalse(previous_day_ogre_won(self.con, self.now))

    def test_rest_event_only_once_per_day(self):
        self.assertTrue(reserve_ogre_event(self.con, 1, "2026-11-20", "rest"))
        self.assertFalse(reserve_ogre_event(self.con, 1, "2026-11-20", "drop"))

    def test_only_first_claim_gets_one_coin_and_leaderboard_updates(self):
        self.assertEqual(coin_total(self.con, 1, 0), 1)
        self.assertTrue(register_drop(self.con, 1, "2026-11-20", 123))
        self.assertEqual(coin_total(self.con, 1, 0), 0)
        self.assertTrue(is_unclaimed_drop(self.con, 1, 123))
        self.assertTrue(claim_drop(self.con, 1, 123, 42, "First", self.now))
        self.assertFalse(claim_drop(self.con, 1, 123, 43, "Second", self.now))
        self.assertFalse(is_unclaimed_drop(self.con, 1, 123))
        self.assertEqual(coin_total(self.con, 1, 42), 1)
        self.assertEqual(coin_total(self.con, 1, 43), 0)
        self.assertEqual(coin_total(self.con, 1, 0), 0)
        self.assertEqual(
            coin_total(self.con, 1, 0) + coin_total(self.con, 1, 42), 1
        )
        self.assertEqual(
            coin_standings(self.con, 1),
            [{"rank": 1, "user_id": 42, "coins": 1}],
        )

    def test_repeated_registration_never_resets_claim(self):
        self.assertTrue(register_drop(self.con, 1, "2026-11-20", 123))
        self.assertTrue(claim_drop(self.con, 1, 123, 42, "First"))
        self.assertFalse(register_drop(self.con, 1, "2026-11-20", 123))
        self.assertFalse(claim_drop(self.con, 1, 123, 43, "Second"))

    def test_no_balance_cannot_drop_or_go_negative(self):
        self.con.execute("DELETE FROM roll_coin_awards")
        self.assertFalse(register_drop(self.con, 1, "2026-11-20", 900))
        self.assertFalse(is_unclaimed_drop(self.con, 1, 900))
        self.assertEqual(coin_total(self.con, 1, 0), 0)

    def test_unclaimed_drop_is_already_out_of_ogres_ledger(self):
        self.assertTrue(register_drop(self.con, 1, "2026-11-20", 123))
        self.assertEqual(coin_total(self.con, 1, 0), 0)
        self.assertEqual(coin_standings(self.con, 1), [])
        self.assertFalse(register_drop(self.con, 1, "2026-11-20", 124))

    def test_not_claimable_without_posted_drop(self):
        self.assertFalse(claim_drop(self.con, 1, 888, 42, "First"))


if __name__ == "__main__":
    unittest.main()
