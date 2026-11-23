"""Private professions V2: featured recipes, tier selection and cadence."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hyped_ogre.features.characters import crafting, armory
from hyped_ogre.features.characters import professions_channel as p


class ProfessionsFeaturedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "guild.db")
        with sqlite3.connect(self.path) as con:
            con.executescript("""
                CREATE TABLE characters (
                  character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                  name TEXT, is_main INTEGER, roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                  character_id INTEGER PRIMARY KEY, hidden INTEGER);
            """)
            con.executemany("INSERT INTO characters VALUES (?,?,?,?,?)", [
                (1, 1, "First Crafter", 1, 1),
                (2, 2, "Second Crafter", 1, 1),
            ])
        crafting.initialise(self.path)
        armory.initialise(self.path)

    def test_only_curated_known_recipes_are_featured(self):
        crafting.save_profile(self.path, 1, {
            "primaries": [{
                "profession": {"name": "Alchemy"},
                "skill_points": 300,
                "known_recipes": [
                    {"id": 17187, "name": "Transmute: Arcanite"},
                    {"id": 12345, "name": "Basic Potion"},
                ],
            }],
            "secondaries": [],
        })
        crafting.save_profile(self.path, 2, {
            "primaries": [{
                "profession": {"name": "Alchemy"},
                "skill_points": 295,
                "known_recipes": [
                    {"id": 54321, "name": "Ordinary potion"},
                ],
            }],
            "secondaries": [],
        })
        people = crafting.board_rows(self.path)["Alchemy"]
        self.assertEqual(people[0]["name"], "First Crafter")
        self.assertEqual([r["featured_count"] for r in people], [1, 0])

    def test_top_three_plus_295_and_above(self):
        candidates = [
            {"name": "a", "skill": 220},
            {"name": "b", "skill": 240},
            {"name": "c", "skill": 250},
            {"name": "d", "skill": 294},
            {"name": "e", "skill": 295},
            {"name": "f", "skill": 300},
        ]
        self.assertEqual(
            [r["name"] for r in p.featured_selection("Alchemy", candidates)],
            ["a", "b", "c", "e", "f"],
        )

    def test_v2_layout_has_crafting_before_gathering_before_secondary(self):
        guild = SimpleNamespace(emojis=[])
        grouped = {
            "Cooking": [{"name": "Cook", "skill": 300,
                         "recipe_count": 15, "featured_count": None}],
            "Mining": [{"name": "Miner", "skill": 300,
                        "recipe_count": -1, "featured_count": None}],
            "Alchemy": [{"name": "Alchemist", "skill": 300,
                         "recipe_count": 12, "featured_count": 1}],
        }
        view = p.ProfessionsV2(grouped, guild)
        self.assertIsInstance(view, p.discord.ui.LayoutView)
        values = []
        for container in view.children:
            for item in getattr(container, "children", []):
                values.append(getattr(item, "content", ""))
        text = "\n".join(values)
        self.assertLess(text.index("Primary professions"), text.index("Gathering"))
        self.assertLess(text.index("Gathering"), text.index("Secondary professions"))
        self.assertIn("1 featured", text)
        self.assertIn("Miner", text)
        self.assertNotIn("15 featured", text)

    def test_icon_names_are_known_and_no_fake_discord_ids(self):
        self.assertEqual(
            crafting.PROFESSION_ICON_NAMES["Alchemy"], "ui_profession_alchemy")
        self.assertEqual(p.profession_emoji(SimpleNamespace(emojis=[]), "Alchemy"), "")

    def test_crafting_refresh_is_stale_after_fifteen_minutes(self):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO character_armory_cache "
                "(character_id,character_name,level,updated_at) "
                "VALUES (1,'First Crafter',60,1000)")
        crafting.save_profile(self.path, 1, {
            "primaries": [{"profession": {"name": "Alchemy"},
                           "skill_points": 300, "known_recipes": []}],
        })
        self.assertNotIn(
            (1, "First Crafter"),
            armory.candidates(self.path, now=1100, for_crafting=True),
        )
        self.assertIn(
            (1, "First Crafter"),
            armory.candidates(self.path, now=1901, for_crafting=True),
        )


if __name__ == "__main__":
    unittest.main()
