"""Daily member-only role eligibility must respect Ogre's coin-winning roll."""
import sqlite3
import unittest
from hyped_ogre.features.rolls.crown import eligible_lucky_gamer


class LuckyGamerTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE rolls (user_id INTEGER, value INTEGER, "
            "actor_type TEXT, rolled_at INTEGER)"
        )
        self.addCleanup(self.con.close)

    def add(self, uid, score, at, kind="user"):
        self.con.execute("INSERT INTO rolls VALUES (?,?,?,?)",
                         (uid, score, kind, at))

    def champion(self):
        return eligible_lucky_gamer(self.con, 0, 86400)

    def test_ogre_highest_blocks_member_role(self):
        self.add(45, 80, 1)
        self.add(0, 90, 2, "bot")
        self.assertIsNone(self.champion())

    def test_member_beats_ogre(self):
        self.add(0, 90, 1, "bot")
        self.add(45, 95, 2)
        self.assertEqual(self.champion(), {"user_id": 45, "value": 95})

    def test_first_roll_wins_tie_against_ogre(self):
        self.add(45, 90, 1)
        self.add(0, 90, 2, "bot")
        self.assertEqual(self.champion()["user_id"], 45)

    def test_ogre_first_roll_wins_tie_no_role(self):
        self.add(0, 90, 1, "bot")
        self.add(45, 90, 2)
        self.assertIsNone(self.champion())

    def test_gamble_is_eligible_if_it_beats_ogre(self):
        self.add(45, 5, 1)
        self.add(0, 90, 2, "bot")
        self.add(45, 91, 3, "gamble")
        self.assertEqual(self.champion()["value"], 91)

    def test_outside_day_ignored(self):
        self.add(45, 100, 100000)
        self.add(0, 50, 2, "bot")
        self.assertIsNone(self.champion())

    def test_no_rolls_no_role(self):
        self.assertIsNone(self.champion())


if __name__ == "__main__":
    unittest.main()
