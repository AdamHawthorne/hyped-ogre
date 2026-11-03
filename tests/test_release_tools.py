"""Character/off-spec, leadership outbox, attendance mirror and live LFG tests.

All databases and transports are temporary. No live Discord pings or Sheets edits.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs import raid_signup as raid, raid_schedule as schedule, raid_sheets_v3 as sheets
from hyped_ogre.features.characters import profiles, hub, roster
from hyped_ogre.features.raids import activity, attendance, notifications, announcer
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID, BOT_ROLE_ID
with patch.dict(os.environ, {"HYPED_GUILD_ID": "77"}):
    from cogs import lfg_preview as lfg
from hyped_ogre.features.lfg import live


class DataFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 19:00", "Hyjal (20m)", wowsims_policy="optional")
        raid.save_character(42, "Paladin", "Paladin", "Retribution", "DPS", self.path)
        self.cid = raid.member_characters(42, self.path)[0][0]

    def pending(self):
        with raid.database(self.path) as con:
            return activity.pending(con)

    def test_offspec_validation_and_owner_checks_do_not_change_main_spec(self):
        with raid.database(self.path) as con:
            profiles.set_offspec(con, 42, self.cid, "Paladin", "Protection", "Tank")
        self.assertEqual(raid.member_characters(42, self.path)[0][2:5], ("Paladin", "Retribution", "DPS"))
        for who, klass, spec, role in ((99, "Paladin", "Holy", "Healer"),
                                     (42, "Mage", "Frost", "DPS"),
                                     (42, "Paladin", "Frost", "Tank")):
            with self.subTest(who=who, klass=klass, spec=spec):
                with self.assertRaises(ValueError), raid.database(self.path) as con:
                    profiles.set_offspec(con, who, self.cid, klass, spec, role)
        with raid.database(self.path) as con:
            self.assertEqual(profiles.offspec(con, 42, self.cid), ("Paladin", "Protection", "Tank"))

    def test_reregistering_a_different_class_clears_offspec_without_replacing_identity(self):
        with raid.database(self.path) as con:
            profiles.set_offspec(con, 42, self.cid, "Paladin", "Protection", "Tank")
        raid.save_character(42, "Paladin", "Priest", "Holy", "Healer", self.path)
        self.assertEqual(raid.member_characters(42, self.path)[0][0], self.cid)
        with raid.database(self.path) as con:
            self.assertEqual(profiles.offspec(con, 42, self.cid), ("Priest", "", ""))

    def test_legacy_character_schema_adds_offspec_without_changing_existing_identity(self):
        legacy = str(Path(self.tmp.name) / "legacy.db")
        with schedule.database(legacy) as con:
            con.execute("CREATE TABLE characters(character_id INTEGER PRIMARY KEY,discord_id INTEGER NOT NULL,"
                        "name TEXT NOT NULL,realm TEXT,class_name TEXT,spec TEXT,role TEXT,is_main INTEGER,last_simc_at INTEGER)")
            con.execute("INSERT INTO characters VALUES (7,42,'Original','','Paladin','Retribution','DPS',1,NULL)")
        raid.create_schema(legacy)
        raid.create_schema(legacy)
        self.assertEqual(raid.member_characters(42, legacy), [(7,"Original","Paladin","Retribution","DPS",None)])
        with raid.database(legacy) as con:
            self.assertEqual(profiles.offspec(con,42,7), ("Paladin","",""))

    def test_board_hides_partial_item_level_instead_of_displaying_misleading_average(self):
        import json
        exported = {"name":"Paladin","class":"paladin","spec":"retribution","version":"v3.2.6",
                    "level":60,"gear":{"items":[{"id":5},{"id":6}]}}
        raid.save_wowsims_export(42,self.cid,json.dumps(exported),self.path)
        with patch.object(roster.items,"cached_catalog",return_value={"5":{"level":50,"name":"Known"}}):
            value=hub.rows(self.path)[0]["ilvl"]
        self.assertEqual(value,"N/A")

    def test_new_notes_changes_and_absences_queue_once_per_actual_change(self):
        raid.save_signup(1, 42, self.cid, self.path)
        self.assertEqual(self.pending(), [])
        raid.save_signup(1, 42, self.cid, self.path, note="Leaving early")
        raid.save_signup(1, 42, self.cid, self.path, note="Leaving early")
        self.assertEqual([r["kind"] for r in self.pending()], ["note"])
        raid.save_signup(1, 42, self.cid, self.path, note="")
        raid.save_absence(1, 42, "Appointment", self.path)
        raid.save_absence(1, 42, "Appointment", self.path)
        raid.save_absence(1, 42, "Updated reason", self.path)
        self.assertEqual([r["kind"] for r in self.pending()], ["note", "note", "absent", "absent"])
        self.assertEqual(self.pending()[-1]["note"], "Updated reason")

    def test_failed_or_rolled_back_mutation_cannot_queue_a_leadership_ping(self):
        with self.assertRaises(ValueError):
            raid.save_signup(1, 99, self.cid, self.path, note="Not mine")
        with self.assertRaises(sqlite3.OperationalError):
            with raid.database(self.path) as con:
                activity.enqueue(con, 1, 42, "note", "Rollback", "now")
                raise sqlite3.OperationalError("rollback")
        self.assertEqual(self.pending(), [])

    def test_concurrent_repeat_forms_cannot_duplicate_change_alerts(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: raid.save_signup(1,42,self.cid,self.path,note="Same note"),range(8)))
            list(pool.map(lambda _: raid.save_absence(1,42,"Same absence",self.path),range(8)))
        self.assertEqual([r["kind"] for r in self.pending()], ["note","absent"])

    def test_returning_to_attend_queues_a_correction_instead_of_leaving_only_absent_alert(self):
        raid.save_absence(1,42,"Away",self.path)
        raid.save_signup(1,42,self.cid,self.path,note="Can come after all")
        raid.save_signup(1,42,self.cid,self.path,note="Can come after all")
        self.assertEqual([r["kind"] for r in self.pending()], ["absent","available"])
        self.assertEqual(self.pending()[-1]["note"], "Can come after all")

    def attendance_row(self, status="Attended", late="", selection="Selected"):
        return ["1", "date", "42", "Discord", str(self.cid), "Paladin", "Paladin", "DPS",
                selection, status, late, "", "No", "Officer note", "2026-12-09T22:00:00+00:00"]

    def test_manual_sheet_attendance_is_mirrored_with_late_and_absent_meaning(self):
        self.assertEqual(attendance.mirror_rows(self.path, [self.attendance_row(late="10")]), 1)
        self.assertEqual(attendance.mirror_rows(self.path, [self.attendance_row(late="10")]), 0)
        with raid.database(self.path) as con:
            self.assertEqual(con.execute("SELECT status,late_minutes,leadership_notes FROM attendance").fetchone(),
                             ("Late", 10, "Officer note"))
        attendance.mirror_rows(self.path, [self.attendance_row("Absent")])
        with raid.database(self.path) as con:
            self.assertEqual(con.execute("SELECT status FROM attendance").fetchone()[0], "No-show")

    def test_guest_unknown_raid_and_foreign_character_sheet_rows_are_not_imported(self):
        rows = [self.attendance_row() for _ in range(3)]
        rows[0][2] = "guest"
        rows[1][0] = "999"
        rows[2][2] = "99"
        self.assertEqual(attendance.mirror_rows(self.path, rows), 0)

    def test_bench_completion_writes_sheet_and_local_attendance_and_preserves_exceptions(self):
        member = ["Player", "Paladin", "Paladin", "Retribution", "DPS", "Main", "", "", "Signed", "Bench", "Yes",
                  "Yes", "No", "No", "", "", "42", str(self.cid)]
        existing = [list(sheets.ATTENDANCE_HEADERS)] if hasattr(sheets, "ATTENDANCE_HEADERS") else [[]]
        with patch.object(sheets, "attendance_candidates", return_value=[member]), \
             patch.object(sheets.raid_sheets, "_session", return_value=MagicMock()), \
             patch.object(sheets, "_permanent_read", return_value=existing), \
             patch.object(sheets, "_permanent_write") as write:
            self.assertEqual(sheets.attendance_complete(self.path, 1), 1)
            self.assertEqual(write.call_args.args[3][9], "Attended")
        with raid.database(self.path) as con:
            self.assertEqual(con.execute("SELECT status FROM attendance").fetchone()[0], "Attended")
        with patch.object(sheets, "attendance_candidates", return_value=[member]), \
             patch.object(sheets.raid_sheets, "_session", return_value=MagicMock()), \
             patch.object(sheets, "_permanent_read", return_value=[[], self.attendance_row("Excused")]), \
             patch.object(sheets, "attendance_set") as set_result:
            sheets.attendance_complete(self.path, 1)
            set_result.assert_not_called()

    def test_character_board_attendance_uses_completed_raids_and_never_invents_parses_or_ilvl(self):
        attendance.mirror_rows(self.path, [self.attendance_row(selection="Bench")])
        schedule.raid_status(self.path, 1, "confirm", confirmed_entries=[("42", "Paladin", "Hyjal")])
        schedule.raid_status(self.path, 1, "archive")
        values = hub.rows(self.path)
        self.assertEqual(values[0]["attendance"], "100%")
        self.assertEqual(values[0]["ilvl"], "N/A")
        embed = hub.render(values)[0]
        self.assertNotIn("Warcraft Logs", str(embed.to_dict()))
        self.assertIn("100%", str(embed.to_dict()))

    def test_hub_shows_all_registered_characters_without_pagination(self):
        base = hub.rows(self.path)[0]
        values = [dict(base, name=f"Character {i:02}") for i in range(19)]
        embeds = hub.render(values)
        self.assertEqual(len(embeds), 1)
        content = str(embeds[0].to_dict())
        for row in values:
            self.assertIn(row["name"], content)
        self.assertNotIn("Page", content)

    def test_archive_hides_only_completed_raid_tab_and_preserves_permanent_history(self):
        tabs = {"Raid #1 - Wed": {"sheetId": 1}, "Raid #2 - Sun": {"sheetId": 2},
                "Forever Attendance": {"sheetId": 3}, "Forever Roster": {"sheetId": 4}}
        raids = {1: {"archived_at": "done"}, 2: {"archived_at": None}}
        with patch.object(sheets.raid_sheets, "_json", return_value={}) as api:
            sheets._hide_archived(object(), tabs, raids)
            requests = api.call_args.kwargs["json"]["requests"]
            self.assertEqual(requests, [{"updateSheetProperties": {
                "properties": {"sheetId": 1, "hidden": True}, "fields": "hidden"}}])
            api.reset_mock()
            sheets._hide_archived(object(), tabs, raids)
            api.assert_not_called()
        self.assertNotIn("hidden", tabs["Forever Attendance"])

    def test_archive_never_hides_the_only_visible_worksheet(self):
        with patch.object(sheets.raid_sheets, "_json") as api:
            sheets._hide_archived(object(), {"Raid #1 - Wed": {"sheetId": 1}}, {1: {"archived_at": "done"}})
            api.assert_not_called()


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 19:00", "Hyjal (20m)", wowsims_policy="optional")
        raid.save_absence(1, 42, "Appointment", self.path)
        self.patch_db = patch.object(raid, "RAID_DB", self.path)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.leader = MagicMock(spec=discord.Role)
        self.leader.id, self.leader.mentionable = LEADERSHIP_ROLE_ID, True
        self.everyone = MagicMock(spec=discord.Role)
        self.everyone.id = GUILD_ID
        self.guild = SimpleNamespace(id=GUILD_ID, default_role=self.everyone, me=SimpleNamespace(id=200),
                                     get_role=lambda ident: self.leader)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.guild = self.guild
        self.channel.overwrites = {self.everyone: discord.PermissionOverwrite(view_channel=False),
                                   self.leader: discord.PermissionOverwrite(view_channel=True)}
        def permissions(target):
            return (discord.Permissions.none() if target is self.everyone else
                    discord.Permissions(view_channel=True, send_messages=True, read_message_history=True,
                                        embed_links=True, mention_everyone=True))
        self.channel.permissions_for.side_effect = permissions
        self.sent = []
        async def history(**kwargs):
            for message in self.sent:
                yield message
        async def send(**kwargs):
            message = SimpleNamespace(id=500 + len(self.sent), author=SimpleNamespace(id=200), embeds=[kwargs["embed"]])
            self.sent.append(message)
            return message
        self.channel.history = history
        self.channel.send = AsyncMock(side_effect=send)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=200), get_channel=lambda ident: self.channel)
        self.cog = notifications.RaidNotifications(self.bot)

    async def test_private_alert_pings_only_leadership_and_is_not_resent(self):
        self.assertEqual(await self.cog.flush(), 1)
        self.assertEqual(await self.cog.flush(), 0)
        call = self.channel.send.call_args.kwargs
        self.assertIn(str(LEADERSHIP_ROLE_ID), call["content"])
        self.assertFalse(call["allowed_mentions"].users)
        self.assertFalse(call["allowed_mentions"].everyone)
        self.assertEqual(call["allowed_mentions"].roles, [self.leader])

    async def test_failed_delivery_retains_event_and_unknown_send_is_recovered_from_history(self):
        with patch.object(self.cog, "record", side_effect=sqlite3.OperationalError("after-send failure")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.cog.flush()
        self.assertEqual(await self.cog.flush(), 0)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.cog.read_pending(), [])

    async def test_public_or_wrong_guild_channel_is_rejected_without_posting(self):
        self.channel.permissions_for.return_value = discord.Permissions.all()
        self.channel.permissions_for.side_effect = None
        with self.assertRaises(ValueError):
            await self.cog.flush()
        self.channel.send.assert_not_awaited()
        self.assertEqual(len(self.cog.read_pending()), 1)

    async def test_deleted_raid_cannot_emit_a_stale_ping(self):
        with raid.database(self.path) as con:
            con.execute("DELETE FROM raid_absences")
            con.execute("DELETE FROM raids")
        self.assertEqual(await self.cog.flush(), 0)
        self.channel.send.assert_not_awaited()


class LfgLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "lfg.db")
        self.patcher = patch.object(lfg, "PATH", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        lfg.init_db()

    def test_live_mode_is_opt_in_and_preserves_launch_plans_and_dungeons(self):
        with lfg.database() as con:
            con.execute("INSERT INTO lfg_listings (channel_id,user_id,character,class_name,role,availability,activity) "
                        "VALUES (1,42,'Launch plan','Paladin','DPS','Launch night','Open / Flexible')")
            lfg.lfg_dungeons.save_request(con, 1, 43, 8, ["Ragefire Chasm"], "", "Tonight")
            self.assertEqual(live.mode(con, 1), "launch")
            live.save(con, 1, 42, 7, "Questing", "Tonight UK", "Two friends", "", "")
            live.set_mode(con, 1, "live")
        self.assertEqual([r["_kind"] for r in lfg.board_entries(1)], ["dungeon", "live"])
        lfg.init_db()
        with lfg.database() as con:
            self.assertEqual(live.mode(con, 1), "live")
            live.set_mode(con, 1, "launch")
            self.assertEqual(len(live.listings(con, 1)), 1)
        self.assertEqual([r["_kind"] for r in lfg.board_entries(1)], ["dungeon", "launch"])

    def test_live_layout_uses_normal_group_form_without_launch_date_prompts(self):
        with lfg.database() as con:
            live.set_mode(con, 1, "live")
            live.save(con, 1, 42, 7, "STV questing", "Tonight 19:00 UK", "Healer", "", "")
        with patch.object(lfg, "shared_character", return_value=(7,"Player","Paladin","Retribution","DPS",None)):
            view = lfg.BoardLayout(SimpleNamespace(), 1)
        text = str(view.to_components())
        self.assertIn("STV questing", text)
        self.assertNotIn("launch groups", text)
        self.assertNotIn("Launch 23:00", text)
        self.assertIn("hyped_lfg_live_plan_v1", text)
        self.assertTrue(view.is_persistent())

    async def test_live_remove_is_scoped_and_does_not_delete_other_member_plans(self):
        with lfg.database() as con:
            live.save(con, 1, 42, 7, "Quest", "Now", "", "", "")
            live.save(con, 1, 43, 8, "Quest", "Now", "", "", "")
        from hyped_ogre.features.lfg.live_ui import RemoveLivePlan
        cog = SimpleNamespace(refresh=AsyncMock())
        interaction = SimpleNamespace(guild_id=77,user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        with patch.dict(os.environ, {"HYPED_GUILD_ID": "77"}):
            await RemoveLivePlan(cog, 1).callback(interaction)
        with lfg.database() as con:
            self.assertEqual([r["user_id"] for r in live.listings(con,1)], [43])
        cog.refresh.assert_awaited_once_with(1)

    def test_shared_character_used_in_live_plan_cannot_be_deleted(self):
        path = str(Path(self.tmp.name) / "characters.db")
        raid.create_schema(path)
        raid.save_character(42,"Player","Paladin","Retribution","DPS",path)
        cid=raid.member_characters(42,path)[0][0]
        with lfg.database() as con:
            live.save(con,1,42,cid,"Quest","Now","","","")
        with patch.object(raid,"RAID_DB",path),patch.dict(os.environ,{"LFG_DB_PATH":self.path}):
            with self.assertRaisesRegex(ValueError,"used on the LFG board"):
                raid.delete_character(42,cid,path)
        self.assertEqual(len(raid.member_characters(42,path)),1)


class HubCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_offspec_picker_saves_owned_feral_tank_and_dispatches_access_update(self):
        from hyped_ogre.features.characters.offspec_ui import OffspecView
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/"raid.db")
            raid.create_schema(path)
            raid.save_character(42,"Druid","Druid","Feral","DPS",path)
            cid=raid.member_characters(42,path)[0][0]
            interaction=SimpleNamespace(guild_id=GUILD_ID,user=SimpleNamespace(id=42),
                client=SimpleNamespace(dispatch=MagicMock()),
                response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock()))
            with patch.object(raid,"RAID_DB",path),patch.object(raid,"GUILD_ID",GUILD_ID):
                view=OffspecView(42,cid,"Druid")
                view.children[0]._values=["Feral"]
                await view.children[0].callback(interaction)
                view.children[0]._values=["Tank"]
                await view.children[0].callback(interaction)
            with raid.database(path) as con:
                self.assertEqual(profiles.offspec(con,42,cid),("Druid","Feral","Tank"))
            interaction.client.dispatch.assert_called_once_with("classroom_character_changed",GUILD_ID,42)
            self.assertIsNone(interaction.response.edit_message.call_args.kwargs["view"])

    async def test_characters_command_in_any_channel_opens_private_shared_editor(self):
        bot = SimpleNamespace(get_cog=lambda name: raid.RaidPrototype(SimpleNamespace()))
        cog = hub.CharacterHub(bot)
        for channel_id in (100, 999):
            interaction = SimpleNamespace(guild_id=GUILD_ID, channel_id=channel_id, user=SimpleNamespace(id=42),
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
            with patch.object(raid, "GUILD_ID", GUILD_ID), patch.object(raid, "member_characters", return_value=[]):
                await cog.characters.callback(cog, interaction)
            self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_character_command_wrong_guild_does_not_open_register(self):
        cog = hub.CharacterHub(SimpleNamespace(get_cog=MagicMock()))
        interaction = SimpleNamespace(guild_id=77, response=SimpleNamespace(send_message=AsyncMock()))
        await cog.characters.callback(cog, interaction)
        cog.bot.get_cog.assert_not_called()

    async def test_new_extensions_load_and_unload_without_a_token_or_duplicate_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            with patch.dict(os.environ, {"HYPED_GUILD_ID": str(GUILD_ID)}), patch.object(raid, "RAID_DB", path):
                async with commands.Bot(command_prefix="test_", intents=discord.Intents.none()) as bot:
                    for _ in range(2):
                        for module in ("hyped_ogre.features.characters.hub", "hyped_ogre.features.raids.notifications"):
                            await bot.load_extension(module)
                        self.assertIsNotNone(bot.tree.get_command("characters", guild=discord.Object(id=GUILD_ID)))
                        self.assertIsNotNone(bot.tree.get_command("postcharacters", guild=discord.Object(id=GUILD_ID)))
                        await bot.unload_extension("hyped_ogre.features.characters.hub")
                        await bot.unload_extension("hyped_ogre.features.raids.notifications")
                        self.assertIsNone(bot.tree.get_command("characters", guild=discord.Object(id=GUILD_ID)))

    async def test_postcharacters_refreshes_one_persistent_board_without_pinging_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/"raid.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            channel=MagicMock(spec=discord.TextChannel)
            channel.id=300
            channel.guild=SimpleNamespace(id=GUILD_ID,owner_id=42)
            channel.send=AsyncMock(return_value=SimpleNamespace(id=500))
            message=SimpleNamespace(edit=AsyncMock())
            channel.get_partial_message.return_value=message
            interaction=SimpleNamespace(guild_id=GUILD_ID,guild=channel.guild,channel=channel,
                user=SimpleNamespace(id=42,roles=[]),response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
            with patch.object(raid,"RAID_DB",path),patch.dict(os.environ,{"HYPED_GUILD_ID":str(GUILD_ID)}):
                async with commands.Bot(command_prefix="test_",intents=discord.Intents.none()) as bot:
                    with patch.object(bot,"get_channel",return_value=channel):
                        cog=hub.CharacterHub(bot)
                        await cog.cog_load()
                        try:
                            await cog.post_characters.callback(cog,interaction)
                            await cog.post_characters.callback(cog,interaction)
                            channel.send.assert_awaited_once()
                            message.edit.assert_awaited_once()
                            self.assertFalse(channel.send.call_args.kwargs["allowed_mentions"].users)
                            self.assertTrue(channel.send.call_args.kwargs["view"].is_persistent())
                        finally:
                            await cog.cog_unload()


class RosterReactionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_ping_correct_check_reaction_and_edit_without_reping(self):
        from cogs import raid_acknowledgements as ack
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            schedule.save_raid(path, 1, "2026-12-09 19:00", "Hyjal (20m)", wowsims_policy="optional")
            raid.save_character(42, "Paladin", "Paladin", "Protection", "Tank", path)
            cid = raid.member_characters(42, path)[0][0]
            raid.save_signup(1,42,cid,path)
            schedule.record_panel(path,1,300,400)
            schedule.raid_status(path,1,"confirm",confirmed_entries=[("42","Paladin","Hyjal")])
            message = SimpleNamespace(id=500, jump_url="https://discord.com/message", content="", add_reaction=AsyncMock())
            async def edit(**kwargs):
                message.content = kwargs["content"]
            message.edit = AsyncMock(side_effect=edit)
            channel = MagicMock(spec=discord.TextChannel)
            channel.guild = SimpleNamespace(id=GUILD_ID,me=SimpleNamespace(id=200))
            channel.permissions_for.return_value = discord.Permissions.all()
            async def send(**kwargs):
                message.content = kwargs["content"]
                return message
            channel.send = AsyncMock(side_effect=send)
            channel.fetch_message = AsyncMock(return_value=message)
            bot = SimpleNamespace(user=SimpleNamespace(id=200),get_channel=lambda ident: channel)
            await announcer.announce(bot,path,1,GUILD_ID,selected=[("42","Paladin"),("43","Mage")])
            message.add_reaction.assert_awaited_once_with(ack.ACK_EMOJI)
            for user_id, channel_id, emoji in ((99,300,ack.ACK_EMOJI),(42,999,ack.ACK_EMOJI),(42,300,"bad")):
                payload=SimpleNamespace(guild_id=GUILD_ID,channel_id=channel_id,message_id=500,user_id=user_id,emoji=emoji)
                self.assertFalse(await announcer.reaction(bot,path,payload,GUILD_ID))
            payload=SimpleNamespace(guild_id=GUILD_ID,channel_id=300,message_id=500,user_id=42,emoji=ack.ACK_EMOJI)
            self.assertTrue(await announcer.reaction(bot,path,payload,GUILD_ID))
            self.assertNotIn("<@42>",message.content)
            self.assertIn("<@43>",message.content)
            self.assertFalse(message.edit.call_args.kwargs["allowed_mentions"].users)
            self.assertFalse(await announcer.reaction(bot,path,payload,GUILD_ID))
            channel.send.assert_awaited_once()
            self.assertEqual(raid.my_signup(1,42,path),("Paladin","Available"))
            self.assertTrue(schedule.read_raid(path,1)["signup_open"])


if __name__ == "__main__":
    unittest.main()
