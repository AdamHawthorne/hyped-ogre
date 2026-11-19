"""Offline tests for private V2 roster, live-level fallback and safe renaming."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import raid_signup as raid, raid_schedule
from hyped_ogre.features.characters import armory, roster, v2, admin_ui
from hyped_ogre.features.characters.hub import CharacterHub
from hyped_ogre.features.classrooms.config import GUILD_ID


class CharacterV2Tests(unittest.TestCase):
    def test_discord_grouping_keeps_mains_with_grey_alts(self):
        records = [
            {"character_id": 5, "discord_id": 101, "name": "Snore Bis",
             "class_name": "Warrior", "spec": "Fury",
             "role": "DPS", "level": 39, "is_main": 0},
            {"character_id": 4, "discord_id": 101, "name": "Big Snore",
             "class_name": "Warrior", "spec": "Protection",
             "role": "Tank", "level": 60, "is_main": 1},
            {"character_id": 6, "discord_id": 102, "name": "Other Gamer",
             "class_name": "Priest", "spec": "Holy",
             "role": "Healer", "level": 50, "is_main": 1},
        ]
        grouped = v2.groups(records)
        self.assertEqual([did for did, _ in grouped], [101, 102])
        self.assertEqual([r["name"] for r in grouped[0][1]],
                         ["Big Snore", "Snore Bis"])
        first = v2.member_text(*grouped[0])
        self.assertIn("<@101>", first)
        self.assertIn("**Big Snore** · Lv 60", first)
        self.assertIn("-# ", first)
        self.assertIn("Snore Bis · Lv 39", first)
        self.assertLess(first.index("Big Snore"), first.index("Snore Bis"))

    def test_v2_is_persistent_and_bounded_like_lfg(self):
        grouped = [
            (i, [{"character_id": i, "discord_id": i, "name": f"Player {i}",
                  "class_name": "Mage", "spec": "Arcane", "role": "DPS",
                  "level": 30, "is_main": 1}])
            for i in range(1, 31)
        ]
        pages = v2.chunks(grouped)
        self.assertEqual([len(p) for p in pages], [7, 7, 7, 7, 2])
        self.assertEqual(sum(len(p) for p in pages), 30)
        bot = SimpleNamespace(get_cog=lambda _: None)
        cog = SimpleNamespace(bot=bot)
        for i, group in enumerate(pages):
            view = v2.CharacterBoardV2(
                cog, raid.TEST_CHANNEL_ID, grouped=group,
                continuation=i != 0,
            )
            self.assertTrue(view.is_persistent())
            self.assertLessEqual(view.total_children_count, 40)
        self.assertEqual(len(v2.chunks([])), 1)

    def test_renaming_keeps_id_gear_and_attendance(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(101, "Big Snore", "Warrior",
                                "Protection", "Tank", path)
            raid.save_character(101, "Snore Bis", "Warrior",
                                "Fury", "DPS", path)
            ids = {r[1]: r[0] for r in raid.member_characters(101, path)}
            char_id = ids["Big Snore"]
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO gear_exports "
                    "(character_id,submitted_at,format,raw_text) "
                    "VALUES (?,?,?,?)",
                    (char_id, "2026-11-19", "wowsims_json",
                     '{"name":"Big","level":60,"class":"warrior"}'),
                )
            self.assertTrue(raid.rename_character(101, char_id, "Grand Snore", path))
            current = {r[1]: r[0] for r in raid.member_characters(101, path)}
            self.assertEqual(current["Grand Snore"], char_id)
            self.assertNotIn("Big Snore", current)
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT character_id FROM gear_exports").fetchone()[0], char_id)
            self.assertFalse(raid.rename_character(101, char_id, "Grand Snore", path))
            with self.assertRaisesRegex(ValueError, "already registered"):
                raid.rename_character(101, char_id, "Snore Bis", path)
            with self.assertRaisesRegex(ValueError, "isn't registered to you"):
                raid.rename_character(999, char_id, "Taken Snore", path)
            with self.assertRaisesRegex(ValueError, "full First Last"):
                raid.rename_character(101, char_id, "SingleName", path)

    def test_armory_cache_cannot_replace_other_names_or_character_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(101, "Big Snore", "Warrior",
                                "Protection", "Tank", path)
            char_id = raid.member_characters(101, path)[0][0]
            armory.initialise(path)
            self.assertEqual(armory.candidates(path, now=100), [(char_id, "Big Snore")])
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO character_armory_cache "
                    "(character_id,character_name,level,updated_at) "
                    "VALUES (?,?,?,?)",
                    (char_id, "Big Snore", 59, 100),
                )
            self.assertEqual(armory.cached(path)[char_id]["level"], 59)
            self.assertEqual(armory.candidates(path, now=101), [])
            self.assertTrue(raid.rename_character(101, char_id, "Grand Snore", path))
            self.assertEqual(armory.candidates(path, now=101), [(char_id, "Grand Snore")])
            registered = roster.rows(path)
            self.assertEqual(registered[0]["level"], None)
            self.assertIsNone(registered[0]["level_source"])

    def test_leadership_can_unlist_historic_character_without_losing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(101, "Big Snore", "Warrior",
                                "Protection", "Tank", path)
            raid.save_character(101, "Snore Bis", "Warrior",
                                "Fury", "DPS", path)
            saved = {r[1]: r[0] for r in raid.member_characters(101, path)}
            main_id, alt_id = saved["Big Snore"], saved["Snore Bis"]
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO gear_exports "
                    "(character_id,submitted_at,format,raw_text) "
                    "VALUES (?,?,?,?)",
                    (main_id, "2026-11-19", "wowsims_json",
                     '{"name":"Big","level":60,"class":"warrior"}'),
                )
            self.assertTrue(raid.remove_character_from_roster(101, main_id, path))
            with raid.database(path) as con:
                hidden = con.execute(
                    "SELECT roster_visible,is_main FROM characters "
                    "WHERE character_id=?", (main_id,)
                ).fetchone()
                self.assertEqual(hidden, (0, 0))
                self.assertEqual(con.execute(
                    "SELECT roster_visible,is_main FROM characters "
                    "WHERE character_id=?", (alt_id,)
                ).fetchone(), (1, 1))
                self.assertEqual(con.execute(
                    "SELECT character_id FROM gear_exports"
                ).fetchone()[0], main_id)
            self.assertFalse(raid.remove_character_from_roster(101, main_id, path))
            self.assertTrue(raid.promote_character_to_roster(101, main_id, path))
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT roster_visible FROM characters "
                    "WHERE character_id=?", (main_id,)
                ).fetchone()[0], 1)

    def test_armory_identity_is_verified_before_using_level(self):
        self.assertEqual(armory.slug("Fu Ming"), "fu-ming")
        self.assertIn("fu-ming", armory.public_url("Fu Ming"))
        good = {"name": "Fu", "surname": "Ming", "level": 60,
                "race": {"name": "Tauren"}, "guild": {"name": "Hyped"}}
        self.assertEqual(armory.parse_character(good, "Fu Ming")["level"], 60)
        self.assertIsNone(armory.parse_character(good, "Other Person"))
        self.assertIsNone(armory.parse_character(
            {**good, "level": "60"}, "Fu Ming"))
        self.assertIsNone(armory.parse_character(
            {**good, "level": 61}, "Fu Ming"))
        self.assertIsNone(armory.slug("not-a-full-name"))


class V2LeadershipGuardTests(unittest.TestCase):
    def test_leadership_requires_owner_guild_and_private_channel(self):
        def fake(gid, cid, uid):
            return SimpleNamespace(
                guild_id=gid, channel_id=cid,
                user=SimpleNamespace(id=uid),
                guild=SimpleNamespace(owner_id=42),
            )
        self.assertTrue(admin_ui.authorised(
            fake(GUILD_ID, raid.TEST_CHANNEL_ID, 42)))
        self.assertFalse(admin_ui.authorised(
            fake(GUILD_ID, 555, 42)))
        self.assertFalse(admin_ui.authorised(
            fake(GUILD_ID, raid.TEST_CHANNEL_ID, 98)))
        self.assertFalse(admin_ui.authorised(
            fake(555, raid.TEST_CHANNEL_ID, 42)))
        self.assertFalse(admin_ui.authorised(
            fake(GUILD_ID, raid.TEST_CHANNEL_ID, 42), actor_id=98))


class V2PrivateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_post_disallows_public_and_other_guilds(self):
        cog = CharacterHub(SimpleNamespace())
        channel = SimpleNamespace()
        for guild_id, channel_id in (
            (GUILD_ID, 1234),
            (1234, raid.TEST_CHANNEL_ID),
        ):
            interaction = SimpleNamespace(
                guild_id=guild_id, channel_id=channel_id,
                user=SimpleNamespace(id=99),
                guild=SimpleNamespace(owner_id=99),
                channel=channel,
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            await CharacterHub.post_characters_v2.callback(cog, interaction)
            interaction.response.send_message.assert_awaited_once()
            self.assertIn("private", interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
