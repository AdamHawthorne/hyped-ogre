"""Offline tests using real discord.py permission models and temporary databases."""

import asyncio
import copy
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms import config, extension
from hyped_ogre.features.classrooms.store import ClassroomError, ClassroomStore


CHANNEL_IDS = {c.key: c.channel_id for c in config.CHANNELS}
PLAYER = 100
BOT = 200
OWNER = 300


def user_data(ident, *, bot=False):
    return {"id": str(ident), "username": f"Member{ident}", "discriminator": "0",
            "avatar": None, "bot": bot}


def member_data(ident, roles=(), *, bot=False):
    return {"user": user_data(ident, bot=bot), "roles": [str(r) for r in roles],
            "joined_at": "2026-11-03T00:00:00+00:00", "flags": 0}


def overwrite_data(ident, kind, **permissions):
    allow, deny = discord.PermissionOverwrite(**permissions).pair()
    return {"id": str(ident), "type": kind,
            "allow": str(allow.value), "deny": str(deny.value)}


class ClassroomFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "raid_signup.db"
        raid.create_schema(str(self.path))
        self.store = ClassroomStore(self.path)
        self.store.initialise()
        self.bot = commands.Bot(command_prefix="test_", intents=discord.Intents.none())
        self.addAsyncCleanup(self.bot.close)
        state = self.bot._connection
        state.user = discord.ClientUser(state=state, data=user_data(BOT, bot=True))
        base = discord.Permissions(view_channel=True, send_messages=True,
                                   read_message_history=True, add_reactions=True,
                                   embed_links=True, attach_files=True)
        bot_perms = discord.Permissions(manage_roles=True, manage_channels=True)
        roles = [
            {"id": str(config.GUILD_ID), "name": "@everyone", "permissions": str(base.value)},
            {"id": str(config.BOT_ROLE_ID), "name": "Bot", "position": 2,
             "permissions": str(bot_perms.value), "managed": True},
            {"id": str(config.LEADERSHIP_ROLE_ID), "name": "Leadership", "position": 3,
             "permissions": "0"},
            {"id": "900", "name": "Forever", "position": 1, "permissions": "0"},
        ]
        self.guild = discord.Guild(state=state, data={
            "id": str(config.GUILD_ID), "name": "Hyped", "owner_id": str(OWNER),
            "roles": roles, "members": [member_data(BOT, [config.BOT_ROLE_ID], bot=True),
                                        member_data(PLAYER), member_data(OWNER)],
            "channels": [], "member_count": 3,
        })
        state._guilds[self.guild.id] = self.guild
        # Intents.none() deliberately disables automatic joined-member caching.
        for data in (member_data(BOT, [config.BOT_ROLE_ID], bot=True),
                     member_data(PLAYER), member_data(OWNER)):
            member = discord.Member(data=data, guild=self.guild, state=state)
            self.guild._members[member.id] = member
        self.remote_members = dict(self.guild._members)
        self.wire = {}
        self.wire[config.CATEGORY_ID] = {
            "id": str(config.CATEGORY_ID), "name": "forever-classrooms", "type": 4, "position": 0,
            "permission_overwrites": [],
        }
        for item in config.CHANNELS:
            self.wire[item.channel_id] = {
                "id": str(item.channel_id), "name": item.key, "type": 0, "position": 0,
                "parent_id": str(config.CATEGORY_ID), "permission_overwrites": [],
            }
        for ident in self.wire:
            self.put(ident, config.GUILD_ID, 0, view_channel=False)
            self.put(ident, config.BOT_ROLE_ID, 0, view_channel=True,
                     send_messages=True, read_message_history=True, manage_roles=True)

        async def fetch_channels(guild):
            return self.channels()

        async def fetch_member(guild, ident):
            if ident not in self.remote_members:
                raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"),
                                       {"code": 10007, "message": "Unknown Member"})
            return self.remote_members[ident]

        async def edit(channel_id, target_id, allow, deny, kind, *, reason=None):
            self.put_raw(channel_id, {"id": str(target_id), "type": kind,
                                      "allow": allow, "deny": deny})

        async def delete(channel_id, target_id, *, reason=None):
            self.wire[channel_id]["permission_overwrites"] = [
                ow for ow in self.wire[channel_id]["permission_overwrites"]
                if int(ow["id"]) != target_id
            ]

        for name, value in (("fetch_channels", fetch_channels), ("fetch_member", fetch_member)):
            self.addCleanup(patch.stopall)
            patch.object(discord.Guild, name, value).start()
        self.http_edit = patch.object(self.bot.http, "edit_channel_permissions",
                                      AsyncMock(side_effect=edit)).start()
        self.http_delete = patch.object(self.bot.http, "delete_channel_permissions",
                                        AsyncMock(side_effect=delete)).start()
        self.cog = extension.ClassroomsCog(self.bot, path=self.path)
        self.store.set_enabled(config.GUILD_ID, True)

    def put_raw(self, channel_id, data):
        values = self.wire[channel_id]["permission_overwrites"]
        values[:] = [ow for ow in values if ow["id"] != data["id"]]
        values.append(data)

    def put(self, channel_id, target_id, kind=1, **permissions):
        self.put_raw(channel_id, overwrite_data(target_id, kind, **permissions))

    def channels(self):
        result = []
        for data in self.wire.values():
            cls = discord.CategoryChannel if data["type"] == 4 else discord.TextChannel
            result.append(cls(state=self.bot._connection, guild=self.guild, data=copy.deepcopy(data)))
        return result

    def channel(self, key):
        return next(c for c in self.channels() if c.id == CHANNEL_IDS[key])

    def current(self, key):
        return self.channel(key).overwrites_for(self.remote_members[PLAYER])

    def character(self, name="Snore", klass="Warrior", spec="Protection", role="Tank"):
        raid.save_character(PLAYER, name, klass, spec, role, str(self.path))
        return next(r[0] for r in raid.member_characters(PLAYER, str(self.path)) if r[1] == name)

    async def test_existing_main_alt_union_grants_correct_channels_and_is_idempotent(self):
        self.character()
        self.character("Alt", "Shaman", "Restoration", "Healer")
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.removed, result.errors), (4, 0, []))
        for key in CHANNEL_IDS:
            self.assertEqual(self.current(key).view_channel,
                             True if key in {"warriors", "shamans", "tanks", "healers"} else None)
        self.assertTrue(self.channel("warriors").permissions_for(self.remote_members[PLAYER]).send_messages)
        again = await self.cog.sync_access(self.guild)
        self.assertEqual(again.added, 0)
        self.assertEqual(self.http_edit.await_count, 4)
        self.assertEqual(len(self.store.snapshot(config.GUILD_ID)[1]), 4)

    async def test_duplicate_alts_remain_until_last_qualifying_character_removed(self):
        first = self.character()
        second = self.character("Alt")
        await self.cog.sync_access(self.guild)
        raid.delete_character(PLAYER, first, str(self.path))
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.removed, 0)
        self.assertTrue(self.current("tanks").view_channel)
        raid.delete_character(PLAYER, second, str(self.path))
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.removed, 2)
        self.assertTrue(self.current("tanks").is_empty())
        self.assertEqual(self.store.snapshot(config.GUILD_ID)[1], {})

    async def test_tank_main_with_ranged_and_melee_alts_keeps_dps_until_last_dps_removed(self):
        self.character()
        ranged = self.character("Mage Alt", "Mage", "Fire", "DPS")
        melee = self.character("Warrior Alt", "Warrior", "Fury", "DPS")
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.errors), (4, []))
        self.assertTrue(self.current("dps").view_channel)
        self.assertTrue(self.current("tanks").view_channel)
        raid.delete_character(PLAYER, ranged, str(self.path))
        await self.cog.sync_access(self.guild)
        self.assertTrue(self.current("dps").view_channel)
        raid.delete_character(PLAYER, melee, str(self.path))
        await self.cog.sync_access(self.guild)
        self.assertIsNone(self.current("dps").view_channel)
        self.assertTrue(self.current("tanks").view_channel)
        self.assertTrue(self.current("warriors").view_channel)

    async def test_ranged_melee_and_survival_profiles_share_dps_and_role_change_removes_it(self):
        profiles = (
            ("Mage", "Fire"), ("Warrior", "Fury"), ("Rogue", "Combat"),
            ("Druid", "Feral"), ("Hunter", "Survival"), ("Shaman", "Elemental"),
        )
        for klass, spec in profiles:
            with self.subTest(klass=klass, spec=spec):
                ident = self.character("Damage", klass, spec, "DPS")
                await self.cog.sync_access(self.guild)
                self.assertTrue(self.current("dps").view_channel)
                self.assertIsNone(self.current("tanks").view_channel)
                self.assertIsNone(self.current("healers").view_channel)
        raid.update_character_profile(PLAYER, ident, "Shaman", "Restoration", "Healer", str(self.path))
        await self.cog.sync_access(self.guild)
        self.assertIsNone(self.current("dps").view_channel)
        self.assertTrue(self.current("healers").view_channel)
        self.assertTrue(self.current("shamans").view_channel)

    async def test_profile_change_revokes_previous_class_and_role_without_gear_requirement(self):
        ident = self.character()
        await self.cog.sync_access(self.guild)
        raid.update_character_profile(PLAYER, ident, "Priest", "Holy", "Healer", str(self.path))
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.removed), (2, 2))
        self.assertIsNone(self.current("warriors").view_channel)
        self.assertIsNone(self.current("tanks").view_channel)
        self.assertTrue(self.current("priests").view_channel)
        self.assertTrue(self.current("healers").view_channel)

    async def test_feral_dps_does_not_get_tank_and_invalid_role_cannot_get_healer(self):
        self.character("Cat", "Druid", "Feral", "DPS")
        # Simulate a legacy/manual inconsistent row, bypassing registration validation.
        with self.store.connection() as con:
            con.execute("INSERT INTO characters (discord_id,name,class_name,spec,role) "
                        "VALUES (?,?,?,?,?)", (PLAYER, "Shadow", "Priest", "Shadow", "Healer"))
        await self.cog.sync_access(self.guild)
        self.assertTrue(self.current("druids").view_channel)
        self.assertTrue(self.current("priests").view_channel)
        self.assertIsNone(self.current("tanks").view_channel)
        self.assertIsNone(self.current("healers").view_channel)

    async def test_manual_allow_denies_and_unrelated_flags_survive_removal(self):
        ident = self.character()
        self.put(CHANNEL_IDS["warriors"], PLAYER, view_channel=True, attach_files=False)
        self.put(CHANNEL_IDS["tanks"], PLAYER, send_messages=False, manage_messages=True)
        await self.cog.sync_access(self.guild)
        self.assertFalse(self.current("tanks").send_messages)
        raid.delete_character(PLAYER, ident, str(self.path))
        await self.cog.sync_access(self.guild)
        self.assertTrue(self.current("warriors").view_channel)
        self.assertFalse(self.current("warriors").attach_files)
        self.assertIsNone(self.current("warriors").send_messages)
        self.assertFalse(self.current("tanks").send_messages)
        self.assertTrue(self.current("tanks").manage_messages)
        self.assertIsNone(self.current("tanks").view_channel)

    async def test_manual_view_deny_is_not_overridden_or_owned(self):
        self.character()
        self.put(CHANNEL_IDS["tanks"], PLAYER, view_channel=False)
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.blocked, 1)
        self.assertFalse(self.current("tanks").view_channel)
        self.assertNotIn((CHANNEL_IDS["tanks"], PLAYER), self.store.snapshot(config.GUILD_ID)[1])

    async def test_moderator_edit_to_active_grant_is_not_fought(self):
        self.character()
        await self.cog.sync_access(self.guild)
        self.put(CHANNEL_IDS["tanks"], PLAYER, view_channel=None,
                 send_messages=True, read_message_history=True, attach_files=False)
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.blocked, 1)
        self.assertIsNone(self.current("tanks").view_channel)
        self.assertFalse(self.current("tanks").attach_files)

    async def test_category_resync_removing_member_entries_is_repaired_after_restart(self):
        self.character()
        await self.cog.sync_access(self.guild)
        for key in ("tanks", "warriors"):
            # Discord Sync Now copies the category's role overwrites and
            # removes the individual members Ogre had previously added.
            self.wire[CHANNEL_IDS[key]]["permission_overwrites"] = copy.deepcopy(
                self.wire[config.CATEGORY_ID]["permission_overwrites"])
        restarted = extension.ClassroomsCog(self.bot, path=self.path)
        result = await restarted.sync_access(self.guild)
        self.assertEqual((result.added, result.errors), (2, []))
        for key in ("tanks", "warriors"):
            self.assertTrue(self.current(key).view_channel)
            self.assertTrue(self.current(key).send_messages)
            self.assertTrue(self.current(key).read_message_history)
        self.assertEqual(len(self.store.snapshot(config.GUILD_ID)[1]), 2)
        self.assertEqual((await restarted.sync_access(self.guild)).added, 0)

    async def test_missing_entry_without_qualifying_character_is_not_restored(self):
        ident = self.character()
        await self.cog.sync_access(self.guild)
        raid.delete_character(PLAYER, ident, str(self.path))
        for key in ("tanks", "warriors"):
            self.wire[CHANNEL_IDS[key]]["permission_overwrites"] = copy.deepcopy(
                self.wire[config.CATEGORY_ID]["permission_overwrites"])
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.errors), (0, []))
        self.assertIsNone(self.current("tanks").view_channel)
        self.assertEqual(self.store.snapshot(config.GUILD_ID)[1], {})

    async def test_explicit_member_deny_and_existing_empty_override_survive_reset_repair(self):
        self.character()
        await self.cog.sync_access(self.guild)
        self.put(CHANNEL_IDS["tanks"], PLAYER, view_channel=False)
        self.put(CHANNEL_IDS["warriors"], PLAYER)
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.blocked, result.errors), (0, 2, []))
        self.assertFalse(self.current("tanks").view_channel)
        self.assertIsNone(self.current("warriors").view_channel)

    async def test_crash_after_discord_grant_before_journal_ack_recovers_after_restart(self):
        self.character()
        with patch.object(self.cog.store, "grant_applied", side_effect=sqlite3.OperationalError("crash")):
            result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.added, 2)
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(all(pending for owned, pending in self.store.snapshot(config.GUILD_ID)[1].values()))
        restarted = extension.ClassroomsCog(self.bot, path=self.path)
        result = await restarted.sync_access(self.guild)
        self.assertEqual((result.added, result.errors), (0, []))
        self.assertTrue(all(not pending for owned, pending in self.store.snapshot(config.GUILD_ID)[1].values()))
        self.assertEqual(self.http_edit.await_count, 2)

    async def test_failed_grant_is_retried_and_failed_removal_retains_journal(self):
        ident = self.character()
        normal_edit = self.http_edit.side_effect
        self.http_edit.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(len(result.errors), 2)
        self.assertEqual(len(self.store.snapshot(config.GUILD_ID)[1]), 2)
        self.http_edit.side_effect = normal_edit
        self.assertEqual((await self.cog.sync_access(self.guild)).added, 2)
        raid.delete_character(PLAYER, ident, str(self.path))
        normal_delete = self.http_delete.side_effect
        self.http_delete.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(len(result.errors), 2)
        self.assertEqual(len(self.store.snapshot(config.GUILD_ID)[1]), 2)
        self.http_delete.side_effect = normal_delete
        self.assertEqual((await self.cog.sync_access(self.guild)).removed, 2)

    async def test_uncached_member_resolves_without_members_intent_and_fetch_error_does_not_revoke(self):
        self.character()
        self.guild._members.pop(PLAYER)
        self.assertEqual((await self.cog.sync_access(self.guild)).added, 2)
        with patch.object(discord.Guild, "fetch_member", AsyncMock(side_effect=discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "Unavailable"))):
            result = await self.cog.sync_access(self.guild)
        self.assertEqual(result.removed, 0)
        self.assertTrue(self.current("tanks").view_channel)

    async def test_departed_account_is_not_granted_and_old_grants_are_removed(self):
        self.character()
        await self.cog.sync_access(self.guild)
        self.guild._members.pop(PLAYER)
        self.remote_members.pop(PLAYER)
        self.bot.fetch_user = AsyncMock(return_value=discord.User(
            state=self.bot._connection, data=user_data(PLAYER)))
        result = await self.cog.sync_access(self.guild)
        self.assertEqual((result.added, result.removed, result.errors), (0, 2, []))
        self.assertEqual(self.store.snapshot(config.GUILD_ID)[1], {})
        self.assertEqual((await self.cog.sync_access(self.guild)).added, 0)

    async def test_setup_installs_leadership_and_does_not_change_member_overwrites(self):
        self.put(CHANNEL_IDS["warriors"], PLAYER, attach_files=False, view_channel=True)
        before = self.current("warriors")
        await self.cog.configure(self.guild)
        self.assertEqual(self.current("warriors"), before)
        leadership = self.guild.get_role(config.LEADERSHIP_ROLE_ID)
        for channel in self.channels():
            self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
            self.assertTrue(channel.overwrites_for(leadership).view_channel)
        writes = self.http_edit.await_count
        await self.cog.configure(self.guild)
        self.assertEqual(self.http_edit.await_count, writes)
        self.assertTrue(self.store.enabled(config.GUILD_ID))

    async def test_setup_preflight_rejects_missing_bot_access_without_partial_writes(self):
        self.store.set_enabled(config.GUILD_ID, False)
        self.put(CHANNEL_IDS["tanks"], config.BOT_ROLE_ID, 0, view_channel=False)
        with self.assertRaisesRegex(ClassroomError, "View Channel"):
            await self.cog.configure(self.guild)
        self.assertFalse(self.store.enabled(config.GUILD_ID))
        self.http_edit.assert_not_awaited()

    async def test_setup_failure_stays_disabled_and_can_be_repeated(self):
        normal = self.http_edit.side_effect
        self.http_edit.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        with self.assertRaises(discord.Forbidden):
            await self.cog.configure(self.guild)
        self.assertFalse(self.store.enabled(config.GUILD_ID))
        self.http_edit.side_effect = normal
        await self.cog.configure(self.guild)
        self.assertTrue(self.store.enabled(config.GUILD_ID))

    async def test_public_role_allow_blocks_setup_and_sync(self):
        self.character()
        self.put(CHANNEL_IDS["tanks"], 900, 0, view_channel=True)
        with self.assertRaisesRegex(ClassroomError, "Forever"):
            await self.cog.configure(self.guild)
        result = await self.cog.sync_access(self.guild)
        self.assertEqual(len(result.errors), 1)
        self.assertIsNone(self.current("tanks").view_channel)

    async def test_moved_destination_fails_closed_before_any_writes(self):
        self.character()
        self.wire[CHANNEL_IDS["tanks"]]["parent_id"] = "999"
        with self.assertRaisesRegex(ClassroomError, "Missing or moved"):
            await self.cog.sync_access(self.guild)
        self.http_edit.assert_not_awaited()

    async def test_paused_other_guild_and_nonselected_member_do_not_change_access(self):
        self.character()
        await self.cog.sync_access(self.guild, member_id=999)
        self.http_edit.assert_not_awaited()
        self.store.set_enabled(config.GUILD_ID, False)
        await self.cog.sync_access(self.guild)
        await self.cog.sync_access(SimpleNamespace(id=77))
        self.http_edit.assert_not_awaited()

    async def test_concurrent_syncs_do_not_duplicate_grants(self):
        self.character()
        results = await asyncio.gather(*(self.cog.sync_access(self.guild) for _ in range(4)))
        self.assertEqual(sum(r.added for r in results), 2)
        self.assertEqual(self.http_edit.await_count, 2)

    async def test_owner_leadership_and_wrong_guild_authorization(self):
        def interaction(ident, role_ids=(), guild_id=config.GUILD_ID):
            return SimpleNamespace(guild_id=guild_id, guild=self.guild,
                                   user=SimpleNamespace(id=ident,
                                                        roles=[SimpleNamespace(id=r) for r in role_ids]))
        with patch.dict(os.environ, {"OWNER_ID": "400"}):
            self.assertTrue(self.cog.allowed(interaction(400), owner_only=True))
            self.assertTrue(self.cog.allowed(interaction(OWNER), owner_only=True))
            self.assertTrue(self.cog.allowed(interaction(PLAYER, [config.LEADERSHIP_ROLE_ID])))
            self.assertFalse(self.cog.allowed(interaction(PLAYER, [config.LEADERSHIP_ROLE_ID]), owner_only=True))
            self.assertFalse(self.cog.allowed(interaction(PLAYER)))
            self.assertFalse(self.cog.allowed(interaction(400, guild_id=77)))

    async def test_read_only_commands_leave_register_and_sheet_data_unchanged(self):
        self.character()
        with closing(sqlite3.connect(self.path)) as con:
            before = list(con.iterdump())
        interaction = SimpleNamespace(guild=self.guild, response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        await self.cog.status.callback(self.cog, interaction)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(list(con.iterdump()), before)
        interaction.followup.send.assert_awaited_once()
        self.assertIn("Registered accounts", interaction.followup.send.call_args.args[0])
        self.http_edit.assert_not_awaited()


class ClassroomLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_load_and_unload_preserve_single_command_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid_signup.db")
            raid.create_schema(path)
            with patch.dict(os.environ, {"HYPED_GUILD_ID": str(config.GUILD_ID), "RAID_DB_PATH": path}):
                async with commands.Bot(command_prefix="test_", intents=discord.Intents.none()) as bot:
                    for _ in range(2):
                        await bot.load_extension("hyped_ogre.features.classrooms.extension")
                        cog = bot.get_cog("ClassroomsCog")
                        self.assertTrue(cog.maintenance.is_running())
                        group = bot.tree.get_command("classrooms", guild=discord.Object(id=config.GUILD_ID))
                        self.assertEqual({c.name for c in group.commands}, {"setup", "sync", "status", "pause"})
                        self.assertFalse(cog.store.enabled(config.GUILD_ID))
                        await bot.unload_extension("hyped_ogre.features.classrooms.extension")
                        self.assertFalse(cog.maintenance.is_running())
                        self.assertIsNone(bot.tree.get_command("classrooms", guild=discord.Object(id=config.GUILD_ID)))

    async def test_shared_character_ui_dispatches_only_after_registration_edit_and_delete_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid_signup.db")
            raid.create_schema(path)
            snapshots = []

            def dispatched(event, guild_id, ident):
                self.assertEqual((event, guild_id, ident),
                                 ("classroom_character_changed", config.GUILD_ID, PLAYER))
                snapshots.append(raid.list_characters(ident, path))

            interaction = SimpleNamespace(
                guild_id=config.GUILD_ID, user=SimpleNamespace(id=PLAYER),
                client=SimpleNamespace(dispatch=dispatched),
                response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
            )
            # Redirect the original wrappers to a temporary register without
            # replacing their validation/commit behaviour with mock mutations.
            wrappers = {name: getattr(raid, name) for name in (
                "save_character", "member_characters", "main_character_ids", "character_race",
                "update_character_profile", "delete_character")}
            with ExitStack() as stack:
                stack.enter_context(patch.object(raid, "GUILD_ID", config.GUILD_ID))
                for name, original in wrappers.items():
                    stack.enter_context(patch.object(raid, name, side_effect=(
                        lambda *args, original=original: original(*args, path=path))))
                stack.enter_context(patch.object(raid, "refresh_character_signups", AsyncMock()))
                stack.enter_context(patch.object(raid, "refresh_lfg_for_member", AsyncMock()))
                modal = raid.CharacterNameModal(PLAYER, "Warrior", "Protection", "Tank")
                modal.name._value = "Snore"
                await modal.on_submit(interaction)
                ident = raid.member_characters(PLAYER)[0][0]
                await raid.apply_profile_update(interaction, PLAYER, ident, "Druid", "Restoration", "Healer")
                delete_view = raid.ConfirmCharacterDeleteView(PLAYER, ident, "Snore")
                confirm = next(item for item in delete_view.walk_children()
                               if getattr(item, "label", None) == "Confirm delete")
                await confirm.callback(interaction)
            self.assertEqual(snapshots, [
                [("Snore", "Warrior", "Protection", "Tank")],
                [("Snore", "Druid", "Restoration", "Healer")], [],
            ])

    async def test_long_permission_diagnostics_remain_private_and_within_discord_limit(self):
        interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        message = "\n".join("Permission problem " + str(i) + "x" * 150 for i in range(20))
        await extension.ClassroomsCog.send(interaction, message)
        calls = interaction.followup.send.await_args_list
        self.assertGreater(len(calls), 1)
        self.assertEqual("\n".join(call.args[0] for call in calls), message)
        for call in calls:
            self.assertLessEqual(len(call.args[0]), 1900)
            self.assertTrue(call.kwargs["ephemeral"])


class ClassroomStoreTests(unittest.TestCase):
    def test_wrong_database_does_not_create_or_initialise_a_character_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            with self.assertRaises(sqlite3.OperationalError):
                ClassroomStore(missing).initialise()
            self.assertFalse(missing.exists())
            empty = Path(tmp) / "empty.db"
            sqlite3.connect(empty).close()
            with self.assertRaises(sqlite3.OperationalError):
                ClassroomStore(empty).initialise()
            with closing(sqlite3.connect(empty)) as con:
                self.assertEqual(con.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_additive_schema_does_not_mutate_existing_rows_and_journal_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid_signup.db")
            raid.create_schema(path)
            raid.save_character(PLAYER, "Snore", "Warrior", "Protection", "Tank", path)
            with closing(sqlite3.connect(path)) as con:
                before = list(con.iterdump())
            store = ClassroomStore(path)
            store.initialise()
            store.initialise()
            with closing(sqlite3.connect(path)) as con:
                after = list(con.iterdump())
            self.assertTrue(all(line in after for line in before))
            self.assertFalse(store.enabled(config.GUILD_ID))
            store.prepare_grant(77, CHANNEL_IDS["tanks"], PLAYER, ("view_channel",))
            self.assertEqual(store.snapshot(config.GUILD_ID)[1], {})
            self.assertEqual(len(store.snapshot(77)[1]), 1)


if __name__ == "__main__":
    unittest.main()
