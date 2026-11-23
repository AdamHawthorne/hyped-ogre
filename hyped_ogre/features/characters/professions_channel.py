"""Public guild professions channel: verified crafter board and craft Q&A."""
import asyncio
import logging
import os
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from . import crafting, craft_catalog, armory

LOG = logging.getLogger(__name__)
COLOUR = 0xA87535
CHANNEL_NAME = "professions"
MAX_PER_PROFESSION = 6
REFRESH_SECONDS = 900


def format_crafters(grouped):
    """Maximum ten profession fields; show only eligible 200+ skill crafters."""
    embed = discord.Embed(
        title="<Hyped> Guild Crafters",
        description=("Verified learned recipes from Forever Armory. "
                     "Only crafters with 200+ skill are listed. "
                     "Ask Ogre below: **Can anyone make Arcanite Bar?**"),
        colour=COLOUR,
    )
    count = 0
    for profession in crafting.PROFESSIONS:
        people = grouped.get(profession, [])
        if not people:
            continue
        count += len(people)
        # Listing more than six per profession makes the board too tall.
        lines = [
            f"**{discord.utils.escape_markdown(person['name'])}** · "
            f"{person['skill']} skill · {person['recipe_count']} recipes"
            for person in people[:MAX_PER_PROFESSION]
        ]
        if len(people) > MAX_PER_PROFESSION:
            lines.append(f"+{len(people) - MAX_PER_PROFESSION} more")
        embed.add_field(
            name=f"{profession} ({len(people)})",
            value="\n".join(lines)[:1024], inline=True,
        )
    if not count:
        embed.add_field(
            name="Crafters syncing",
            value="No verified 200+ skill crafters recorded yet. "
                  "Ogre will update this board as the Forever API responds.",
            inline=False,
        )
    embed.set_footer(text="Hyped · Recipe counts are verified, not estimated")
    return embed


def extract_question(content):
    """Respond to crafting requests, not unrelated guild conversation."""
    text = " ".join(str(content).strip().split())
    if len(text) > 240 or len(text) < 7:
        return None
    prefixes = (
        r"^(?:hey |ogre[, ]+)?(?:can (?:anyone|anybody|someone|you) "
        r"(?:make|craft|do|enchant|transmute)|who (?:can|knows|makes|crafts) "
        r"|(?:anyone|anybody) (?:know|make|craft|do)"
        r"|(?:looking for|need) (?:someone to )?(?:make|craft|enchant)?"
        r"|(?:can you find|find) (?:a )?(?:crafter for)?"
        r"|(?:craft|make) )\s*(.*?)[?!.]*$"
    )
    match = re.match(prefixes, text, re.IGNORECASE)
    if not match:
        return None
    phrase = match.group(1).strip(" ?.!").strip()
    phrase = re.sub(
        r"^(?:me |an? |the |some |recipe (?:for |to )?|one |any )",
        "", phrase, flags=re.IGNORECASE)
    return phrase if len(phrase) >= 3 else None


def answer(path, term):
    result = crafting.query(path, term)
    if not result or result["status"] == "unknown":
        return ("Ogre can't match that craft to a verified Forever recipe yet. "
                "Try the exact item or recipe name.")
    if result["status"] == "unavailable":
        return ("Ogre hasn't finished collecting the Forever recipe list. "
                "Try again after the next Blizzard sync.")
    if result["status"] == "ambiguous":
        labels = ", ".join(result["matches"][:4])
        return f"Ogre found several recipes: {labels}. Ask for a more specific one."
    recipes = result["recipes"]
    people = result["crafters"]
    recipe_label = recipes[0]["name"] if len(recipes) == 1 else term
    if people:
        # One Discord user may have several capable characters. Show up to 3.
        unique = set()
        display = []
        for player in people:
            key = (player["discord_id"], player["name"])
            if key in unique:
                continue
            unique.add(key)
            display.append(
                f"<@{player['discord_id']}> can do **{recipe_label}** "
                f"on **{discord.utils.escape_markdown(player['name'])}**.")
            if len(display) >= 3:
                break
        return "\n".join(display)
    verified = next((r for r in recipes if r["verified"] and r["acquisition"]), None)
    if verified:
        return (
            f"Ogre has no verified guild crafter for **{recipe_label}** yet. "
            f"It requires **{verified['acquisition']}**. "
            f"Source: {verified['source_url']}"
        )
    return (f"Ogre has no verified guild crafter for **{recipe_label}** yet. "
            "Recipe source not verified. Ogre won't guess.")


class ProfessionBoard(commands.GroupCog, group_name="professions",
                      group_description="Manage Hyped's crafting channel"):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.refresh_at = 0.0
        self.catalog_at = 0.0
        self.scheduled = {}
        self.previous_signature = None

    async def cog_load(self):
        if int(os.getenv("HYPED_GUILD_ID") or 0) != GUILD_ID:
            return
        await asyncio.to_thread(crafting.initialise, raid.RAID_DB)
        self.maintenance.start()

    async def cog_unload(self):
        self.maintenance.cancel()
        for task in self.scheduled.values():
            task.cancel()

    async def _channel(self):
        record = await asyncio.to_thread(crafting.channel, raid.RAID_DB, GUILD_ID)
        if not record:
            return None
        channel = self.bot.get_channel(record[0])
        if channel is None:
            channel = await self.bot.fetch_channel(record[0])
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            return None
        return channel

    async def ensure_channel(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        configured = await self._channel()
        if configured:
            return configured
        # Never create a duplicate of an existing channel. Reuse by name,
        # but only in the expected guild. A channel is created once.
        channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if channel is None:
            member = guild.me
            if member is None or not member.guild_permissions.manage_channels:
                LOG.warning("Ogre needs Manage Channels to create professions")
                return None
            channel = await guild.create_text_channel(
                CHANNEL_NAME,
                topic="Ask Ogre who can make a craft. Verified guild recipes and crafters.",
                reason="Hyped verified guild crafting directory",
            )
        await asyncio.to_thread(
            crafting.set_channel, raid.RAID_DB, GUILD_ID, channel.id)
        return channel

    async def publish_panel(self, *, bump=False):
        channel = await self._channel()
        if channel is None:
            return False
        async with self.lock:
            record = await asyncio.to_thread(
                crafting.channel, raid.RAID_DB, GUILD_ID)
            grouped = await asyncio.to_thread(crafting.leaderboard, raid.RAID_DB)
            embed = format_crafters(grouped)
            prior = None
            if record and record[1]:
                try:
                    prior = await channel.fetch_message(record[1])
                except (discord.NotFound, discord.Forbidden):
                    pass
            if not bump and prior:
                await prior.edit(embed=embed)
                return True
            # After a message or answer, re-post as the latest message.
            # Delete only Ogre's own former panel, never member messages.
            if prior:
                try:
                    await prior.delete()
                except discord.HTTPException:
                    LOG.warning("Old crafter panel couldn't be deleted")
            new = await channel.send(embed=embed,
                                     allowed_mentions=discord.AllowedMentions.none())
            await asyncio.to_thread(
                crafting.set_panel, raid.RAID_DB, GUILD_ID, channel.id, new.id)
        return True

    async def _debounced_panel(self, channel_id):
        try:
            await asyncio.sleep(3)
            await self.publish_panel(bump=True)
        except (discord.HTTPException, sqlite3.Error, ValueError):
            LOG.exception("Crafting board bump failed")
        finally:
            self.scheduled.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or message.guild.id != GUILD_ID:
            return
        configured = await asyncio.to_thread(
            crafting.channel, raid.RAID_DB, GUILD_ID)
        if not configured or message.channel.id != configured[0]:
            return
        term = extract_question(message.content)
        if term:
            try:
                reply = await asyncio.to_thread(answer, raid.RAID_DB, term)
                await message.reply(
                    reply, mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=True,
                        replied_user=False),
                )
            except (sqlite3.Error, discord.HTTPException):
                LOG.exception("Crafting answer unavailable")
        previous = self.scheduled.get(message.channel.id)
        if previous:
            previous.cancel()
        self.scheduled[message.channel.id] = asyncio.create_task(
            self._debounced_panel(message.channel.id))

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def maintenance(self):
        try:
            await self.ensure_channel()
            # The existing character Armory loop supplies character knowledge.
            # This importer only supplies the official static recipe index.
            await craft_catalog.refresh(raid.RAID_DB)
            await self.publish_panel()
        except (discord.HTTPException, sqlite3.Error, ValueError):
            LOG.exception("Crafting channel maintenance delayed")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="refresh", description="Update the guild crafting board and recipe index")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def refresh(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not (
            member.guild_permissions.manage_guild or
            any(role.id == LEADERSHIP_ROLE_ID for role in member.roles)
        ):
            await interaction.response.send_message("Leadership only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await armory.refresh(raid.RAID_DB)
            await craft_catalog.refresh(raid.RAID_DB)
            await self.publish_panel(bump=True)
            await interaction.followup.send("Ogre refreshed the guild crafters.", ephemeral=True)
        except (sqlite3.Error, discord.HTTPException, ValueError) as exc:
            await interaction.followup.send(f"Crafting refresh failed: {exc}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ProfessionBoard(bot))
