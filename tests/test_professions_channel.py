"""Offline safety tests for verified Forever recipe ownership and guild board."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hyped_ogre.features.characters import crafting, craft_catalog, professions_channel as channel


class CraftingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "test.db")
        with sqlite3.connect(self.path) as con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                    name TEXT, roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY, hidden INTEGER);
            """)
            con.executemany(
                "INSERT INTO characters VALUES (?,?,?,?)",
                [(1, 100, "Big Snore", 1),
                 (2, 101, "Alch Ogre", 1),
                 (3, 102, "Secret Alt", 1),
                 (4, 103, "LFG Only", 0)],
            )
            con.execute("INSERT INTO character_board_overrides VALUES (3,1)")
        crafting.initialise(self.path)

    def payload(self, *, skill=300, recipes=None):
        return {"primaries": [
            {"profession": {"name": "Alchemy"},
             "tiers": [{"skill_points": skill,
                        "known_recipes": recipes if recipes is not None
                        else [{"id": 17187, "name": "Transmute: Arcanite"}]}]},
        ], "secondaries": [
            {"profession": {"name": "Cooking"},
             "tiers": [{"skill_points": 150, "known_recipes": []}]},
        ]}

    def test_only_verified_known_recipes_count(self):
        self.assertTrue(crafting.save_profile(self.path, 2, self.payload()))
        grouped = crafting.leaderboard(self.path)
        self.assertEqual(len(grouped["Alchemy"]), 1)
        self.assertEqual(grouped["Alchemy"][0]["recipe_count"], 1)
        self.assertNotIn("Cooking", grouped)
        result = crafting.query(self.path, "transmute arcanite")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["crafters"][0]["name"], "Alch Ogre")

    def test_missing_known_recipes_does_not_erase_ownership(self):
        crafting.save_profile(self.path, 2, self.payload())
        crafting.save_profile(
            self.path, 2,
            {"primaries": [{"profession": {"name": "Alchemy"},
                            "tiers": [{"skill_points": 301}]}]},
        )
        self.assertEqual(crafting.leaderboard(self.path)["Alchemy"][0]["recipe_count"], 1)

    def test_full_snapshot_retires_dropped_profession(self):
        crafting.save_profile(self.path, 2, self.payload())
        crafting.save_profile(
            self.path, 2,
            {"primaries": [], "secondaries": []},
        )
        self.assertNotIn("Alchemy", crafting.leaderboard(self.path))
        self.assertEqual(crafting.query(self.path, "transmute arcanite")["crafters"], [])

    def test_incomplete_payload_does_not_retire_profession(self):
        crafting.save_profile(self.path, 2, self.payload())
        self.assertFalse(crafting.save_profile(self.path, 2, {}))
        self.assertEqual(crafting.leaderboard(self.path)["Alchemy"][0]["recipe_count"], 1)

    def test_explicit_empty_recipe_list_clears_previous_snapshot(self):
        crafting.save_profile(self.path, 2, self.payload())
        crafting.save_profile(self.path, 2, self.payload(recipes=[]))
        self.assertEqual(crafting.leaderboard(self.path)["Alchemy"][0]["recipe_count"], 0)
        self.assertEqual(crafting.query(self.path, "transmute arcanite")["crafters"], [])

    def test_hide_and_lfg_only_excluded_even_when_recipe_known(self):
        for ident in (2, 3, 4):
            crafting.save_profile(self.path, ident, self.payload())
        grouped = crafting.leaderboard(self.path)
        self.assertEqual([p["name"] for p in grouped["Alchemy"]], ["Alch Ogre"])
        self.assertEqual(len(crafting.query(self.path, "transmute arcanite")["crafters"]), 1)

    def test_official_catalog_and_curated_source(self):
        crafting.save_catalog(
            self.path,
            [{"id": 17187, "name": "Transmute: Arcanite",
              "profession": "Alchemy"},
             {"id": 200, "name": "Enchant Weapon - Crusader",
              "profession": "Enchanting"}],
        )
        result = crafting.query(self.path, "Arcanite Bar")
        self.assertEqual(result["status"], "found")
        self.assertTrue(result["recipes"][0]["verified"])
        self.assertIn("Pestlezugg", channel.answer(self.path, "Arcanite Bar"))
        self.assertIn("not verified", channel.answer(self.path, "Crusader"))

    def test_top_crafters_sorted_by_recipes_not_skill(self):
        crafting.save_profile(self.path, 1, self.payload(
            skill=300, recipes=[{"id": 10, "name": "Recipe A"}]))
        crafting.save_profile(self.path, 2, self.payload(
            skill=250, recipes=[{"id": 10, "name": "Recipe A"},
                                {"id": 11, "name": "Recipe B"}]))
        result = crafting.leaderboard(self.path)["Alchemy"]
        self.assertEqual([r["name"] for r in result], ["Alch Ogre", "Big Snore"])
        board = channel.format_crafters(crafting.leaderboard(self.path))
        self.assertIn("2 recipes", str(board.fields[0].value))

    def test_natural_language_asks_only_crafting_questions(self):
        examples = {
            "Can anyone do arcanite bar?": "arcanite bar",
            "Who can craft Crusader?": "Crusader",
            "Who can enchant Crusader?": "Crusader",
            "Who can make Arcanite Bar?": "Arcanite Bar",
            "Who can do enchant Crusader?": "Crusader",
            "Anyone know Crusader?": "Crusader",
            "Does anyone have enchant Crusader?": "Crusader",
            "Ogre, can anyone transmute Arcanite Bar?": "Arcanite Bar",
            "Need someone to make Arcanite Bar": "Arcanite Bar",
            "Looking for a crafter for Arcanite Bar?": "Arcanite Bar",
            "Crusader enchant?": "Crusader enchant",
            "Can anyone do +100 hp on chest?": "+100 hp on chest",
            "Who has 100hp chest?": "100hp chest",
            "Chest: Enchant Chest - Major Health": "Chest - Major Health",
            "LF a crafter for +100 HP chest": "+100 HP chest",
            "Anyone for Crusader?": "Crusader",
            "Can we get Arcanite Bar?": "Arcanite Bar",
        }
        for message, expected in examples.items():
            with self.subTest(message=message):
                self.assertEqual(
                    channel.extract_question(message), expected
                )
        for ordinary in (
            "Thanks for the dungeon", "Can anyone join the dungeon?",
            "How was the raid?", "Who can tank tonight?",
            "Anyone interested in PvP?",
        ):
            with self.subTest(ordinary=ordinary):
                self.assertIsNone(channel.extract_question(ordinary))

    def test_colloquial_chest_health_maps_to_verified_catalog_recipe(self):
        crafting.save_catalog(self.path, [{
            "id": 2001, "name": "Enchant Chest - Major Health",
            "profession": "Enchanting"
        }])
        for question in (
            "+100 hp on chest",
            "100hp chest",
            "chest 100 health",
            "major health chest",
        ):
            with self.subTest(question=question):
                result = crafting.query(self.path, question)
                self.assertEqual(result["status"], "found")
                self.assertEqual(
                    result["recipes"][0]["name"],
                    "Enchant Chest - Major Health",
                )
        no_owner = channel.answer(self.path, "+100 hp on chest")
        self.assertIn("Enchant Chest - Major Health", no_owner)
        self.assertIn("nobody", no_owner)

    def test_recipe_ownership_and_uncertain_suggestions_are_not_invented(self):
        crafting.save_catalog(self.path, [
            {"id": 2001, "name": "Enchant Chest - Major Health",
             "profession": "Enchanting"},
            {"id": 2002, "name": "Enchant Chest - Major Mana",
             "profession": "Enchanting"},
        ])
        unknown = crafting.query(self.path, "enchnt chest major health")
        self.assertEqual(unknown["status"], "unknown")
        self.assertIn(
            "Enchant Chest - Major Health", unknown["suggestions"]
        )
        self.assertIn("Did you mean", channel.answer(self.path, "enchnt chest major health"))
        # A verified known-recipe relationship is still required for ownership.
        with sqlite3.connect(self.path) as con:
            con.execute(
                "UPDATE craft_catalog SET name='Enchant Chest - Major Health' "
                "WHERE recipe_id=2001"
            )
        self.assertEqual(
            crafting.query(self.path, "+100 hp on chest")["crafters"], []
        )

    def test_extract_blizzard_static_nested_categories(self):
        result = craft_catalog.extract_tier(
            {"categories": [
                {"name": "Transmutations", "recipes": [
                    {"id": 17187, "name": "Transmute: Arcanite"}]}]},
            "Alchemy",
        )
        self.assertEqual(result, [{"id": 17187, "name": "Transmute: Arcanite",
                                   "profession": "Alchemy"}])


if __name__ == "__main__":
    unittest.main()
