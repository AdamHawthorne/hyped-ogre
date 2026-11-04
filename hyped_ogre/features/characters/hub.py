"""Shared private character editor and a persistent guild character board."""

import asyncio
import logging
import os
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from .roster import rows, render
from .absence_ui import AbsenceActions

LOG = logging.getLogger(__name__)
CHARACTERS_MESSAGE = "Add yer name to mighty Hyped roster!"


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

    async def cog_load(self):
        if int(os.getenv("HYPED_GUILD_ID") or 0) != GUILD_ID:
            return
        with raid.database(raid.RAID_DB) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS character_panels (
                guild_id INTEGER NOT NULL, channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL, page_index INTEGER NOT NULL DEFAULT 0)""")
            con.execute("""CREATE TABLE IF NOT EXISTS character_panel_parts (
                channel_id INTEGER NOT NULL, position INTEGER NOT NULL,
                message_id INTEGER NOT NULL, PRIMARY KEY(channel_id,position))""")
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
            content=CHARACTERS_MESSAGE,
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

    @tasks.loop(seconds=60)
    async def maintenance(self):
        async with self.lock:
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

    @app_commands.command(name="postcharacters", description="Post or refresh the shared character board in this channel")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def post_characters(self, interaction: discord.Interaction):
        if (interaction.guild_id != GUILD_ID or not (
                interaction.user.id in (int(os.getenv("OWNER_ID") or 0), interaction.guild.owner_id)
                or any(r.id == LEADERSHIP_ROLE_ID for r in interaction.user.roles))):
            await interaction.response.send_message("Leadership manages the character board.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Post the board in a text channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            saved = next((r for r in await asyncio.to_thread(self.panels) if r[0] == channel.id), None)
            if saved:
                try:
                    await self.refresh(channel.id, saved[1], saved[2])
                except discord.NotFound:
                    saved = None
            if saved is None:
                embeds = render(await asyncio.to_thread(rows, raid.RAID_DB))
                message = await channel.send(
                    content=CHARACTERS_MESSAGE,
                    embed=embeds[0], view=HubView(self, channel.id),
                    allowed_mentions=discord.AllowedMentions.none())
                with raid.database(raid.RAID_DB) as con:
                    con.execute("INSERT INTO character_panels (guild_id,channel_id,message_id,page_index) "
                                "VALUES (?,?,?,0) ON CONFLICT(channel_id) DO UPDATE SET "
                                "message_id=excluded.message_id,page_index=0", (GUILD_ID, channel.id, message.id))
                await self.sync_parts(channel, embeds[1:])
        await interaction.followup.send("Ogre's roster is ready. Everyone can use /characters privately.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(CharacterHub(bot))
