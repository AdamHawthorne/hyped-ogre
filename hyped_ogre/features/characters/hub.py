"""Shared private character editor and a persistent guild character board."""

import asyncio
import logging
import os
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from .roster import rows, render
from .absence_ui import AbsenceActions
from . import armory
from .v2 import CharacterBoardV2, groups as v2_groups, chunks as v2_chunks

LOG = logging.getLogger(__name__)
ROSTER_ANNOUNCEMENT = (
    "Ogre wants yer main! Register it below to earn the **Rostered** "
    "rank in-game and make raid sign-ups easier. Click **Add** to join!"
)


class RosterAddView(discord.ui.View):
    """Choose a saved LFG character or create a genuinely new roster entry."""

    def __init__(self, cog, channel_id, owner_id):
        super().__init__(timeout=300)
        self.cog, self.channel_id, self.owner_id = cog, channel_id, owner_id
        existing = raid.member_characters(owner_id)
        if existing:
            picker = discord.ui.Select(
                placeholder="Add a saved character to the roster",
                options=[
                    discord.SelectOption(
                        label=row[1][:100], value=str(row[0]),
                        description=f"{row[2]} / {row[3]} · {row[4]}"[:100],
                    ) for row in existing[:25]
                ],
            )

            async def choose(interaction):
                if interaction.user.id != owner_id or interaction.guild_id != GUILD_ID:
                    await interaction.response.send_message(
                        "Only you can add your characters.", ephemeral=True)
                    return
                try:
                    changed = raid.promote_character_to_roster(owner_id, int(picker.values[0]))
                except (ValueError, sqlite3.Error) as exc:
                    await interaction.response.send_message(
                        f"Ogre couldn't add that character: {exc}", ephemeral=True)
                    return
                await interaction.response.edit_message(
                    content=("Added existing character to the guild roster."
                             if changed else "That character is already on the roster."),
                    view=None,
                )
                try:
                    saved = next((row for row in cog.panels() if row[0] == channel_id), None)
                    if saved:
                        await cog.refresh(channel_id, saved[1])
                except (ValueError, sqlite3.Error, discord.HTTPException):
                    LOG.warning("Roster will refresh automatically after character promotion.")

            picker.callback = choose
            self.add_item(picker)

        create = discord.ui.Button(label="New character", style=discord.ButtonStyle.success)

        async def new_character(interaction):
            if interaction.user.id != owner_id or interaction.guild_id != GUILD_ID:
                await interaction.response.send_message(
                    "Only you can add your characters.", ephemeral=True)
                return
            await interaction.response.edit_message(
                content=None, view=raid.ClassPickerView(owner_id, roster_visible=True),
            )

        create.callback = new_character
        self.add_item(create)


class HubView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id

    async def interaction_check(self, interaction):
        if interaction.guild_id == GUILD_ID:
            return True
        await interaction.response.send_message("This roster belongs to Hyped.", ephemeral=True)
        return False

    @discord.ui.button(label="Add", style=discord.ButtonStyle.success,
                       custom_id="hyped_characters:add:v2")
    async def add(self, interaction, button):
        await interaction.response.send_message(
            "Add a saved character, or register a new one. Existing names are reused.",
            view=RosterAddView(self.cog, self.channel_id, interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.secondary,
                       custom_id="hyped_characters:update:v1")
    async def update(self, interaction, button):
        await self.cog.open_characters(interaction)

    @discord.ui.button(label="WoWSims", style=discord.ButtonStyle.secondary,
                       custom_id="hyped_characters:gear:v2")
    async def gear(self, interaction, button):
        cog = self.cog.bot.get_cog("RaidPrototype")
        if cog is None:
            await interaction.response.send_message("Ogre's character menu is restarting. Try again shortly.", ephemeral=True)
            return
        await cog.on_gear(interaction)

    @discord.ui.button(label="Remove / Absent", style=discord.ButtonStyle.danger,
                       custom_id="hyped_characters:absence:v2")
    async def absence(self, interaction, button):
        await interaction.response.send_message(
            "Ogre tell Leadership when you remove a character or report a long-term absence. "
            "Absence details stay private. Existing raid sign-ups need their own Absent update.",
            view=AbsenceActions(interaction.user.id), ephemeral=True)


class CharacterHub(commands.Cog):
    def __init__(self, bot):
        self.bot, self.lock = bot, asyncio.Lock()
        self.armory_next_check = 0.0
        self.v2_member_lookup = {}
        self.v2_member_checked = {}

    async def cog_app_command_error(self, interaction, error):
        LOG.warning("Character hub command delayed: %s", type(getattr(error, "original", error)).__name__)
        text = "Ogre couldn't finish that character-board update. Check channel permissions and try again. Saved characters are kept."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def panels():
        with raid.database(raid.RAID_DB) as con:
            return con.execute("SELECT channel_id,message_id,page_index FROM character_panels "
                               "WHERE guild_id=?", (GUILD_ID,)).fetchall()

    @staticmethod
    def v2_panels():
        with raid.database(raid.RAID_DB) as con:
            return con.execute(
                "SELECT channel_id,message_id FROM character_v2_panels "
                "WHERE guild_id=?", (GUILD_ID,)
            ).fetchall()

    async def member_labels(self, guild, grouped):
        """Resolve missing Discord users once, then cache status for 30 minutes.

        A cache miss is never evidence a member left; only fetch_member()
        NotFound is definitive. Fetches are bounded to avoid Discord bursts.
        """
        users = {
            int(character["discord_id"])
            for _, members in grouped for character in members
        }
        now = time.monotonic()
        missing = []
        for user_id in users:
            cached_member = guild.get_member(user_id)
            if cached_member is not None:
                self.v2_member_lookup[user_id] = (
                    "member", str(cached_member.display_name)
                )
                self.v2_member_checked[user_id] = now
            elif now - self.v2_member_checked.get(user_id, -1000000) >= 1800:
                missing.append(user_id)

        semaphore = asyncio.Semaphore(4)

        async def check(user_id):
            async with semaphore:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    result = ("departed", "")
                except (discord.Forbidden, discord.HTTPException):
                    result = ("unknown", "")
                else:
                    result = ("member", str(member.display_name))
                self.v2_member_lookup[user_id] = result
                self.v2_member_checked[user_id] = now

        await asyncio.gather(*(check(user_id) for user_id in missing))
        return {
            user_id: self.v2_member_lookup.get(user_id, ("unknown", ""))
            for user_id in users
        }

    async def cog_load(self):
        if int(os.getenv("HYPED_GUILD_ID") or 0) != GUILD_ID:
            return
        armory.initialise(raid.RAID_DB)
        with raid.database(raid.RAID_DB) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS character_panels (
                guild_id INTEGER NOT NULL, channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL, page_index INTEGER NOT NULL DEFAULT 0)""")
            con.execute("""CREATE TABLE IF NOT EXISTS character_panel_parts (
                channel_id INTEGER NOT NULL, position INTEGER NOT NULL,
                message_id INTEGER NOT NULL, PRIMARY KEY(channel_id,position))""")
            con.execute("""CREATE TABLE IF NOT EXISTS character_panel_announcements (
                channel_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL
            )""")
            # V2 is an isolated private pilot. Existing public board IDs and
            # stored messages are not replaced, changed or removed.
            con.execute("""CREATE TABLE IF NOT EXISTS character_v2_panels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS character_v2_parts (
                channel_id INTEGER NOT NULL, position INTEGER NOT NULL,
                message_id INTEGER NOT NULL, PRIMARY KEY(channel_id,position)
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS character_v2_intros (
                channel_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL
            )""")
        for channel_id, message_id in await asyncio.to_thread(self.v2_panels):
            self.bot.add_view(
                CharacterBoardV2(self, channel_id), message_id=message_id
            )
        for channel_id, message_id, _ in await asyncio.to_thread(self.panels):
            self.bot.add_view(HubView(self, channel_id), message_id=message_id)
        self.maintenance.start()

    async def cog_unload(self):
        task = self.maintenance.get_task()
        self.maintenance.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)

    async def open_characters(self, interaction):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message("Characters are available in Hyped.", ephemeral=True)
            return
        cog = self.bot.get_cog("RaidPrototype")
        if cog is None:
            await interaction.response.send_message("Ogre's character menu is restarting. Try again shortly.", ephemeral=True)
            return
        await cog.on_update(interaction)

    async def refresh(self, channel_id, message_id, page=0):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise ValueError("Character board channel is not in Hyped.")
        embeds = render(await asyncio.to_thread(rows, raid.RAID_DB))
        # Refresh the anchor first. A missing anchor is handled by /postcharacters.
        await channel.get_partial_message(message_id).edit(
            content=None,
            embed=embeds[0], view=HubView(self, channel_id),
            allowed_mentions=discord.AllowedMentions.none())
        await self.sync_parts(channel, embeds[1:])
        with raid.database(raid.RAID_DB) as con:
            con.execute("UPDATE character_panels SET page_index=0 WHERE channel_id=?", (channel_id,))

    async def sync_parts(self, channel, embeds):
        with raid.database(raid.RAID_DB) as con:
            saved = dict(con.execute("SELECT position,message_id FROM character_panel_parts WHERE channel_id=?",
                                     (channel.id,)))
        for position, embed in enumerate(embeds, 1):
            message_id = saved.get(position)
            if message_id:
                try:
                    await channel.get_partial_message(message_id).edit(
                        embed=embed, allowed_mentions=discord.AllowedMentions.none())
                except discord.NotFound:
                    message_id = None
            if not message_id:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                with raid.database(raid.RAID_DB) as con:
                    con.execute("INSERT INTO character_panel_parts VALUES (?,?,?) "
                                "ON CONFLICT(channel_id,position) DO UPDATE SET message_id=excluded.message_id",
                                (channel.id, position, message.id))
        for position, message_id in saved.items():
            if position <= len(embeds):
                continue
            try:
                await channel.get_partial_message(message_id).delete()
            except discord.NotFound:
                pass
            with raid.database(raid.RAID_DB) as con:
                con.execute("DELETE FROM character_panel_parts WHERE channel_id=? AND position=?",
                            (channel.id, position))

    async def ensure_v2_intro(self, channel):
        """As for raid sign-ups, a role ping is a separate plain-text message.

        Components V2 cannot carry ordinary message content. Send at most
        one notification for this private pilot board, not on refreshes.
        """
        with raid.database(raid.RAID_DB) as con:
            exists = con.execute(
                "SELECT message_id FROM character_v2_intros WHERE channel_id=?",
                (channel.id,),
            ).fetchone()
        if exists:
            return
        role = next(
            (role for role in channel.guild.roles
             if role.name.casefold() == "forever"), None
        )
        permissions = channel.permissions_for(channel.guild.me)
        can_ping = bool(role and (
            role.mentionable or permissions.mention_everyone
        ))
        mention = role.mention if role else "@Forever"
        sent = await channel.send(
            content=f"{mention} Ogre got roster here, manage it yerself!",
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=[role] if can_ping else [],
            ),
        )
        with raid.database(raid.RAID_DB) as con:
            con.execute(
                "INSERT INTO character_v2_intros(channel_id,message_id) "
                "VALUES(?,?) ON CONFLICT(channel_id) DO NOTHING",
                (channel.id, sent.id),
            )

    async def refresh_v2(self, channel_id, message_id):
        """Refresh only the private V2 anchor and its owned overflow messages."""
        if channel_id != raid.TEST_CHANNEL_ID:
            raise ValueError("V2 characters may only appear in the private Ogre channel.")
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise ValueError("V2 character board channel is not in Hyped.")
        grouped = v2_groups(await asyncio.to_thread(rows, raid.RAID_DB))
        guild = channel.guild
        verified = await self.member_labels(guild, grouped)
        pages = v2_chunks(grouped, guild, verified)
        await channel.get_partial_message(message_id).edit(
            content=None, embeds=[],
            view=CharacterBoardV2(self, channel_id, guild, grouped=pages[0]),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        with raid.database(raid.RAID_DB) as con:
            stored = dict(con.execute(
                "SELECT position,message_id FROM character_v2_parts "
                "WHERE channel_id=?", (channel_id,)
            ))
        for index, page in enumerate(pages[1:], 1):
            view = CharacterBoardV2(
                self, channel_id, guild, grouped=page, continuation=True,
            )
            part_id = stored.get(index)
            if part_id:
                try:
                    await channel.get_partial_message(part_id).edit(
                        view=view, allowed_mentions=discord.AllowedMentions.none(),
                    )
                    continue
                except discord.NotFound:
                    pass
            sent = await channel.send(
                view=view, allowed_mentions=discord.AllowedMentions.none(),
            )
            with raid.database(raid.RAID_DB) as con:
                con.execute(
                    "INSERT INTO character_v2_parts VALUES (?,?,?) "
                    "ON CONFLICT(channel_id,position) DO UPDATE SET "
                    "message_id=excluded.message_id",
                    (channel_id, index, sent.id),
                )
        for index, part_id in stored.items():
            if index < len(pages):
                continue
            try:
                await channel.get_partial_message(part_id).delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                continue
            with raid.database(raid.RAID_DB) as con:
                con.execute(
                    "DELETE FROM character_v2_parts "
                    "WHERE channel_id=? AND position=? AND message_id=?",
                    (channel_id, index, part_id),
                )

    @tasks.loop(seconds=60)
    async def maintenance(self):
        if time.monotonic() >= self.armory_next_check:
            # An optional client-credentials lookup, never a requirement
            # for the board to function. The WSE cache remains the fallback.
            self.armory_next_check = time.monotonic() + 300
            try:
                await armory.refresh(raid.RAID_DB)
            except (sqlite3.Error, ValueError):
                LOG.warning("Armory update delayed; saved WoWSims levels retained.")
        async with self.lock:
            for channel_id, message_id in await asyncio.to_thread(self.v2_panels):
                try:
                    await self.refresh_v2(channel_id, message_id)
                except (ValueError, sqlite3.Error, discord.HTTPException):
                    LOG.warning("V2 character board %s refresh delayed.", channel_id)
            for channel_id, message_id, page in await asyncio.to_thread(self.panels):
                try:
                    await self.refresh(channel_id, message_id, page)
                except (ValueError, sqlite3.Error, discord.HTTPException):
                    LOG.warning("Character board %s could not refresh; /postcharacters can restore it.", channel_id)

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="characters", description="Privately add or update your main, alts and off-spec")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def characters(self, interaction: discord.Interaction):
        await self.open_characters(interaction)

    @app_commands.command(
        name="postcharacters-v2",
        description="Owner-only pilot: post or update the V2 roster in private Ogre channel",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def post_characters_v2(self, interaction: discord.Interaction):
        owner_id = int(os.getenv("OWNER_ID") or 0)
        allowed_owner = (
            interaction.user.id == owner_id
            or interaction.user.id == getattr(interaction.guild, "owner_id", None)
        )
        if (interaction.guild_id != GUILD_ID
                or interaction.channel_id != raid.TEST_CHANNEL_ID
                or not allowed_owner):
            await interaction.response.send_message(
                "The V2 pilot is restricted to the guild owner in Ogre's "
                "private test channel.", ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use a text channel for the V2 pilot.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        # A manual refresh also attempts a bounded live Armory update.
        # The normal 10-minute background worker handles future changes.
        try:
            await armory.refresh(raid.RAID_DB, limit=12)
        except (ValueError, sqlite3.Error):
            LOG.warning("Manual Armory refresh unavailable; cached data retained.")
        async with self.lock:
            saved = next(
                (item for item in await asyncio.to_thread(self.v2_panels)
                 if item[0] == interaction.channel_id), None
            )
            if saved:
                try:
                    await self.refresh_v2(saved[0], saved[1])
                except discord.NotFound:
                    saved = None
            if not saved:
                await self.ensure_v2_intro(interaction.channel)
                grouped = v2_groups(await asyncio.to_thread(rows, raid.RAID_DB))
                verified = await self.member_labels(interaction.guild, grouped)
                pages = v2_chunks(grouped, interaction.guild, verified)
                message = await interaction.channel.send(
                    view=CharacterBoardV2(
                        self, interaction.channel_id, interaction.guild,
                        grouped=pages[0],
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                with raid.database(raid.RAID_DB) as con:
                    con.execute(
                        "INSERT INTO character_v2_panels VALUES (?,?,?) "
                        "ON CONFLICT(channel_id) DO UPDATE SET "
                        "message_id=excluded.message_id",
                        (GUILD_ID, interaction.channel_id, message.id),
                    )
                await self.refresh_v2(interaction.channel_id, message.id)
            else:
                await self.ensure_v2_intro(interaction.channel)
        try:
            status = armory.coverage(raid.RAID_DB)
            detail = (
                f"Blizzard iLvl cached for {status['ilvls']}/"
                f"{status['visible']} registered characters. "
                + ("Armory credentials configured."
                   if status['credentials'] else
                   "Blizzard API credentials are not configured on Fly.io.")
            )
        except sqlite3.Error:
            detail = "Armory cache status unavailable."
        await interaction.followup.send(
            "Private Characters V2 updated; existing public roster unchanged. "
            + detail,
            ephemeral=True,
        )

    @app_commands.command(
        name="armory-status",
        description="Privately diagnose Forever Armory iLvl coverage and API configuration",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def armory_status(self, interaction: discord.Interaction):
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if not (
            interaction.guild_id == GUILD_ID
            and interaction.channel_id == raid.TEST_CHANNEL_ID
            and (interaction.user.id == owner_id
                 or interaction.user.id == getattr(interaction.guild, "owner_id", None))
        ):
            await interaction.response.send_message(
                "Armory diagnostics are available only to the guild owner in Ogre's private channel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            updated = await armory.refresh(raid.RAID_DB, limit=12)
            status = await asyncio.to_thread(armory.coverage, raid.RAID_DB)
        except (sqlite3.Error, ValueError) as exc:
            LOG.warning("Armory diagnostic failed: %s", type(exc).__name__)
            await interaction.followup.send(
                "Armory check failed. Character records were not changed.",
                ephemeral=True,
            )
            return
        lines = [
            "**Forever Armory (EU)**",
            "Credentials: " + ("Configured" if status["credentials"] else "Missing"),
            f"Visible roster: {status['visible']} characters",
            f"Cached character levels: {status['levels']}",
            f"Cached Blizzard iLvls: {status['ilvls']}",
            f"Profiles checked this time: {updated}",
        ]
        if not status["credentials"]:
            lines.append(
                "To enable lookups, configure BLIZZARD_CLIENT_ID and "
                "BLIZZARD_CLIENT_SECRET as Fly.io secrets."
            )
        elif not status["ilvls"]:
            lines.append(
                "No usable item levels cached. Check the app logs for "
                "Forever API errors. Blizzard profiles require level 10+ "
                "and usually update after logout."
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="postcharacters",
        description="Refresh or repost the main-only guild roster",
    )
    @app_commands.describe(
        repost="Publish a new V1 roster and remove the previous board",
        ping_forever="Ping @Forever on a new post or repost (never on refresh)",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def post_characters(
        self, interaction: discord.Interaction, repost: bool = False,
        ping_forever: bool = True,
    ):
        if (interaction.guild_id != GUILD_ID or not (
                interaction.user.id in (int(os.getenv("OWNER_ID") or 0), interaction.guild.owner_id)
                or any(r.id == LEADERSHIP_ROLE_ID for r in interaction.user.roles))):
            await interaction.response.send_message(
                "Leadership manages the character board.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Post the board in a text channel.", ephemeral=True)
            return

        # A plain /postcharacters refresh should never require mention
        # permissions or ping the role, even when ping_forever defaults True.
        existing = next(
            (entry for entry in await asyncio.to_thread(self.panels)
             if entry[0] == channel.id), None
        )
        should_announce = ping_forever and (repost or existing is None)
        # Verify role access before making any change to existing messages.
        forever_role = None
        if should_announce:
            forever_role = next(
                (role for role in channel.guild.roles
                 if role.name.strip().casefold() == "forever"), None
            )
            if forever_role is None:
                await interaction.response.send_message(
                    "Ogre can't find the @Forever role. Choose "
                    "ping_forever: False, or check the role name.",
                    ephemeral=True,
                )
                return
            permissions = channel.permissions_for(channel.guild.me)
            if not (forever_role.mentionable or permissions.mention_everyone):
                await interaction.response.send_message(
                    "Ogre cannot mention @Forever in this channel. "
                    "Allow role mentions, or choose ping_forever: False.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        # Manual posting can refresh up to 12 stale Armory entries; normally
        # the background worker handles hourly character checks in batches.
        try:
            await armory.refresh(raid.RAID_DB, limit=armory.MAX_REFRESH)
        except (ValueError, sqlite3.Error):
            LOG.warning("Manual Armory refresh delayed; cached iLvls retained.")

        async with self.lock:
            saved = next(
                (entry for entry in await asyncio.to_thread(self.panels)
                 if entry[0] == channel.id), None
            )
            if saved and not repost:
                try:
                    await self.refresh(channel.id, saved[1], saved[2])
                except discord.NotFound:
                    saved = None
                else:
                    await interaction.followup.send(
                        "Ogre refreshed the existing roster. No new post "
                        "and no @Forever ping. Choose repost: True to announce it.",
                        ephemeral=True,
                    )
                    return

            # New post or deliberate repost: send exactly one role mention
            # before the replacement board, then retire the previous board.
            notice = None
            message = None
            try:
                embeds = render(await asyncio.to_thread(rows, raid.RAID_DB))
                if should_announce:
                    notice = await channel.send(
                        content=f"{forever_role.mention} {ROSTER_ANNOUNCEMENT}",
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, users=False, roles=[forever_role],
                        ),
                    )
                message = await channel.send(
                    embed=embeds[0], view=HubView(self, channel.id),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                with raid.database(raid.RAID_DB) as con:
                    previous_notice = con.execute(
                        "SELECT message_id FROM character_panel_announcements "
                        "WHERE channel_id=?", (channel.id,),
                    ).fetchone()
                    con.execute(
                        "INSERT INTO character_panels "
                        "(guild_id,channel_id,message_id,page_index) "
                        "VALUES (?,?,?,0) ON CONFLICT(channel_id) DO UPDATE "
                        "SET message_id=excluded.message_id,page_index=0",
                        (GUILD_ID, channel.id, message.id),
                    )
                    if notice is not None:
                        con.execute(
                            "INSERT INTO character_panel_announcements "
                            "(channel_id,message_id) VALUES (?,?) "
                            "ON CONFLICT(channel_id) DO UPDATE "
                            "SET message_id=excluded.message_id",
                            (channel.id, notice.id),
                        )
                    else:
                        con.execute(
                            "DELETE FROM character_panel_announcements "
                            "WHERE channel_id=?", (channel.id,),
                        )
                await self.sync_parts(channel, embeds[1:])
            except (sqlite3.Error, discord.HTTPException) as exc:
                LOG.warning("Could not post V1 roster: %s", type(exc).__name__)
                if message is not None:
                    try:
                        await message.delete()
                    except (discord.NotFound, discord.HTTPException):
                        pass
                if notice is not None:
                    try:
                        await notice.delete()
                    except (discord.NotFound, discord.HTTPException):
                        pass
                await interaction.followup.send(
                    "Ogre couldn't publish the roster. Check channel "
                    "permissions before retrying.", ephemeral=True,
                )
                return

            # Only delete the previous posts once the new board exists.
            old_ids = [saved[1]] if saved else []
            if previous_notice:
                old_ids.append(previous_notice[0])
            for old_id in old_ids:
                if old_id in (message.id, getattr(notice, "id", None)):
                    continue
                try:
                    await channel.get_partial_message(old_id).delete()
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException):
                    LOG.warning("Old roster/announcement %s not removable.", old_id)

        await interaction.followup.send(
            "Ogre posted a fresh V1 roster with the @Forever invitation."
            if notice is not None else
            "Ogre posted a fresh V1 roster without a role ping.",
            ephemeral=True,
        )

async def setup(bot):
    await bot.add_cog(CharacterHub(bot))
