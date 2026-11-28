"""Offline guards for guild-wide Blizzard recipe census and desired crafts."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hyped_ogre.features.characters import (
    armory, craft_wanted, crafting, guild_craft_sync, priority_recipes,
)


class GuildCraftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "guild.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript("""
                CREATE TABLE characters (
                    character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                    name TEXT, roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                    character_id INTEGER PRIMARY KEY, hidden INTEGER);
            """)
            con.execute(
                "INSERT INTO characters VALUES (1,100,'Our Main',1)"
            )
        crafting.initialise(self.path)
        crafting.save_catalog(self.path, [
            {"id": 600, "name": "Lionheart Helm", "profession": "Blacksmithing"},
            {"id": 601, "name": "Enchant Weapon - Crusader", "profession": "Enchanting"},
            {"id": 602, "name": "Goblin Sapper Charge", "profession": "Engineering"},
        ])
        self.members = [
            {"id": 900, "slug": "guild-smith", "name": "Guild Smith",
             "level": 60, "rank": 3},
            {"id": 901, "slug": "guild-mage", "name": "Guild Mage",
             "level": 40, "rank": 4},
        ]
        self.smith = {
            "primaries": [{
                "profession": {"name": "Blacksmithing"},
                "tiers": [{"skill_points": 300, "known_recipes": [
                    {"id": 600, "name": "Lionheart Helm"}]}],
            }],
            "secondaries": [],
        }

    def test_unregistered_guild_member_is_verified_without_raid_registration(self):
        self.assertEqual(crafting.guild_roster(
            self.path, self.members, observed_at=1795800000), 2)
        crafting.save_profile(self.path, -900, self.smith, observed_at=1795800001)
        crafting.guild_profile_checked(self.path, 900, observed_at=1795800001)
        owners = crafting.query(self.path, "Lionheart")["crafters"]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]["name"], "Guild Smith")
        self.assertIsNone(owners[0]["discord_id"])
        self.assertEqual(
            crafting.leaderboard(self.path)["Blacksmithing"][0]["name"],
            "Guild Smith"
        )
        audited = priority_recipes.audit(self.path, "Blacksmithing")
        self.assertEqual(audited[0]["status"], "confirmed")
        self.assertEqual(audited[0]["members"][0]["character_name"], "Guild Smith")
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute(
                "SELECT character_id,name FROM characters").fetchall()
        self.assertEqual(rows, [(1, "Our Main")])

    def test_membership_removal_is_reversible_and_failed_roster_keeps_cache(self):
        crafting.guild_roster(self.path, self.members, observed_at=1795800000)
        crafting.save_profile(self.path, -900, self.smith)
        self.assertTrue(crafting.query(self.path, "Lionheart")["crafters"])
        self.assertEqual(crafting.guild_roster(self.path, [], observed_at=1795800001), 0)
        self.assertTrue(crafting.query(self.path, "Lionheart")["crafters"])
        crafting.guild_roster(self.path, self.members[1:], observed_at=1795800001)
        self.assertEqual(crafting.query(self.path, "Lionheart")["crafters"], [])
        crafting.guild_roster(self.path, self.members, observed_at=1795800001)
        self.assertTrue(crafting.query(self.path, "Lionheart")["crafters"])

    def test_hidden_registered_character_cannot_reappear_from_guild_api(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("INSERT INTO characters VALUES (2,200,'Guild Smith',1)")
            con.execute("INSERT INTO character_board_overrides VALUES (2,1)")
        crafting.guild_roster(self.path, self.members, observed_at=1795800000)
        crafting.save_profile(self.path, -900, self.smith)
        self.assertEqual(crafting.query(self.path, "Lionheart")["crafters"], [])
        self.assertNotIn("Blacksmithing", crafting.leaderboard(self.path))

    def test_desires_are_durable_idempotent_and_prioritised_without_claiming_missing(self):
        crafting.guild_roster(self.path, self.members, observed_at=1795800000)
        crafting.save_profile(self.path, -900, self.smith)
        for request, recipe in ((11, 601), (12, 601), (13, 600)):
            self.assertTrue(crafting.store_desire(
                self.path, request_id=request, guild_id=1,
                user_id=request, term="Crusader" if recipe == 601 else "Lionheart",
                recipe_id=recipe,
            ))
        self.assertFalse(crafting.store_desire(
            self.path, request_id=11, guild_id=1, user_id=11,
            term="Crusader", recipe_id=601,
        ))
        top = craft_wanted.top_ten(self.path)
        names = [row["name"] for row in top["recipes"]]
        self.assertEqual(names[0], "Enchant Weapon - Crusader")
        self.assertNotIn("Lionheart Helm", names)
        message = craft_wanted.format_wanted(top)
        self.assertIn("2 guildie(s) asking", message)
        self.assertIn("no live Auctionator", message.lower())
        self.assertIn("not proof", message.lower())
        # Archive/expiry of the original Discord request must not erase demand.
        crafting.store_request(
            self.path, request_id=11, guild_id=1, channel_id=1
        )
        crafting.forget_request(self.path, 11)
        self.assertEqual(craft_wanted.top_ten(self.path)["recipes"][0]["requesters"], 2)

    def test_incomplete_profession_response_never_erases_verified_recipes(self):
        crafting.guild_roster(self.path, self.members, observed_at=1795800000)
        crafting.save_profile(self.path, -900, self.smith)
        self.assertTrue(crafting.save_profile(self.path, -900, {
            "primaries": [{"profession": {"name": "Blacksmithing"},
                           "tiers": [{"skill_points": 301}]}],
        }))
        self.assertTrue(crafting.query(self.path, "Lionheart")["crafters"])


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload, self.status = payload, status
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return False
    def raise_for_status(self):
        if self.status >= 400:
            raise ValueError(f"HTTP {self.status}")
    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, profession_payload, *, profession_status=200):
        self.profession_payload, self.profession_status = (
            profession_payload, profession_status
        )
        self.urls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return False
    def post(self, url, **kwargs):
        return FakeResponse({"access_token": "token"})
    def get(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/guild/hyped"):
            return FakeResponse({"name": "Hyped", "slug": "hyped"})
        if url.endswith("/guild/hyped/roster"):
            return FakeResponse({"members": [{
                "character": {
                    "id": 900, "name": "Guild", "surname": "Smith",
                    "slug": "guild-smith", "level": 60,
                }, "rank": 4,
            }]})
        if url.endswith("/guild-smith/professions"):
            return FakeResponse(self.profession_payload, self.profession_status)
        raise AssertionError(f"Unexpected API URL: {url}")


class GuildApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_roster_then_professions_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "guild.db")
            crafting.initialise(path)
            payload = {"primaries": [{
                "profession": {"name": "Alchemy"},
                "tiers": [{"skill_points": 300, "known_recipes": [
                    {"id": 17187, "name": "Transmute: Arcanite"}
                ]}],
            }], "secondaries": []}
            fake = FakeSession(payload)
            with patch.dict(os.environ, {
                "BLIZZARD_CLIENT_ID": "test", "BLIZZARD_CLIENT_SECRET": "test"
            }), patch.object(armory, "credentials_configured", return_value=True), \
                 patch.object(guild_craft_sync.aiohttp, "ClientSession",
                              return_value=fake):
                result = await guild_craft_sync.refresh(
                    path, now=1795800000, max_profiles=1)
            self.assertEqual(result["members"], 1)
            self.assertEqual(result["verified"], 1)
            self.assertEqual(result["attempted"], 1)
            self.assertEqual(crafting.query(path, "Arcanite Bar")["crafters"][0]["name"],
                             "Guild Smith")
            self.assertEqual(len(fake.urls), 3)
            self.assertTrue(all(url.startswith(armory.HOST) for url in fake.urls))

    async def test_partial_roster_cannot_replace_census(self):
        result = guild_craft_sync.parse_members({
            "members": [
                {"character": {"id": 100, "name": "Big",
                                "surname": "Snore", "slug": "big-snore"}},
                {"character": {"id": 101, "name": "Missing"}}
            ]
        })
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
