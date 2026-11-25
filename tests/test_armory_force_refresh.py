"""Offline regression guards for manual full-roster Blizzard refreshes."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from hyped_ogre.features.characters import armory


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def post(self, *_args, **_kwargs):
        return self

    def raise_for_status(self):
        return None

    async def json(self):
        return {"access_token": "test-token"}


class ArmoryForceRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "characters.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute(
                "CREATE TABLE characters (character_id INTEGER PRIMARY KEY, "
                "name TEXT, roster_visible INTEGER, is_main INTEGER)"
            )
            con.executemany(
                "INSERT INTO characters VALUES (?,?,1,1)",
                [(1, "One Ogre"), (2, "Two Ogre"), (3, "Three Ogre")],
            )
        armory.initialise(self.path)
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executemany(
                "INSERT INTO character_armory_cache "
                "(character_id,character_name,level,updated_at,ilvl,professions) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (1, "One Ogre", 60, 10000, 75,
                     '[{"name":"Alchemy","level":200}]'),
                    (2, "Two Ogre", 60, 10000, None, "[]"),
                ],
            )

    @patch.dict(os.environ, {"BLIZZARD_CLIENT_ID": "test-id",
                             "BLIZZARD_CLIENT_SECRET": "test-secret"})
    async def test_forced_refresh_attempts_fresh_records_and_retains_verified_values(self):
        self.assertEqual(
            armory.candidates(self.path, now=10000),
            [(3, "Three Ogre")],
        )
        # Oldest unchecked profiles go first, even when TTL is bypassed.
        self.assertEqual(
            armory.candidates(self.path, now=10000, force=True, limit=100),
            [(3, "Three Ogre"), (1, "One Ogre"), (2, "Two Ogre")],
        )

        async def response(_session, _token, name, *, diagnostics=None):
            if name == "Three Ogre":
                diagnostics["outcome"] = "not_found"
                return None
            return {
                "level": 60,
                "ilvl": None if name == "One Ogre" else 81,
                "race": "",
                "guild": "",
                "profession_details": [],
            }

        with patch.object(armory.aiohttp, "ClientSession",
                          return_value=FakeSession()), \
             patch.object(armory, "fetch_profile", side_effect=response):
            report = await armory.refresh(
                self.path, now=10000, force=True, limit=100,
                detailed=True, throttle=0,
            )

        self.assertEqual(report["attempted"], 3)
        self.assertEqual(report["updated"], 2)
        self.assertEqual(report["new_ilvls"], 1)
        self.assertEqual(report["missing_ilvl"], 1)
        self.assertEqual(report["not_found"], 1)
        self.assertEqual(report["api_errors"], 0)
        saved = armory.cached(self.path)
        self.assertEqual(saved[1]["ilvl"], 75)
        self.assertEqual(saved[1]["profession_details"],
                         [{"name": "Alchemy", "level": 200}])
        self.assertEqual(saved[2]["ilvl"], 81)
        self.assertIsNone(saved[3]["ilvl"])

    @patch.dict(os.environ, {"BLIZZARD_CLIENT_ID": "test-id",
                             "BLIZZARD_CLIENT_SECRET": "test-secret"})
    async def test_rate_limit_stops_remaining_requests_without_erasing_cache(self):
        import aiohttp

        async def rate_limited(*_args, **_kwargs):
            raise aiohttp.ClientResponseError(None, (), status=429)

        with patch.object(armory.aiohttp, "ClientSession",
                          return_value=FakeSession()), \
             patch.object(armory, "fetch_profile", side_effect=rate_limited):
            report = await armory.refresh(
                self.path, now=10000, force=True, detailed=True, limit=100,
            )

        self.assertTrue(report["rate_limited"])
        self.assertEqual(report["attempted"], 1)
        self.assertEqual(report["api_errors"], 1)
        self.assertEqual(armory.cached(self.path)[1]["ilvl"], 75)


if __name__ == "__main__":
    unittest.main()
