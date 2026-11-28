"""Persistent crafting request cleanup and verified high-skill recipe discoveries."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hyped_ogre.features.characters import crafting, craft_catalog
from hyped_ogre.features.characters import professions_channel as board


class CraftRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "craft.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY,
                    discord_id INTEGER,
                    name TEXT, roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY,
                    hidden INTEGER);
            """)
            con.execute(
                "INSERT INTO characters VALUES (1,123,'Test Crafter',1)"
            )
        crafting.initialise(self.path)

    def test_tick_one_day_unticked_three_days(self):
        crafting.store_request(
            self.path, request_id=100, guild_id=2,
            channel_id=10, reply_id=101, requested_at=1000)
        crafting.store_request(
            self.path, request_id=200, guild_id=2,
            channel_id=10, requested_at=1000)
        self.assertTrue(crafting.request_exists(self.path, 100, 10))
        self.assertFalse(crafting.request_exists(self.path, 100, 11))
        self.assertTrue(crafting.resolve_request(
            self.path, request_id=100, user_id=4, resolved_at=2000))
        self.assertFalse(crafting.resolve_request(
            self.path, request_id=100, user_id=5, resolved_at=3000))
        self.assertEqual(crafting.expired_requests(self.path, now=88399), [])
        self.assertEqual(
            [r["request_id"] for r in crafting.expired_requests(
                self.path, now=88400)], [100])
        self.assertEqual(
            [r["request_id"] for r in crafting.expired_requests(
                self.path, now=260200)], [100, 200])
        crafting.forget_request(self.path, 100)
        self.assertFalse(crafting.request_exists(self.path, 100, 10))

    def test_artisan_guide_recipes_fill_missing_api_skill_metadata(self):
        self._save([{"id": 51000, "name": "Initial"}], now=1000)
        self._save([
            {"id": 51000, "name": "Initial"},
            {"id": 51001, "name": "Alchemy Laboratory"},
            {"id": 51002, "name": "Low Level Potion"},
        ], now=2000)
        # Blizzard occasionally omits required_skill even for documented
        # Artisan campfire blueprints; the guide whitelist bridges this.
        crafting.save_catalog(self.path, [
            {"id": 51001, "name": "Alchemy Laboratory",
             "profession": "Alchemy"},
            {"id": 51002, "name": "Low Level Potion",
             "profession": "Alchemy"},
        ])
        recent = crafting.latest_high_skill_discoveries(self.path)
        self.assertEqual([row["recipe_name"] for row in recent],
                         ["Alchemy Laboratory"])
        self.assertTrue(crafting._artisan_from_guide(
            "Alchemy", "Alchemy Laboratory"))
        self.assertFalse(crafting._artisan_from_guide(
            "Alchemy", "Low Level Potion"))

    def test_initial_recipe_import_is_not_a_new_discovery(self):
        self._save([{"id": 50001, "name": "First 300 Recipe"}], now=1000)
        self._catalog(50001, "First 300 Recipe", required=300)
        self.assertEqual(crafting.latest_high_skill_discoveries(self.path), [])

    def test_new_recipe_requires_verified_skill_300(self):
        self._save([{"id": 50001, "name": "Initial"}], now=1000)
        self._catalog(50001, "Initial", required=300)
        self._save([
            {"id": 50001, "name": "Initial"},
            {"id": 50002, "name": "New 300 Recipe"},
            {"id": 50003, "name": "New 225 Recipe"},
        ], now=2000)
        self._catalog(50002, "New 300 Recipe", required=300)
        self._catalog(50003, "New 225 Recipe", required=225)
        recent = crafting.latest_high_skill_discoveries(self.path)
        self.assertEqual([r["recipe_name"] for r in recent],
                         ["New 300 Recipe"])
        view = board.ProfessionsV2({}, new_recipes=recent)
        text = "\n".join(
            getattr(component, "content", "")
            for child in view.children
            for component in getattr(child, "children", [])
        )
        self.assertIn("https://www.wowhead.com/forever/search?q=New%20300%20Recipe", text)
        self.assertNotIn("New 225 Recipe", text)

    def _catalog(self, ident, name, required):
        crafting.save_catalog(self.path, [{
            "id": ident, "name": name,
            "profession": "Alchemy", "skill_required": required,
        }])

    def _save(self, known, now):
        crafting.save_profile(self.path, 1, {
            "primaries": [{
                "profession": {"name": "Alchemy"},
                "skill_points": 300,
                "known_recipes": known,
            }],
            "secondaries": [],
        }, observed_at=now)

    def test_static_catalog_explicit_skill_only(self):
        tier = {"recipes": [
            {"id": 10, "name": "300 Craft", "required_skill": 300},
            {"id": 11, "name": "Unknown Craft"},
        ]}
        result = craft_catalog.extract_tier(tier, "Alchemy")
        self.assertEqual(result[0]["skill_required"], 300)
        self.assertNotIn("skill_required", result[1])

    def test_crusader_natural_language_lookup(self):
        crafting.save_catalog(self.path, [{
            "id": 20034, "name": "Enchant Weapon - Crusader",
            "profession": "Enchanting",
        }])
        self.assertIn("wanted pile",
                      board.answer(self.path, "enchant Crusader"))


if __name__ == "__main__":
    unittest.main()
