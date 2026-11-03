"""Shared private character editor and a persistent guild character board."""

import asyncio
import logging
import math
import os
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from . import equipment, items

LOG = logging.getLogger(__name__)
PAGE_SIZE = 8


def rows(path):
    with raid.database(path) as con:
        con.row_factory = sqlite3.Row
        characters = [dict(row) for row in con.execute(
            "SELECT character_id,discord_id,name,class_name,spec,role,is_main,off_spec,off_role "
            "FROM characters ORDER BY is_main DESC,LOWER(name),character_id")]
        window = [r[0] for r in con.execute(
            "SELECT raid_id FROM raids WHERE archived_at IS NOT NULL ORDER BY raid_at DESC LIMIT 4")]
        credits = {}
        if window:
            for row in con.execute(
                "SELECT discord_id,COUNT(DISTINCT raid_id) FROM attendance WHERE raid_id IN ("
                + ",".join("?" for _ in window) + ") AND status IN ('Attended','Late','AFK') GROUP BY discord_id",
                window):
                credits[row[0]] = row[1]
    catalog = items.cached_catalog(path)
    gear = {r["character_id"]: r for r in equipment.latest_profiles(path)}
    for character in characters:
        character["attendance"] = (f"{credits.get(character['discord_id'], 0)}/{len(window)}"
                                   if window else "No completed raids")
        export = gear.get(character["character_id"])
        parsed = equipment.parse_export(export["raw_text"]) if export else None
        average, known, equipped = equipment.average_item_level(parsed, catalog)
        character["ilvl"] = (f"{average:.1f}" + (f" ({known}/{equipped} items known)" if known < equipped else "")
                              if average is not None else "Awaiting WoWSims/item data")
    return characters


def render(characters, page=0):
    pages = max(1, math.ceil(len(characters) / PAGE_SIZE))
    page = min(max(0, page), pages - 1)
    embed = discord.Embed(title="<Hyped> Characters", colour=0xB84A3A,
        description="Use **Update characters** to add a main, alt or off-spec and unlock class chats.\n"
                    "Attendance is per player across the last four completed guild raids.")
    for row in characters[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        off = f"{row['off_spec']} · {row['off_role']}" if row["off_spec"] else "Not set"
        name = discord.utils.escape_mentions(discord.utils.escape_markdown(row["name"]))
        embed.add_field(name=f"{name} · {row['class_name']} · {'Main' if row['is_main'] else 'Alt'}",
                        value=(f"<@{row['discord_id']}> · {row['spec']} · {row['role']}\n"
                               f"Off-spec: {off}\nAttendance: {row['attendance']} · iLvl: {row['ilvl']}\n"
                               "Top parses: Awaiting Warcraft Logs connection"), inline=False)
    if not characters:
        embed.add_field(name="Add your character", value="Ogre has no registered characters yet.", inline=False)
    embed.set_footer(text=f"Page {page + 1}/{pages} · WoWSims gear snapshot · Updates every minute")
    return embed, page, pages


class HubView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id

    @discord.ui.button(label="Update characters", style=discord.ButtonStyle.success,
                       custom_id="hyped_characters:update:v1")
    async def update(self, interaction, button):
        await self.cog.open_characters(interaction)

    @discord.ui.button(label="Previous", custom_id="hyped_characters:previous:v1")
    async def previous(self, interaction, button):
        await self.cog.turn(interaction, self.channel_id, -1)

    @discord.ui.button(label="Next", custom_id="hyped_characters:next:v1")
    async def next_page(self, interaction, button):
        await self.cog.turn(interaction, self.channel_id, 1)


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

    async def turn(self, interaction, channel_id, direction):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message("This board belongs to Hyped.", ephemeral=True)
            return
        await interaction.response.defer()
        async with self.lock:
            saved = next((row for row in await asyncio.to_thread(self.panels) if row[0] == channel_id), None)
            if saved and interaction.message.id == saved[1]:
                await self.refresh(channel_id, saved[1], saved[2] + direction)

    async def refresh(self, channel_id, message_id, page):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            raise ValueError("Character board channel is not in Hyped.")
        embed, page, _ = render(await asyncio.to_thread(rows, raid.RAID_DB), page)
        await channel.get_partial_message(message_id).edit(
            embed=embed, view=HubView(self, channel_id), allowed_mentions=discord.AllowedMentions.none())
        with raid.database(raid.RAID_DB) as con:
            con.execute("UPDATE character_panels SET page_index=? WHERE channel_id=?", (page, channel_id))

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
                embed, _, _ = render(await asyncio.to_thread(rows, raid.RAID_DB))
                message = await channel.send(embed=embed, view=HubView(self, channel.id),
                                             allowed_mentions=discord.AllowedMentions.none())
                with raid.database(raid.RAID_DB) as con:
                    con.execute("INSERT INTO character_panels (guild_id,channel_id,message_id,page_index) "
                                "VALUES (?,?,?,0) ON CONFLICT(channel_id) DO UPDATE SET "
                                "message_id=excluded.message_id,page_index=0", (GUILD_ID, channel.id, message.id))
        await interaction.followup.send("Ogre's character board is ready. Everyone can use /characters privately.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(CharacterHub(bot))
