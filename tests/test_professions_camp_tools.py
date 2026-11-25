"""Offline guards for verified Artisan Forever campfire tool ownership."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hyped_ogre.features.characters import camp_tools, crafting
from hyped_ogre.features.characters.professions_channel import ProfessionsV2


class CampToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "guild.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY,
                    discord_id INTEGER,
                    name TEXT,
                    roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY,
                    hidden INTEGER);
            """)
            con.executemany(
                "INSERT INTO characters VALUES (?,?,?,?)", [
                    (1, 10101, "Tailor Main", 1),
                    (2, 10202, "Smith Main", 1),
                    (3, 10303, "Hidden Tailor", 1),
                    (4, 10404, "LFG Only", 0),
                    (5, 10101, "Tailor Alt", 1),
                ],
            )
            con.execute(
                "INSERT INTO character_board_overrides VALUES (3,1)"
            )
        crafting.initialise(self.path)

    def add(self, char_id, prof, recipes, skill=300):
        crafting.save_profile(self.path, char_id, {
            "primaries": [{
                "profession": {"name": prof},
                "skill_points": skill,
                "known_recipes": [
                    {"id": ident, "name": name}
                    for ident, name in recipes
                ],
            }],
            "secondaries": [],
        })

    def test_exact_camp_tool_known_recipe_not_high_skill(self):
        self.add(1, "Tailoring", [
            (501, "Spinning Wheel"),
            (502, "Loom"),
        ])
        self.add(2, "Blacksmithing", [
            (503, "Sharpening Wheel"),
            (504, "Anvil"),
        ])
        self.add(3, "Tailoring", [(502, "Loom")])
        self.add(4, "Tailoring", [(502, "Loom")])
        self.add(5, "Tailoring", [(505, "Blueprint: Loom")])
        owners = camp_tools.owners(self.path)
        self.assertEqual(
            [member["discord_id"] for member in owners["Tailoring"]],
            [10101],
        )
        # Owning a skill-300 Blacksmith does not prove Artisan Master Forge.
        self.assertEqual(owners["Blacksmithing"], [])
        self.assertTrue(all(p in owners for p in camp_tools.ADVANCED_STATIONS))

    def test_qualified_blueprint_name_and_url(self):
        self.assertEqual(
            camp_tools._normalise("Blueprint: Loom"),
            camp_tools._normalise("Loom"),
        )
        self.assertEqual(
            camp_tools.link("Tailoring"),
            "[Loom](<https://www.wowhead.com/forever/"
            "search?q=Blueprint%3A%20Loom>)",
        )
        self.assertIn("search?q=Blueprint%3A%20Master%20Forge",
                      camp_tools.link("Blacksmithing"))

    def test_v2_heads_same_line_with_member_and_no_fake_owner(self):
        grouped = {
            "Tailoring": [
                {"name": "Tailor Main", "discord_id": 10101, "skill": 300}
            ],
            "Blacksmithing": [
                {"name": "Smith Main", "discord_id": 10202, "skill": 300}
            ],
        }
        camp_owners = {
            "Tailoring": [{"discord_id": 10101, "character_name": "Tailor Main"}],
            "Blacksmithing": [],
        }
        view = ProfessionsV2(
            grouped, SimpleNamespace(emojis=[]), camp_owners=camp_owners,
            updated_at=12345,
        )
        strings = [
            component.content
            for container in view.children
            for component in getattr(container, "children", [])
            if hasattr(component, "content")
        ]
        grouped_text = "\n".join(strings)
        self.assertIn(
            "**Tailoring (1)**\n-# [Loom]", grouped_text
        )
        self.assertIn("<@10101>", grouped_text)
        self.assertIn(
            "**Blacksmithing (1)**\n-# [Master Forge]", grouped_text
        )
        self.assertIn("Ogre not found anyone with this yet", grouped_text)
        self.assertNotIn("**Blacksmithing (1)** · [Master Forge]", "filler")

    def test_three_discord_mentions_max_with_overflow(self):
        people = [{"discord_id": n} for n in (1, 2, 3, 4)]
        result = camp_tools.heading_suffix(
            "Tailoring", {"Tailoring": people}
        )
        self.assertIn("\n-# [Loom]", result)
        self.assertIn("<@1>, <@2>, <@3> +1 more", result)
        self.assertNotIn("<@4>", result)


if __name__ == "__main__":
    unittest.main()
