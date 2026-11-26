"""Professions V2 preview, cache freshness and ownership safeguards."""
from contextlib import closing
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from hyped_ogre.features.characters import armory, crafting
from hyped_ogre.features.characters.professions_channel import (
    ProfessionPilotCommands, format_crafters,
)


class ProfessionV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "guild.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                    name TEXT, roster_visible INTEGER, is_main INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY, hidden INTEGER);
            """)
            con.executemany(
                "INSERT INTO characters VALUES (?,?,?,?,?)", [
                    (1, 101, "Craft Ogre", 1, 1),
                    (2, 102, "Alchemy Ogre", 1, 1),
                    (3, 103, "Hidden Ogre", 1, 1),
                    (4, 104, "LFG Ogre", 0, 1),
                ],
            )
            con.execute("INSERT INTO character_board_overrides VALUES (3,1)")
        crafting.initialise(self.path)
        armory.initialise(self.path)

    def test_unverified_skill_is_shown_not_claimed_as_zero_recipes(self):
        profile = {"primaries": [{"profession": {"name": "Alchemy"},
                                 "skill_points": 275}]}
        self.assertTrue(crafting.save_profile(self.path, 1, profile))
        listed = crafting.leaderboard(self.path)["Alchemy"]
        self.assertEqual(listed[0]["recipe_count"], -1)
        embed = format_crafters({"Alchemy": listed})
        self.assertIn("Craft Ogre", embed.fields[0].value)
        self.assertIn("recipes not verified", embed.fields[0].value)
        self.assertFalse(embed.fields[0].inline)

    def test_wse_fallback_and_verified_order(self):
        crafting.save_profile(self.path, 2, {
            "primaries": [{"profession": {"name": "Alchemy"},
                           "skill_points": 220,
                           "known_recipes": [{"id": 100, "name": "Test Recipe"}]}]})
        exports = [
            {"character_id": 1, "discord_id": 101, "name": "Craft Ogre",
             "raw_text": "first"},
            {"character_id": 3, "discord_id": 103, "name": "Hidden Ogre",
             "raw_text": "hidden"},
            {"character_id": 4, "discord_id": 104, "name": "LFG Ogre",
             "raw_text": "lfg"},
        ]
        def parsed(text):
            return {"profession_details": [{"name": "Alchemy", "level": 300}]}
        with (patch("hyped_ogre.features.characters.equipment.latest_profiles",
                    return_value=exports),
              patch("hyped_ogre.features.characters.equipment.parse_export",
                    side_effect=parsed)):
            grouped = crafting.board_rows(self.path)
        self.assertEqual([r["name"] for r in grouped["Alchemy"]],
                         ["Craft Ogre", "Alchemy Ogre"])
        self.assertEqual(grouped["Alchemy"][0]["source"], "WoWSims")
        embed = format_crafters(grouped)
        self.assertIn("WSE", embed.fields[0].value)

    def test_armory_profession_refresh_bypasses_valid_level_cache(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute(
                "INSERT INTO character_armory_cache "
                "(character_id,character_name,level,updated_at) "
                "VALUES (1,'Craft Ogre',60,9000)")
        baseline = armory.candidates(self.path, now=9050)
        self.assertNotIn((1, "Craft Ogre"), baseline)
        targeted = armory.candidates(self.path, now=9050, for_crafting=True)
        self.assertIn((1, "Craft Ogre"), targeted)

    def test_private_top_level_commands_are_registered(self):
        pilot = ProfessionPilotCommands(MagicMock(), MagicMock())
        registered = {command.name for command in pilot.__cog_app_commands__}
        self.assertIn("postprofessions-v2", registered)
        self.assertIn("profession-status", registered)


if __name__ == "__main__":
    unittest.main()
