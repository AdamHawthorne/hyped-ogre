import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from hyped_ogre.features import guild_member_sync as ranks
from hyped_ogre.features.classrooms.config import GUILD_ID


class DiscordExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/"raid.db")
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("CREATE TABLE characters(character_id,discord_id,name,is_main)")
            con.executemany("INSERT INTO characters VALUES(?,?,?,?)",[(1,42,"Big Snore",1),(2,42,"Snore Bis",0),(3,99,"Another Person",1)])
        self.role=SimpleNamespace(id=12345,name="Forever")
        async def fetch(i):
            return SimpleNamespace(roles=[self.role] if i==42 else [])
        self.guild=SimpleNamespace(get_role=lambda i:self.role if i==12345 else None, fetch_member=AsyncMock(side_effect=fetch))
        self.bot=SimpleNamespace(get_guild=lambda i:self.guild if i==GUILD_ID else None)

    async def test_fresh_membership_and_full_character_names(self):
        with patch.dict(os.environ,{"FOREVER_ROLE_ID":"12345","HYPED_RANK_DISCORD_ROLE_ID":""}):
            export=await ranks.create_export(self.bot,self.path)
        self.assertEqual(self.guild.fetch_member.await_count,2)
        self.assertEqual([r["character"] for r in export["members"]],["Big Snore","Snore Bis","Another Person"])
        self.assertEqual([r["eligible"] for r in export["members"]],[True,True,False])
        self.assertEqual([r["isMain"] for r in export["members"]],[True,False,True])
        self.assertEqual(export["roleId"],"12345")

    async def test_missing_member_is_distinct_from_role_absence(self):
        self.guild.fetch_member.side_effect=discord.NotFound(SimpleNamespace(status=404,reason="Not Found"),"gone")
        with patch.dict(os.environ,{"FOREVER_ROLE_ID":"12345","HYPED_RANK_DISCORD_ROLE_ID":""}):
            result=await ranks.create_export(self.bot,self.path)
        self.assertTrue(all(not r["memberPresent"] and not r["eligible"] for r in result["members"]))

    async def test_failed_role_lookup_never_yields_partial_eligibility(self):
        self.guild.fetch_member.side_effect=discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"blocked")
        with patch.dict(os.environ,{"FOREVER_ROLE_ID":"12345","HYPED_RANK_DISCORD_ROLE_ID":""}):
            with self.assertRaises(ranks.RankExportError):await ranks.create_export(self.bot,self.path)

    async def test_wrong_role_configuration_is_rejected(self):
        with patch.dict(os.environ,{"FOREVER_ROLE_ID":"0","HYPED_RANK_DISCORD_ROLE_ID":""}):
            with self.assertRaises(ranks.RankExportError):await ranks.create_export(self.bot,self.path)
        self.guild.fetch_member.assert_not_called()

    async def test_character_reader_closes_database_handle(self):
        opened = []
        real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch.object(ranks.sqlite3, "connect", side_effect=connect):
            self.assertEqual(len(ranks.registered_characters(self.path)), 3)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


class DirectMemberExportTests(unittest.TestCase):
    def snapshot(self):
        return {"format":"HGT_DISCORD_RANKS", "version":1, "guild":"Hyped", "generatedAt":1800000000,
                "members":[{"character":"Big Snore", "isMain":True,"memberPresent":True,"eligible":True},
                           {"character":"Snore Bis", "isMain":False,"memberPresent":True,"eligible":True}]}

    def test_direct_addon_format_and_unknown_future_eligibility(self):
        text = ranks.member_export_text(self.snapshot())
        self.assertEqual(text, "HGT|3|MEMBERS|Hyped|1800000000\nCHAR|Big Snore|main|forever|unknown|G1\nCHAR|Snore Bis|alt|forever|unknown|G2\nEND")
        self.assertNotIn("discordId", text)

    def test_missing_role_and_departed_member_are_distinct(self):
        snapshot=self.snapshot()
        snapshot["members"][0]["eligible"]=False
        snapshot["members"][1]["memberPresent"]=False
        text=ranks.member_export_text(snapshot)
        self.assertIn("|main|no_role|unknown",text)
        self.assertIn("|alt|left|unknown",text)

    def test_duplicates_malformed_names_and_missing_facts_fail(self):
        for name in ("Big-Snore", "Bad|Name", "Bad\nName", "Name123", " Name", ""):
            snapshot=self.snapshot();snapshot["members"][1]["character"]=name
            with self.subTest(name=name), self.assertRaises(ranks.RankExportError):
                ranks.member_export_text(snapshot)
        snapshot=self.snapshot();del snapshot["members"][0]["eligible"]
        with self.assertRaises(ranks.RankExportError):ranks.member_export_text(snapshot)

    def test_empty_and_oversized_export_rejected(self):
        snapshot=self.snapshot();snapshot["members"]=[]
        with self.assertRaises(ranks.RankExportError):ranks.member_export_text(snapshot)
        snapshot=self.snapshot();snapshot["members"]*=501
        with self.assertRaises(ranks.RankExportError):ranks.member_export_text(snapshot)


class DirectMemberCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_export_is_private_text_attachment(self):
        cog=ranks.GuildRankSync(SimpleNamespace(is_owner=AsyncMock(return_value=False)))
        interaction=SimpleNamespace(guild_id=GUILD_ID, guild=SimpleNamespace(owner_id=42),
            user=SimpleNamespace(id=42,roles=[]), response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(ranks,'create_export',new_callable=AsyncMock,return_value=DirectMemberExportTests().snapshot()):
            await cog.export_members.callback(cog,interaction)
        result=interaction.followup.send.call_args.kwargs
        self.assertTrue(result['ephemeral'])
        self.assertEqual(result['file'].filename,'hyped-members.txt')
        self.assertTrue(result['file'].fp.read().startswith(b'HGT|3|MEMBERS|Hyped|'))

    async def test_unprivileged_export_does_not_read_database(self):
        cog=ranks.GuildRankSync(SimpleNamespace(is_owner=AsyncMock(return_value=False)))
        interaction=SimpleNamespace(guild_id=GUILD_ID, guild=SimpleNamespace(owner_id=1),
            user=SimpleNamespace(id=42,roles=[]), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(ranks,'create_export',new_callable=AsyncMock) as create:
            await cog.export_members.callback(cog,interaction)
        create.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
