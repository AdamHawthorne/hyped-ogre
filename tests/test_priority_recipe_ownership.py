"""Priority recipe list and proof of ownership from Blizzard known recipes."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hyped_ogre.features.characters import crafting, priority_recipes


class PriorityRecipesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "guild.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY,
                    discord_id INTEGER,
                    name TEXT,
                    roster_visible INTEGER,
                    is_main INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY,
                    hidden INTEGER);
            """)
            con.executemany("INSERT INTO characters VALUES (?,?,?,?,?)", [
                (1, 100, "Known Enchanter", 1, 1),
                (2, 101, "Hidden Enchanter", 1, 1),
                (3, 102, "Unlisted Enchanter", 0, 1),
            ])
            con.execute(
                "INSERT INTO character_board_overrides VALUES (2,1)"
            )
        crafting.initialise(self.path)

    def test_priority_lists_cover_every_profession(self):
        self.assertEqual(len(priority_recipes.PRIORITIES), 12)
        for name in (
            "Alchemy", "Blacksmithing", "Enchanting",
            "Engineering", "Leatherworking", "Tailoring",
        ):
            self.assertGreaterEqual(
                len(priority_recipes.PRIORITIES[name]), 10
            )
        self.assertIn(
            "Enchant Weapon - Crusader",
            priority_recipes.PRIORITIES["Enchanting"],
        )
        self.assertIn("Lionheart Helm",
                      priority_recipes.PRIORITIES["Blacksmithing"])

    def test_confirmed_vs_no_record_vs_not_indexed(self):
        crafting.save_catalog(self.path, [
            {"id": 20034, "name": "Enchant Weapon - Crusader",
             "profession": "Enchanting"},
            {"id": 20001, "name": "Enchant Weapon - Spell Power",
             "profession": "Enchanting"},
        ])
        for char_id in (1,2,3):
            crafting.save_profile(self.path, char_id, {
                "primaries": [{
                    "profession": {"name": "Enchanting"},
                    "skill_points": 300,
                    "known_recipes": [{"id": 20034,
                                       "name": "Enchant Weapon - Crusader"}],
                }],
                "secondaries": [],
            })
        results = priority_recipes.audit(self.path, "Enchanting")
        self.assertEqual(results[0]["status"], "confirmed")
        self.assertEqual(results[0]["members"], [
            {"discord_id": 100, "character_name": "Known Enchanter"}
        ])
        self.assertEqual(results[1]["status"], "not_recorded")
        self.assertEqual(results[2]["status"], "not_indexed")
        text = priority_recipes.format_audit(results, "Enchanting")
        self.assertIn("<@100>", text)
        self.assertNotIn("<@101>", text)
        self.assertNotIn("<@102>", text)
        self.assertIn("Still hunting for a guild crafter", text)
        self.assertIn("Ogre hasn't found this in the recipe book yet", text)
        self.assertLess(len(text), 2000)

    def test_exact_match_does_not_count_similar_recipe(self):
        crafting.save_catalog(self.path, [{
            "id": 9999, "name": "Enchant Weapon - Crusader Training",
            "profession": "Enchanting",
        }])
        crafting.save_profile(self.path, 1, {
            "primaries": [{
                "profession": {"name": "Enchanting"},
                "skill_points": 300,
                "known_recipes": [
                    {"id": 9999, "name": "Enchant Weapon - Crusader Training"}
                ],
            }], "secondaries": [],
        })
        self.assertEqual(
            priority_recipes.audit(self.path, "Enchanting")[0]["status"],
            "not_indexed",
        )


if __name__ == "__main__":
    unittest.main()
