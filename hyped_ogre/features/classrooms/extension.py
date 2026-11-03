"""Discord permissions adapter and private leadership classroom controls."""

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import (
    BOT_ROLE_ID, CATEGORY_ID, CHANNELS, GUILD_ID, LEADERSHIP_ROLE_ID,
    MEMBER_PERMISSIONS,
)
from .store import ClassroomError, ClassroomStore

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    added: int = 0
    removed: int = 0
    blocked: int = 0
    errors: list[str] = field(default_factory=list)

    def text(self):
        text = (f"Ogre updated classrooms: {self.added} member/channel grants, "
                f"{self.removed} removals, {self.blocked} manual restrictions kept.")
        if self.errors:
            text += "\nNeeds attention:\n" + "\n".join(self.errors[:len(CHANNELS)])
        return text


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class ClassroomsCog(commands.GroupCog, group_name="classrooms",
                    group_description="Manage private class chats from registered characters"):
    def __init__(self, bot, *, path=None):
        self.bot = bot
        self.store = ClassroomStore(path or os.getenv("RAID_DB_PATH") or (
            "/data/raid_signup.db" if os.path.isdir("/data") else "raid_signup.db"))
        self.lock = asyncio.Lock()

    async def run(self, operation, *args):
        return await asyncio.to_thread(operation, *args)

    async def cog_load(self):
        # The register is not multi-guild. Never apply Hyped characters elsewhere.
        if int(os.getenv("HYPED_GUILD_ID") or 0) != GUILD_ID:
            return
        await self.run(self.store.initialise)
        self.maintenance.start()

    async def cog_unload(self):
        task = self.maintenance.get_task()
        self.maintenance.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def allowed(interaction, *, owner_only=False):
        if interaction.guild_id != GUILD_ID:
            return False
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if interaction.user.id in (owner_id, interaction.guild.owner_id):
            return True
        return not owner_only and any(
            role.id == LEADERSHIP_ROLE_ID for role in getattr(interaction.user, "roles", ())
        )

    async def interaction_check(self, interaction):
        if self.allowed(interaction):
            return True
        await interaction.response.send_message("Classrooms are managed by Hyped leadership.",
                                                ephemeral=True)
        return False

    @staticmethod
    async def send(interaction, content):
        """Keep permission diagnostics within Discord's message length limit."""
        while content:
            end = len(content) if len(content) <= 1900 else content.rfind("\n", 0, 1900)
            if end <= 0:
                end = min(1900, len(content))
            await interaction.followup.send(content[:end], ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
            content = content[end:].lstrip("\n")

    async def cog_app_command_error(self, interaction, error):
        error = getattr(error, "original", error)
        if isinstance(error, (ClassroomError, discord.HTTPException, sqlite3.Error)):
            message = (str(error) if isinstance(error, ClassroomError) else
                       "Ogre couldn't finish classroom sync. Check bot permissions and retry. "
                       "Character records are unchanged.")
            log.warning("Classroom command failed: %s", type(error).__name__)
        else:
            log.error("Unexpected classroom error", exc_info=error)
            message = "Ogre couldn't finish that classroom update. Check the bot logs."
        if interaction.response.is_done():
            await self.send(interaction, message)
        else:
            await interaction.response.send_message(message, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

    async def destinations(self, guild):
        if guild.id != GUILD_ID:
            raise ClassroomError("This character register belongs to Hyped only.")
        # REST snapshot avoids stale gateway overwrites after a previous edit and
        # works without listing/chunking members or requiring a members intent.
        channels = {channel.id: channel for channel in await guild.fetch_channels()}
        category = channels.get(CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            raise ClassroomError("Cannot find the audited forever-classrooms category.")
        result = []
        for item in CHANNELS:
            channel = channels.get(item.channel_id)
            if (not isinstance(channel, discord.TextChannel)
                    or channel.category_id != CATEGORY_ID):
                raise ClassroomError(f"Missing or moved classroom: #{item.key}. No sync performed.")
            result.append(channel)
        return category, result

    @staticmethod
    def channel_problem(channel, guild, *, privacy=True):
        bot_permissions = channel.permissions_for(guild.me)
        needed = (*MEMBER_PERMISSIONS, "manage_roles")
        if not all(getattr(bot_permissions, name) for name in needed):
            return (f"#{channel.name}: allow Bot View Channel, Send Messages, "
                    "Read Message History and Manage Permissions (Manage Roles).")
        if privacy:
            if channel.overwrites_for(guild.default_role).view_channel is not False:
                return f"#{channel.name}: deny @everyone View Channel first."
            for target, overwrite in channel.overwrites.items():
                if (isinstance(target, discord.Role) and overwrite.view_channel is True
                        and target.id not in (BOT_ROLE_ID, LEADERSHIP_ROLE_ID)
                        and not target.permissions.administrator):
                    return f"#{channel.name}: {target.name} has broad View Channel access."
        return None

    @staticmethod
    async def write(channel, target, overwrite):
        # Merge a single target's current overwrite; never replace the channel's
        # entire overwrite map or sync the channel to its category.
        await channel.set_permissions(
            target, overwrite=None if overwrite.is_empty() else overwrite,
            reason="Hyped classrooms: registered main/alt access",
        )

    async def configure(self, guild):
        """All-channel preflight, private baselines, then enable reconciliation."""
        async with self.lock:
            category, channels = await self.destinations(guild)
            bot_role = guild.get_role(BOT_ROLE_ID)
            leadership = guild.get_role(LEADERSHIP_ROLE_ID)
            if bot_role is None or leadership is None or bot_role not in guild.me.roles:
                raise ClassroomError("Cannot find the audited Bot and Leadership roles on Hyped.")
            problems = [p for c in (category, *channels)
                        if (p := self.channel_problem(c, guild, privacy=False))]
            # Do not silently strip other roles' deliberate access.
            for channel in (category, *channels):
                for target, overwrite in channel.overwrites.items():
                    if (isinstance(target, discord.Role) and overwrite.view_channel is True
                            and target.id not in (BOT_ROLE_ID, LEADERSHIP_ROLE_ID)
                            and not target.permissions.administrator):
                        problems.append(f"#{channel.name}: remove broad View Channel "
                                        f"access for {target.name} before setup.")
            if problems:
                raise ClassroomError("\n".join(problems))
            # Setup can be repeated. Disable until every baseline is installed.
            await self.run(self.store.set_enabled, GUILD_ID, False)
            for channel in (category, *channels):
                everyone = channel.overwrites_for(guild.default_role)
                if everyone.view_channel is not False:
                    everyone.view_channel = False
                    await self.write(channel, guild.default_role, everyone)
                for role in (bot_role, leadership):
                    overwrite = channel.overwrites_for(role)
                    if any(getattr(overwrite, p) is not True for p in MEMBER_PERMISSIONS):
                        overwrite.update(**dict.fromkeys(MEMBER_PERMISSIONS, True))
                        await self.write(channel, role, overwrite)
            await self.run(self.store.set_enabled, GUILD_ID, True)

    async def sync_access(self, guild, *, member_id=None):
        async with self.lock:
            result = SyncResult()
            if guild.id != GUILD_ID or not await self.run(self.store.enabled, GUILD_ID):
                return result
            _, channels = await self.destinations(guild)
            desired, journal = await self.run(self.store.snapshot, GUILD_ID)
            members = {}
            for channel in channels:
                problem = self.channel_problem(channel, guild)
                if problem:
                    result.errors.append(problem)
                    continue
                identifiers = desired[channel.id] | {
                    ident for cid, ident in journal if cid == channel.id
                }
                if member_id is not None:
                    identifiers &= {member_id}
                for ident in sorted(identifiers):
                    try:
                        if ident not in members:
                            member = guild.get_member(ident)
                            if member is None:
                                try:
                                    member = await guild.fetch_member(ident)
                                except discord.NotFound:
                                    member = None
                            members[ident] = member
                        member = members[ident]
                        record = journal.get((channel.id, ident))
                        eligible = ident in desired[channel.id] and member is not None and not member.bot
                        if not eligible and record is None:
                            continue
                        # User is accepted by discord.py's permission endpoint,
                        # allowing old grants for departed members to be removed.
                        target = member or self.bot.get_user(ident) or await self.bot.fetch_user(ident)
                        current = channel.overwrites_for(target)
                        overwrite = discord.PermissionOverwrite.from_pair(*current.pair())
                        if eligible:
                            if current.view_channel is False:
                                result.blocked += 1
                                continue
                            if record is None:
                                owned = tuple(p for p in MEMBER_PERMISSIONS
                                              if getattr(current, p) is None)
                                if not owned:
                                    continue  # Pre-existing manual grant, not ours.
                                await self.run(self.store.prepare_grant, GUILD_ID,
                                               channel.id, ident, owned)
                                pending = True
                            else:
                                owned, pending = record
                            if not pending:
                                # A moderator may have edited our allow to deny or
                                # inherit. Keep their decision rather than fighting it.
                                if any(getattr(current, p) is not True for p in owned):
                                    result.blocked += 1
                                continue
                            for permission in owned:
                                if getattr(overwrite, permission) is None:
                                    setattr(overwrite, permission, True)
                            if overwrite != current:
                                await self.write(channel, target, overwrite)
                                result.added += 1
                            await self.run(self.store.grant_applied, GUILD_ID, channel.id, ident)
                        else:
                            owned, _ = record
                            # All owned flags were originally None. Restore only
                            # flags still equal to our allow. Preserve manual denies,
                            # pre-existing allows and every unrelated permission.
                            for permission in owned:
                                if getattr(overwrite, permission) is True:
                                    setattr(overwrite, permission, None)
                            if overwrite != current:
                                await self.write(channel, target, overwrite)
                                result.removed += 1
                            await self.run(self.store.forget_grant, GUILD_ID, channel.id, ident)
                    except (discord.HTTPException, sqlite3.Error) as exc:
                        # Leave the durable journal for retry. An unknown fetch
                        # result is never interpreted as proof a member has left.
                        result.errors.append(f"#{channel.name}: member {ident} update failed "
                                             f"({type(exc).__name__}); will retry.")
            return result

    @tasks.loop(seconds=60)
    async def maintenance(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return
        try:
            result = await self.sync_access(guild)
            if result.errors:
                log.warning("Classroom sync: %s", "; ".join(result.errors))
        except (discord.HTTPException, sqlite3.Error, ClassroomError) as exc:
            log.warning("Classroom sync delayed: %s", exc)

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_classroom_character_changed(self, guild_id, member_id):
        if guild_id != GUILD_ID:
            return
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return
        try:
            result = await self.sync_access(guild, member_id=member_id)
            if result.errors:
                log.warning("Classroom character update: %s", "; ".join(result.errors))
        except (discord.HTTPException, sqlite3.Error, ClassroomError) as exc:
            log.warning("Classroom character update delayed: %s", exc)

    @app_commands.command(name="setup", description="Set up private classrooms and populate existing characters")
    async def setup_classrooms(self, interaction: discord.Interaction):
        if not self.allowed(interaction, owner_only=True):
            await interaction.response.send_message("Only Ogre's owner or the server owner can set up classrooms.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.configure(interaction.guild)
        result = await self.sync_access(interaction.guild)
        await self.send(interaction, "Classrooms enabled. " + result.text())

    @app_commands.command(name="sync", description="Refresh class access for every registered main and alt")
    async def sync_classrooms(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self.run(self.store.enabled, GUILD_ID):
            raise ClassroomError("Classrooms are paused or not set up. Ask Ogre's owner to run /classrooms setup.")
        result = await self.sync_access(interaction.guild)
        await self.send(interaction, result.text())

    @app_commands.command(name="status", description="Check classroom permissions and registered character totals")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        enabled = await self.run(self.store.enabled, GUILD_ID)
        desired, journal = await self.run(self.store.snapshot, GUILD_ID)
        category, channels = await self.destinations(interaction.guild)
        lines = [f"Classrooms: **{'enabled' if enabled else 'paused / awaiting setup'}**.",
                 "Registered accounts per channel (mains and alts combined):"]
        for item in CHANNELS:
            lines.append(f"<#{item.channel_id}>: {len(desired[item.channel_id])}")
        problems = [p for c in (category, *channels)
                    if (p := self.channel_problem(c, interaction.guild))]
        lines.append(f"Ogre tracks {len(journal)} member/channel grants.")
        lines.extend(problems)
        lines.append("Manual member access and administrator access are separate from character access.")
        await self.send(interaction, "\n".join(lines))

    @app_commands.command(name="pause", description="Pause automatic updates and keep current access")
    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            await self.run(self.store.set_enabled, GUILD_ID, False)
        await interaction.followup.send("Classroom updates paused. Current access kept. "
                                        "Run /classrooms setup to resume.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ClassroomsCog(bot))
