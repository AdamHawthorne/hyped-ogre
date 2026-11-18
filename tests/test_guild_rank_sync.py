import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from hyped_ogre.features import guild_rank_sync as ranks, guild_sync
from hyped_ogre.features.classrooms.config import GUILD_ID


class DiscordExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/"raid.db")
        with sqlite3.connect(self.path) as con:
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

    async def test_unprivileged_member_cannot_export(self):
        cog=ranks.GuildRankSync(SimpleNamespace(is_owner=AsyncMock(return_value=False)))
        interaction=SimpleNamespace(guild_id=GUILD_ID,guild=SimpleNamespace(owner_id=1),user=SimpleNamespace(id=42,roles=[]),
            response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()))
        with patch.object(ranks,"create_export",new_callable=AsyncMock) as create:
            await cog.export_roles.callback(cog,interaction)
        create.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])


class SignedRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.secret="testing-only-"+"x"*40
        self.app=web.Application()
        self.env=patch.dict(os.environ,{"HYPED_SYNC_SECRET":self.secret,"HYPED_RANK_SYNC_ENABLED":"true","HYPED_CONTROL_ENABLED":"false"})
        self.env.start();self.addCleanup(self.env.stop)
        guild_sync.register_route(self.app,SimpleNamespace())
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def headers(self,body,age=0):
        stamp=str(int(time.time())-age)
        digest=hmac.new(self.secret.encode(),stamp.encode()+b"."+body,hashlib.sha256).hexdigest()
        return {"X-Hyped-Timestamp":stamp,"X-Hyped-Signature":"sha256="+digest,"Content-Type":"application/json"}

    async def test_signed_rank_fetch_and_stale_or_mutated_request_rejected(self):
        body=json.dumps({"version":1,"guild":"Hyped"}).encode()
        with patch.object(ranks,"create_export",new_callable=AsyncMock,return_value={"format":"HGT_DISCORD_RANKS"}) as create:
            stale=await self.client.post("/hyped/rank-roster",data=body,headers=self.headers(body,301))
            self.assertEqual(stale.status,401);create.assert_not_awaited()
            bad=await self.client.post("/hyped/rank-roster",data=body+b" ",headers=self.headers(body))
            self.assertEqual(bad.status,401)
            good=await self.client.post("/hyped/rank-roster",data=body,headers=self.headers(body))
            self.assertEqual(good.status,200);create.assert_awaited_once()
            again=await self.client.post("/hyped/rank-roster",data=body,headers=self.headers(body))
            self.assertEqual(again.status,429)

    async def test_level_sync_cannot_flush_pending_bank_announcements_when_disabled(self):
        payload={"version":1,"source":"HypedGuildTools","guild":"Hyped","roster":[],"bankEvents":[],"bankUploadEnabled":False}
        body=json.dumps(payload).encode()
        with patch.object(guild_sync,"persist_payload",return_value={}), patch.object(guild_sync,"announce_pending",new_callable=AsyncMock) as announce:
            response=await self.client.post("/hyped/sync",data=body,headers=self.headers(body))
        self.assertEqual(response.status,200);announce.assert_not_awaited()

    async def test_bank_payload_requires_opt_in(self):
        from test_guild_sync import payload
        value=payload();value["bankUploadEnabled"]=False
        with self.assertRaises(guild_sync.SyncPayloadError):guild_sync.validate_payload(value)


class FullNameLevelsTests(unittest.TestCase):
    def test_full_surname_is_retained_for_level_identity(self):
        self.assertEqual(guild_sync._canonical_name("Big-Snore"),"big snore")
        self.assertNotEqual(guild_sync._canonical_name("Big"),guild_sync._canonical_name("Big Snore"))
