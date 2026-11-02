"""Offline tests for anonymous polls; never use a live token or bot database."""

import asyncio
import json
import os
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from hyped_ogre.features.polls import extension, ui
from hyped_ogre.features.polls.store import (
    PollError,
    PollStore,
    deadline_from_text,
    options_from_text,
)


class PollStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "polls.db"
        self.store = PollStore(self.path)
        self.store.initialise()
        self.ident = self.store.create(7, 99, 10, "Raid Sunday?")

    def opened(self, *, multiple=False, deadline=None):
        self.store.configure(7, self.ident, "public_channel_id", 20)
        if multiple:
            self.store.configure(7, self.ident, "multiple", True)
        if deadline:
            self.store.configure(7, self.ident, "closes_at", deadline)
        self.store.open(7, self.ident)
        self.store.message(7, self.ident, "public", 100)

    def test_draft_requires_destination_and_does_not_accept_votes(self):
        self.assertEqual(self.store.get(7, self.ident)["options"], ["Yes", "No"])
        self.assertEqual(self.store.get(7, self.ident)["visibility"], "hidden")
        with self.assertRaises(PollError):
            self.store.open(7, self.ident)
        with self.assertRaises(PollError):
            self.store.vote(7, self.ident, 123, 0)

    def test_repeat_vote_is_one_ballot_and_can_be_changed_or_removed(self):
        self.opened()
        for _ in range(5):
            self.store.vote(7, self.ident, 123, 0)
        self.assertEqual(self.store.get(7, self.ident)["counts"], [1, 0])
        self.assertEqual(self.store.vote(7, self.ident, 123, 1), [1])
        self.assertEqual(self.store.get(7, self.ident)["counts"], [0, 1])
        self.assertEqual(self.store.mine(7, self.ident, 123), [1])
        self.store.vote(7, self.ident, 123, remove=True)
        self.assertEqual(self.store.get(7, self.ident)["voters"], 0)

    def test_concurrent_clicks_cannot_duplicate_a_single_vote(self):
        self.opened()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda i: self.store.vote(7, self.ident, 123, i % 2), range(24)
                )
            )
        poll = self.store.get(7, self.ident)
        self.assertEqual(sum(poll["counts"]), 1)
        self.assertEqual(poll["voters"], 1)

    def test_concurrent_members_are_all_counted(self):
        self.opened()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda uid: self.store.vote(7, self.ident, uid, 0), range(100, 124)
                )
            )
        self.assertEqual(self.store.get(7, self.ident)["counts"], [24, 0])

    def test_multiple_choices_toggle_and_count_people_separately(self):
        self.opened(multiple=True)
        self.store.vote(7, self.ident, 123, 0)
        self.store.vote(7, self.ident, 123, 1)
        poll = self.store.get(7, self.ident)
        self.assertEqual(poll["counts"], [1, 1])
        self.assertEqual(poll["voters"], 1)
        self.assertEqual(self.store.vote(7, self.ident, 123, 0), [1])

    def test_restart_retains_vote_and_my_selection(self):
        self.opened()
        self.store.vote(7, self.ident, 123, 1)
        restarted = PollStore(self.path)
        restarted.initialise()
        self.assertEqual(restarted.mine(7, self.ident, 123), [1])
        self.assertEqual(restarted.get(7, self.ident)["counts"], [0, 1])

    def test_ballots_contain_no_raw_member_id_and_closed_poll_discards_linking_keys(
        self,
    ):
        self.opened()
        uid = 123456789012345678
        self.store.vote(7, self.ident, uid, 1)
        with self.store.connection() as con:
            ballots = [dict(r) for r in con.execute("SELECT * FROM ballots")]
        self.assertNotIn(str(uid), json.dumps(ballots))
        self.assertEqual(
            set(ballots[0]), {"guild_id", "poll_id", "voter_key", "option_id"}
        )
        self.assertNotIn("voter_secret", self.store.get(7, self.ident))
        self.store.close(7, self.ident)
        with self.store.connection() as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM ballots").fetchone()[0], 0
            )
            self.assertIsNone(
                con.execute("SELECT voter_secret FROM polls").fetchone()[0]
            )
        self.assertEqual(self.store.get(7, self.ident)["counts"], [0, 1])
        self.assertEqual(self.store.get(7, self.ident)["voters"], 1)

    def test_deadline_rejects_vote_at_exact_boundary_before_worker_closes(self):
        end = int(datetime.now(UTC).timestamp()) + 3600
        self.opened(deadline=end)
        self.store.vote(7, self.ident, 123, 0, now=end - 1)
        with self.assertRaises(PollError):
            self.store.vote(7, self.ident, 124, 0, now=end)
        self.assertFalse(self.store.close(7, self.ident, due_only=True, now=end - 1))
        self.assertTrue(self.store.close(7, self.ident, due_only=True, now=end))
        self.assertFalse(self.store.close(7, self.ident, due_only=True, now=end + 1))
        self.assertEqual(self.store.get(7, self.ident)["counts"], [1, 0])

    def test_vote_close_race_keeps_a_consistent_final_total(self):
        self.opened()

        def cast(uid):
            try:
                self.store.vote(7, self.ident, uid, 0)
                return 1
            except PollError:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            votes = [pool.submit(cast, uid) for uid in range(100, 130)]
            close = pool.submit(self.store.close, 7, self.ident)
            successful = sum(v.result() for v in votes)
            close.result()
        poll = self.store.get(7, self.ident)
        self.assertEqual(poll["voters"], successful)
        self.assertEqual(poll["counts"], [successful, 0])

    def test_other_guild_cannot_read_or_modify_poll(self):
        self.opened()
        for operation in (
            lambda: self.store.get(8, self.ident),
            lambda: self.store.vote(8, self.ident, 123, 0),
            lambda: self.store.close(8, self.ident),
            lambda: self.store.configure(8, self.ident, "visibility", "live"),
        ):
            with self.assertRaises(PollError):
                operation()

    def test_edit_is_locked_after_posting_and_options_are_validated(self):
        for value in (
            "Yes",
            "Yes\nyes",
            "x" * 81 + "\nNo",
            "\n".join(map(str, range(11))),
        ):
            with self.assertRaises(PollError):
                options_from_text(value)
        self.store.edit(
            7, self.ident, "Choose day", "Wednesday\nSunday", "Details", None
        )
        self.opened()
        for operation in (
            lambda: self.store.edit(
                7, self.ident, "Another question", "Yes\nNo", "", None
            ),
            lambda: self.store.configure(7, self.ident, "multiple", True),
            lambda: self.store.configure(7, self.ident, "public_channel_id", 30),
        ):
            with self.assertRaises(PollError):
                operation()
        self.store.configure(7, self.ident, "visibility", "live")
        self.assertEqual(self.store.get(7, self.ident)["visibility"], "live")

    def test_dirty_snapshot_cannot_clear_a_newer_vote(self):
        self.opened()
        self.store.configure(7, self.ident, "visibility", "live")
        revision = self.store.get(7, self.ident)["revision"]
        self.store.vote(7, self.ident, 123, 0)
        self.store.clean(7, self.ident, "public", revision)
        self.assertTrue(self.store.get(7, self.ident)["public_dirty"])

    def test_existing_non_poll_database_is_not_modified(self):
        path = Path(self.tmp.name) / "legacy.db"
        with closing(sqlite3.connect(path)) as con:
            con.execute("CREATE TABLE rolls(value INTEGER)")
            con.execute("INSERT INTO rolls VALUES(99)")
            con.commit()
        with self.assertRaises(PollError):
            PollStore(path).initialise()
        with closing(sqlite3.connect(path)) as con:
            self.assertEqual(con.execute("SELECT value FROM rolls").fetchone()[0], 99)

    def test_relative_dates_dst_and_invalid_deadlines(self):
        now = datetime(2026, 11, 2, 12, tzinfo=UTC)
        self.assertEqual(deadline_from_text("2h", now=now), int(now.timestamp()) + 7200)
        self.assertIsNone(deadline_from_text("", now=now))
        for value in (
            "0h",
            "999999999999999d",
            "yesterday",
            "2026-03-29 01:30",
            "2026-10-25 01:30",
        ):
            with self.subTest(value=value), self.assertRaises(PollError):
                deadline_from_text(value, now=datetime(2026, 1, 1, tzinfo=UTC))
        self.assertIsInstance(
            deadline_from_text(
                "2026-10-25 01:30+00:00", now=datetime(2026, 10, 24, tzinfo=UTC)
            ),
            int,
        )


class PollUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PollStore(Path(self.tmp.name) / "polls.db")
        self.store.initialise()
        self.ident = self.store.create(
            7, 99, 10, "Question?", "\n".join("Option " + str(i) for i in range(10))
        )

    async def test_buttons_persist_and_are_always_secondary(self):
        poll = self.store.get(7, self.ident)
        for state in ("draft", "open", "closed", "cancelled"):
            row = {**poll, "state": state}
            for view in (ui.public_view(row), ui.admin_view(row)):
                self.assertTrue(view.is_persistent())
                for child in view.children:
                    self.assertEqual(child.item.style, discord.ButtonStyle.secondary)
        button = ui.PollButton(self.ident, "vote", "Yes", option=1)
        restored = await ui.PollButton.from_custom_id(
            None, button.item, re.fullmatch(button.template, button.item.custom_id)
        )
        self.assertEqual(
            (restored.poll_id, restored.action, restored.option),
            (self.ident, "vote", 1),
        )

    async def test_fixed_thumbnail_hidden_totals_and_live_results(self):
        poll = self.store.get(7, self.ident)
        poll.update(state="open", counts=[1] * 10, voters=1)
        hidden = ui.render(poll)
        self.assertEqual(hidden.thumbnail.url, "https://i.vgy.me/dy0EEH.png")
        self.assertNotIn("people have voted", hidden.description)
        self.assertNotIn("%", hidden.description)
        live = ui.render({**poll, "visibility": "live"})
        self.assertIn("100%", live.description)
        self.assertIn("**1** person has voted", live.description)
        closed = ui.render({**poll, "state": "closed"}, private=True)
        self.assertIn("100%", closed.description)
        self.assertNotIn("<@", closed.description)

    async def test_worst_case_embed_and_real_modal_are_within_discord_limits(self):
        ident = self.store.create(
            7,
            99,
            10,
            "*" * 256,
            "\n".join("*" * 79 + str(i) for i in range(10)),
            "*" * 1000,
        )
        poll = self.store.get(7, ident)
        result = ui.render(poll, results=True)
        self.assertLessEqual(len(result.description), 4096)
        self.assertLessEqual(len(result), 6000)
        async with commands.Bot(
            command_prefix="test_", intents=discord.Intents.none()
        ) as bot:
            cog = extension.PollsCog(bot)
            modal = ui.PollEditor(cog, 99, poll=poll)
            self.assertEqual(len(modal.children), 4)
            self.assertTrue(all(len(c.label) <= 45 for c in modal.children))
            settings = ui.SettingsView(cog, 99, poll)
            self.assertTrue(
                all(
                    c.style == discord.ButtonStyle.secondary
                    for c in settings.children
                    if isinstance(c, discord.ui.Button)
                )
            )

    async def test_admin_configuration_requires_owner_private_channel_and_guild(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.permissions_for.return_value.view_channel = False
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=99),
            guild_id=7,
            channel_id=10,
            channel=channel,
            guild=SimpleNamespace(default_role=object()),
        )
        with patch.multiple(extension, OWNER_ID=99, GUILD_ID=7, ADMIN_CHANNEL_ID=10):
            extension.PollsCog.require_admin(interaction)
            for key, value in (("guild_id", 8), ("channel_id", 20)):
                original = getattr(interaction, key)
                setattr(interaction, key, value)
                with self.assertRaises(PollError):
                    extension.PollsCog.require_admin(interaction)
                setattr(interaction, key, original)
            interaction.user.id = 123
            with self.assertRaises(PollError):
                extension.PollsCog.require_admin(interaction)
            interaction.user.id = 99
            channel.permissions_for.return_value.view_channel = True
            with self.assertRaises(PollError):
                extension.PollsCog.require_admin(interaction)

    async def test_forged_or_private_preview_vote_is_rejected(self):
        self.store.configure(7, self.ident, "public_channel_id", 20)
        self.store.open(7, self.ident)
        self.store.message(7, self.ident, "public", 100)
        async with commands.Bot(
            command_prefix="test_", intents=discord.Intents.none()
        ) as bot:
            cog = extension.PollsCog(bot)
            cog.store = self.store
            interaction = SimpleNamespace(
                guild_id=7,
                channel_id=20,
                message=SimpleNamespace(id=999),
                user=SimpleNamespace(id=123, bot=False),
            )
            with patch.object(extension, "GUILD_ID", 7), self.assertRaises(PollError):
                await cog.dispatch(interaction, self.ident, "vote", 0)
        self.assertEqual(self.store.get(7, self.ident)["voters"], 0)

    async def test_private_confirmation_rechecks_owner(self):
        cog = SimpleNamespace(require_admin=lambda _: None, respond=AsyncMock())
        confirmation = ui.Confirmation(cog, 99, self.ident, "close")
        self.assertFalse(
            await confirmation.interaction_check(
                SimpleNamespace(user=SimpleNamespace(id=123))
            )
        )
        cog.respond.assert_awaited_once()

    async def test_send_recovery_reuses_the_message_sent_before_restart(self):
        poll = self.store.get(7, self.ident)
        previous = SimpleNamespace(
            id=888,
            author=SimpleNamespace(id=55),
            embeds=[
                discord.Embed().set_footer(
                    text="Poll · " + ui.marker(self.ident, "admin")
                )
            ],
        )
        previous.edit = AsyncMock(return_value=previous)

        async def history(**kwargs):
            yield previous

        channel = SimpleNamespace(history=history, send=AsyncMock())
        async with commands.Bot(
            command_prefix="test_", intents=discord.Intents.none()
        ) as bot:
            cog = extension.PollsCog(bot)
            cog.store = self.store
            bot._connection.user = SimpleNamespace(id=55)
            with patch.object(extension, "GUILD_ID", 7):
                message = await cog.managed_message(
                    poll, "admin", channel, ui.render(poll, private=True)
                )
            self.assertEqual(message.id, 888)
            channel.send.assert_not_awaited()
        self.assertEqual(self.store.get(7, self.ident)["admin_message_id"], 888)

    async def test_extension_load_unload_has_no_login_and_uses_isolated_database(self):
        path = str(Path(self.tmp.name) / "extension.db")
        with patch.dict(os.environ, {"POLL_DB_PATH": path}):
            async with commands.Bot(
                command_prefix="test_", intents=discord.Intents.none()
            ) as bot:
                await bot.load_extension("hyped_ogre.features.polls.extension")
                cog = bot.get_cog("PollsCog")
                self.assertIsNotNone(cog)
                self.assertEqual(cog.__module__, "hyped_ogre.features.polls.extension")
                groups = bot.tree.get_commands(
                    guild=discord.Object(id=extension.GUILD_ID)
                )
                self.assertEqual([command.name for command in groups], ["poll"])
                for command in groups:
                    self.assertEqual(command.to_dict(bot.tree)["name"], "poll")
                await bot.unload_extension("hyped_ogre.features.polls.extension")
                self.assertIsNone(bot.get_cog("PollsCog"))

    async def test_poll_path_cannot_target_the_roll_database(self):
        with (
            patch.dict(os.environ, {"POLL_DB_PATH": "bot.db", "DB_PATH": "bot.db"}),
            self.assertRaises(PollError),
        ):
            extension.store_path()


class PollFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PollStore(Path(self.tmp.name) / "polls.db")
        self.store.initialise()
        self.ident = self.store.create(7, 99, 10, "Guild question?")
        self.store.configure(7, self.ident, "public_channel_id", 20)
        self.bot_user = SimpleNamespace(id=55)
        self.guild = SimpleNamespace(id=7, default_role=object(), me=object())
        self.channels = {ident: self.make_channel(ident) for ident in (10, 20)}
        self.cog = extension.PollsCog(
            SimpleNamespace(
                get_channel=lambda ident: self.channels.get(ident),
                fetch_channel=AsyncMock(),
                user=self.bot_user,
            )
        )
        self.cog.store = self.store
        self.constants = patch.multiple(
            extension, GUILD_ID=7, OWNER_ID=99, ADMIN_CHANNEL_ID=10
        )
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def make_channel(self, ident):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.guild, channel.sent = ident, self.guild, []

        def permissions(who):
            return SimpleNamespace(
                view_channel=ident != 10 or who is not self.guild.default_role,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
            )

        channel.permissions_for.side_effect = permissions

        async def send(**kwargs):
            message = SimpleNamespace(
                id=1000 + ident + len(channel.sent),
                author=self.bot_user,
                embeds=[kwargs["embed"]],
                controls=kwargs.get("view"),
            )

            async def edit(**new):
                message.embeds = [new["embed"]]
                message.controls = new.get("view")
                return message

            message.edit = AsyncMock(side_effect=edit)
            channel.sent.append(message)
            return message

        channel.send = AsyncMock(side_effect=send)

        async def history(**kwargs):
            for message in reversed(channel.sent):
                yield message

        channel.history = history
        channel.get_partial_message.side_effect = lambda key: next(
            m for m in channel.sent if m.id == key
        )
        return channel

    def interaction(self):
        response = SimpleNamespace(
            is_done=lambda: True, defer=AsyncMock(), send_message=AsyncMock()
        )
        return SimpleNamespace(
            user=SimpleNamespace(id=99),
            guild_id=7,
            channel_id=10,
            channel=self.channels[10],
            guild=self.guild,
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_full_private_draft_vote_close_and_deliberate_publication(self):
        await self.cog.sync(self.ident)
        self.assertEqual(len(self.channels[10].sent), 1)
        self.assertEqual(len(self.channels[20].sent), 0)
        await self.cog.perform_admin(self.interaction(), self.ident, "post")
        self.assertEqual(len(self.channels[20].sent), 1)
        self.store.vote(7, self.ident, 123, 0)
        self.store.vote(7, self.ident, 124, 1)
        await self.cog.perform_admin(self.interaction(), self.ident, "close")
        self.assertEqual(len(self.channels[20].sent), 1)
        public = self.channels[20].sent[0]
        private = self.channels[10].sent[0]
        self.assertNotIn("%", public.embeds[0].description)
        self.assertIn("50%", private.embeds[0].description)
        self.assertTrue(all(c.item.disabled for c in public.controls.children))
        await asyncio.gather(
            *[
                self.cog.perform_admin(self.interaction(), self.ident, "publish")
                for _ in range(3)
            ]
        )
        self.assertEqual(len(self.channels[20].sent), 2)
        self.assertIn("50%", self.channels[20].sent[1].embeds[0].description)
        self.assertIn("50%", public.embeds[0].description)
        self.assertTrue(self.store.get(7, self.ident)["results_message_id"])

    async def test_expired_poll_returns_results_privately_after_restart(self):
        end = int(datetime.now(UTC).timestamp()) + 3600
        self.store.configure(7, self.ident, "closes_at", end)
        await self.cog.perform_admin(self.interaction(), self.ident, "post")
        self.store.vote(7, self.ident, 123, 0)
        self.cog.store = PollStore(self.store.path)
        self.cog.store.initialise()
        with patch.object(extension.time, "time", return_value=end + 1):
            await self.cog.sync(self.ident)
        self.assertEqual(self.store.get(7, self.ident)["state"], "closed")
        self.assertIn("100%", self.channels[10].sent[0].embeds[0].description)
        self.assertNotIn("%", self.channels[20].sent[0].embeds[0].description)
        self.assertEqual(len(self.channels[20].sent), 1)

    async def test_changed_private_permissions_do_not_publish_private_totals(self):
        await self.cog.perform_admin(self.interaction(), self.ident, "post")
        self.store.vote(7, self.ident, 123, 0)
        self.store.close(7, self.ident)
        self.channels[10].permissions_for.side_effect = None
        self.channels[10].permissions_for.return_value = SimpleNamespace(
            view_channel=True
        )
        with self.assertRaises(PollError):
            await self.cog.sync(self.ident)
        self.assertNotIn("%", self.channels[10].sent[0].embeds[0].description)
        self.assertNotIn("%", self.channels[20].sent[0].embeds[0].description)
        self.assertTrue(self.store.get(7, self.ident)["admin_dirty"])


if __name__ == "__main__":
    unittest.main()
