"""Owner-managed anonymous polls for the private Ogre channel."""

import asyncio
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .store import PollError, PollStore
from .ui import (
    Confirmation,
    PollButton,
    PollEditor,
    SettingsView,
    admin_view,
    marker,
    public_view,
    render,
    safe,
)

log = logging.getLogger(__name__)
GUILD_ID = int(os.getenv("HYPED_GUILD_ID") or 0)
OWNER_ID = int(os.getenv("OWNER_ID") or 0)
ADMIN_CHANNEL_ID = int(
    os.getenv("POLL_ADMIN_CHANNEL_ID")
    or os.getenv("OFFICE_LOG_CHANNEL_ID")
    or "1554621739017707591"
)


def store_path():
    base = Path(os.getenv("DB_PATH") or "bot.db").parent
    path = Path(os.getenv("POLL_DB_PATH") or base / "polls.db")
    protected = [
        Path(os.getenv(key) or base / name).resolve()
        for key, name in (
            ("DB_PATH", "bot.db"),
            ("RAID_DB_PATH", "raid_signup.db"),
            ("LFG_DB_PATH", "lfg_board.db"),
            ("COUNTDOWN_DB_PATH", "countdown.db"),
            ("STREAM_DB_PATH", "streams.db"),
        )
    ]
    if path.resolve() in protected:
        raise PollError(
            "Polls must use their own POLL_DB_PATH; existing bot databases are protected."
        )
    return path


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class PollsCog(
    commands.GroupCog,
    group_name="poll",
    group_description="Private Ogre poll setup and results",
):
    def __init__(self, bot):
        self.bot = bot
        self.store = PollStore(store_path())
        self.timezone = ZoneInfo(os.getenv("BOT_TIMEZONE") or "Europe/London")
        self.locks, self.retry_after = {}, {}

    async def run(self, operation, *args, **kwargs):
        return await asyncio.to_thread(operation, *args, **kwargs)

    async def cog_load(self):
        await self.run(self.store.initialise)
        self.bot.add_dynamic_items(PollButton)
        self.maintenance.start()

    def cog_unload(self):
        self.maintenance.cancel()
        self.bot.remove_dynamic_items(PollButton)

    @staticmethod
    def require_admin(interaction):
        if (
            not OWNER_ID
            or interaction.user.id != OWNER_ID
            or interaction.guild_id != GUILD_ID
        ):
            raise PollError("Only the Hyped Ogre owner can configure polls.")
        if interaction.channel_id != ADMIN_CHANNEL_ID:
            raise PollError(f"Configure polls privately in <#{ADMIN_CHANNEL_ID}>.")
        channel = interaction.channel
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.permissions_for(interaction.guild.default_role).view_channel
        ):
            raise PollError(
                "The poll control channel must be private. Check its @everyone permissions."
            )

    async def interaction_check(self, interaction):
        try:
            self.require_admin(interaction)
        except PollError as exc:
            await self.respond(interaction, str(exc))
            return False
        return True

    async def cog_app_command_error(self, interaction, error):
        await self.report(interaction, getattr(error, "original", error))

    @staticmethod
    async def respond(interaction, content=None, **kwargs):
        kwargs.setdefault("ephemeral", True)
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        if interaction.response.is_done():
            return await interaction.followup.send(content, **kwargs)
        return await interaction.response.send_message(content, **kwargs)

    async def report(self, interaction, error, poll_id=0):
        if isinstance(error, PollError):
            message = str(error)
        elif isinstance(error, discord.Forbidden):
            message = "Ogre needs View Channel, Send Messages, Embed Links and Read Message History here. The poll record is saved."
        else:
            # Ballot identities/selections and HTTP payloads never enter application logs.
            log.warning(
                "Poll operation failed poll=%s type=%s", poll_id, type(error).__name__
            )
            message = "Ogre couldn't finish that poll update. Saved votes remain recorded. Try /poll manage or check permissions."
        await self.respond(interaction, message)

    async def channel(self, ident, *, private=False):
        channel = self.bot.get_channel(ident) or await self.bot.fetch_channel(ident)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise PollError("Choose a text channel in Hyped.")
        if private and channel.permissions_for(channel.guild.default_role).view_channel:
            raise PollError(
                "Private poll results cannot be delivered to a public channel."
            )
        permissions = channel.permissions_for(channel.guild.me)
        if not all(
            getattr(permissions, name)
            for name in (
                "view_channel",
                "send_messages",
                "embed_links",
                "read_message_history",
            )
        ):
            raise PollError(
                "Ogre needs View Channel, Send Messages, Embed Links and Read Message History in that channel."
            )
        return channel

    async def validate_destination(self, interaction, ident):
        self.require_admin(interaction)
        channel = await self.channel(ident)
        permissions = channel.permissions_for(interaction.user)
        if not permissions.view_channel or not permissions.send_messages:
            raise PollError("Choose a channel where you can view and send messages.")
        return channel

    def lock(self, poll_id):
        return self.locks.setdefault(poll_id, asyncio.Lock())

    async def managed_message(
        self, poll, kind, channel, result, controls=None, *, allow_send=True
    ):
        column = {
            "admin": "admin_message_id",
            "public": "public_message_id",
            "results": "results_message_id",
        }[kind]
        ident = poll[column]
        message = None
        if ident:
            try:
                return await channel.get_partial_message(ident).edit(
                    embed=result,
                    view=controls,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                pass
        # Recover a send which reached Discord just before a process restart.
        expected = marker(poll["id"], kind)
        async for previous in channel.history(limit=100):
            if previous.author.id == self.bot.user.id and any(
                (e.footer.text or "").endswith(expected) for e in previous.embeds
            ):
                message = await previous.edit(
                    embed=result,
                    view=controls,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                break
        if message is None:
            if not allow_send:
                return None
            message = await channel.send(
                embed=result,
                view=controls,
                nonce=f"p{poll['id']}{kind[0]}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await self.run(self.store.message, GUILD_ID, poll["id"], kind, message.id)
        return message

    async def sync(self, poll_id):
        async with self.lock(poll_id):
            await self._sync_locked(poll_id)

    async def _sync_locked(self, poll_id):
        poll = await self.run(self.store.get, GUILD_ID, poll_id)
        if (
            poll["state"] == "open"
            and poll["closes_at"] is not None
            and poll["closes_at"] <= int(time.time())
        ):
            await self.run(self.store.close, GUILD_ID, poll_id, due_only=True)
            poll = await self.run(self.store.get, GUILD_ID, poll_id)
        if poll["public_dirty"]:
            if poll["state"] in {"open", "closed"} and poll["public_channel_id"]:
                channel = await self.channel(poll["public_channel_id"])
                await self.managed_message(
                    poll,
                    "public",
                    channel,
                    render(poll),
                    public_view(poll),
                    allow_send=poll["state"] == "open"
                    or bool(poll["public_message_id"]),
                )
            await self.run(
                self.store.clean, GUILD_ID, poll_id, "public", poll["revision"]
            )
        if poll["admin_dirty"]:
            channel = await self.channel(poll["admin_channel_id"], private=True)
            await self.managed_message(
                poll, "admin", channel, render(poll, private=True), admin_view(poll)
            )
            await self.run(
                self.store.clean, GUILD_ID, poll_id, "admin", poll["revision"]
            )

    @tasks.loop(seconds=5)
    async def maintenance(self):
        try:
            pending = await self.run(self.store.pending, GUILD_ID)
        except Exception as exc:  # noqa: BLE001 - retain the worker after a transient failure
            log.warning(
                "Poll queue temporarily unavailable type=%s", type(exc).__name__
            )
            return
        for ident in pending:
            if time.monotonic() < self.retry_after.get(ident, 0):
                continue
            try:
                await self.sync(ident)
                self.retry_after.pop(ident, None)
            except Exception as exc:  # noqa: BLE001 - isolate a failed poll from other polls
                self.retry_after[ident] = time.monotonic() + 60
                log.warning(
                    "Poll refresh pending poll=%s type=%s", ident, type(exc).__name__
                )

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    async def configure(self, interaction, poll_id, key, value):
        self.require_admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(poll_id):
            await self.run(self.store.configure, GUILD_ID, poll_id, key, value)
            await self._sync_locked(poll_id)
        await self.respond(interaction, "Setting saved. Ogre updated the poll card.")

    async def dispatch(self, interaction, poll_id, action, option):
        if action in {"vote", "mine", "remove"}:
            poll = await self.run(self.store.get, interaction.guild_id or 0, poll_id)
            if (
                interaction.guild_id != GUILD_ID
                or interaction.channel_id != poll["public_channel_id"]
                or not interaction.message
                or interaction.message.id != poll["public_message_id"]
                or interaction.user.bot
            ):
                raise PollError("Use the buttons on the posted poll in Hyped.")
            await interaction.response.defer(ephemeral=True, thinking=True)
            if action == "mine":
                chosen = await self.run(
                    self.store.mine, GUILD_ID, poll_id, interaction.user.id
                )
            else:
                chosen = await self.run(
                    self.store.vote,
                    GUILD_ID,
                    poll_id,
                    interaction.user.id,
                    option,
                    remove=action == "remove",
                )
            labels = ", ".join(safe(poll["options"][index]) for index in chosen)
            await self.respond(
                interaction,
                (
                    "Your anonymous choice: "
                    + labels
                    + ". You can change or remove it before closing."
                )
                if chosen
                else "You currently have no vote in this poll.",
            )
            return
        self.require_admin(interaction)
        poll = await self.run(self.store.get, GUILD_ID, poll_id)
        if action == "edit":
            if poll["state"] != "draft":
                raise PollError("Questions and options are locked after posting.")
            await interaction.response.send_modal(
                PollEditor(self, interaction.user.id, poll=poll)
            )
        elif action == "configure":
            if poll["state"] not in {"draft", "open"}:
                raise PollError("That poll has closed.")
            await self.respond(
                interaction,
                "Choose a destination, display mode and voting type. Blank deadline means manual close.",
                view=SettingsView(self, interaction.user.id, poll),
            )
        elif action == "preview":
            await self.respond(
                interaction,
                "Preview only; voting is disabled here.",
                embed=render(poll),
                view=public_view(poll, preview=True),
            )
        elif action == "results":
            await self.respond(
                interaction,
                "Private aggregate results. Voter names are never shown.",
                embed=render(poll, private=True, results=True),
            )
        elif action in {"close", "cancel"}:
            await self.respond(
                interaction,
                "Close voting and finalise anonymous totals?"
                if action == "close"
                else "Cancel this unpublished draft?",
                view=Confirmation(self, interaction.user.id, poll_id, action),
            )
        elif action in {"post", "publish"}:
            await self.perform_admin(interaction, poll_id, action)
        else:
            raise PollError("That poll control is not supported. Use /poll manage.")

    async def perform_admin(self, interaction, poll_id, action):
        self.require_admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(poll_id):
            poll = await self.run(self.store.get, GUILD_ID, poll_id)
            if action == "post":
                await self.validate_destination(
                    interaction, poll["public_channel_id"] or 0
                )
                await self.run(self.store.open, GUILD_ID, poll_id)
            elif action == "close":
                await self.run(self.store.close, GUILD_ID, poll_id)
            elif action == "cancel":
                await self.run(self.store.cancel, GUILD_ID, poll_id)
            elif action == "publish":
                if poll["state"] != "closed":
                    raise PollError(
                        "Close the poll and review its private results first."
                    )
                if not poll["results_message_id"]:
                    channel = await self.validate_destination(
                        interaction, poll["public_channel_id"]
                    )
                    await self.managed_message(
                        poll, "results", channel, render(poll, results=True)
                    )
            else:
                raise PollError("Unknown poll action.")
            await self._sync_locked(poll_id)
        messages = {
            "post": "Poll posted. Members can now vote anonymously.",
            "close": "Voting closed. Final results are on the private Ogre card; use Publish results when ready.",
            "cancel": "Draft cancelled.",
            "publish": "Final anonymous results published in the poll channel.",
        }
        await self.respond(interaction, messages[action])

    @app_commands.command(
        description="Create an anonymous poll draft privately in Ogre"
    )
    async def create(self, interaction: discord.Interaction):
        self.require_admin(interaction)
        await interaction.response.send_modal(PollEditor(self, interaction.user.id))

    @app_commands.command(description="Open a poll's private management controls")
    async def manage(self, interaction: discord.Interaction, poll_id: int):
        self.require_admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        poll = await self.run(self.store.get, GUILD_ID, poll_id)
        await self.respond(
            interaction, embed=render(poll, private=True), view=admin_view(poll)
        )

    @app_commands.command(description="List recent polls and drafts privately")
    async def list(self, interaction: discord.Interaction):
        self.require_admin(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        polls = await self.run(self.store.recent, GUILD_ID)
        text = (
            "\n".join(
                f"**#{p['id']}** · {p['state']} · {safe(p['question'])[:90]}"
                for p in polls
            )
            or "No polls yet. Use /poll create."
        )
        await self.respond(interaction, text)

    @app_commands.command(description="Close a posted poll after private confirmation")
    async def close(self, interaction: discord.Interaction, poll_id: int):
        await self.dispatch(interaction, poll_id, "close", 0)

    @app_commands.command(description="Review aggregate results privately")
    async def results(self, interaction: discord.Interaction, poll_id: int):
        await self.dispatch(interaction, poll_id, "results", 0)

    @app_commands.command(
        description="Publish a closed poll's anonymous results in its channel"
    )
    async def publish(self, interaction: discord.Interaction, poll_id: int):
        await self.perform_admin(interaction, poll_id, "publish")


async def setup(bot):
    await bot.add_cog(PollsCog(bot))
