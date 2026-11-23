"""Owner-only professions pilot in the existing private Ogre test channel."""
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
AUTO_CREATED_TOPIC = "Ask Ogre who can make a craft. Verified guild recipes and crafters."
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
    # The "who can" branch does not consume the action verb.
    phrase = re.sub(r"^(?:make|craft|do|enchant|transmute)\s+", "", phrase,
                    flags=re.IGNORECASE)
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
        self.public_cleanup_done = False

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
        """Only the existing private Ogre test channel is a valid destination."""
        record = await asyncio.to_thread(crafting.channel, raid.RAID_DB, GUILD_ID)
        if not record or record[0] != raid.TEST_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(raid.TEST_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(raid.TEST_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            return None
        return channel

    async def retract_unapproved_channel(self):
        """Reverse the accidentally created public channel on first startup.

        Delete *only* our own precisely recognised channel if it contains no
        human messages. Otherwise hide it from @everyone and preserve history.
        Never touch a pre-existing channel merely because of its name.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return False
        record = await asyncio.to_thread(crafting.channel, raid.RAID_DB, GUILD_ID)
        candidates = [
            channel for channel in guild.text_channels
            if channel.id != raid.TEST_CHANNEL_ID
            and channel.name == CHANNEL_NAME
            and channel.topic == AUTO_CREATED_TOPIC
        ]
        if record and record[0] != raid.TEST_CHANNEL_ID:
            specific = guild.get_channel(record[0])
            if specific is not None and specific not in candidates:
                LOG.warning("Existing configured professions channel needs manual review: %s",
                            specific.id)
        for channel in candidates:
            # Restrict access before inspection or deletion to prevent
            # continued public exposure while safety checks run.
            await channel.set_permissions(
                guild.default_role, view_channel=False,
                reason="Revert unapproved public professions rollout",
            )
            human_messages = False
            async for message in channel.history(limit=None):
                if not message.author.bot:
                    human_messages = True
                    break
            if not human_messages:
                await channel.delete(
                    reason="Remove Ogre's unapproved automatically created professions channel")
                LOG.info("Removed the unapproved bot-only professions channel.")
            else:
                LOG.warning(
                    "Unapproved professions channel contains member messages; "
                    "hidden from @everyone, retained for administrator review."
                )
        if record and record[0] != raid.TEST_CHANNEL_ID:
            # Detach the auto-post destination, even when the old channel
            # could not be deleted or was not recognisably created by Ogre.
            await asyncio.to_thread(
                crafting.set_channel, raid.RAID_DB, GUILD_ID,
                raid.TEST_CHANNEL_ID, 0,
            )
        return True

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
                # A restart may have missed chat messages. Restore the panel
                # to the bottom instead of only editing its old position.
                if channel.last_message_id in (None, prior.id):
                    await prior.edit(embed=embed)
                    return True
                bump = True
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
        if (not configured or configured[0] != raid.TEST_CHANNEL_ID
                or message.channel.id != raid.TEST_CHANNEL_ID):
            return
        term = extract_question(message.content)
        if term:
            try:
                reply = await asyncio.to_thread(answer, raid.RAID_DB, term)
                await message.reply(
                    reply, mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=False,
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
            if not self.public_cleanup_done:
                self.public_cleanup_done = await self.retract_unapproved_channel()
            # Collect data without posting anything publicly or creating
            # channels. The private panel requires owner /professions preview.
            await craft_catalog.refresh(raid.RAID_DB)
            record = await asyncio.to_thread(
                crafting.channel, raid.RAID_DB, GUILD_ID)
            if record and record[0] == raid.TEST_CHANNEL_ID and record[1]:
                await self.publish_panel()
        except (discord.HTTPException, sqlite3.Error, ValueError):
            LOG.exception("Crafting channel maintenance delayed")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="preview",
        description="Owner-only: show the professions pilot inside private Ogre",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def preview(self, interaction: discord.Interaction):
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if (interaction.guild_id != GUILD_ID
                or interaction.channel_id != raid.TEST_CHANNEL_ID
                or interaction.user.id not in (owner_id, interaction.guild.owner_id)):
            await interaction.response.send_message(
                "Ogre's crafting pilot is restricted to the owner in private Ogre.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(
                crafting.set_channel, raid.RAID_DB, GUILD_ID,
                raid.TEST_CHANNEL_ID,
            )
            await self.publish_panel(bump=True)
            await interaction.followup.send(
                "Private crafter preview posted. No public channel was created.",
                ephemeral=True,
            )
        except (sqlite3.Error, discord.HTTPException, ValueError):
            LOG.exception("Private crafting preview failed")
            await interaction.followup.send(
                "Ogre could not post the private crafter preview.", ephemeral=True,
            )

    @app_commands.command(name="refresh", description="Refresh crafting pilot in private Ogre")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def refresh(self, interaction: discord.Interaction):
        member = interaction.user
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if (interaction.channel_id != raid.TEST_CHANNEL_ID
                or interaction.user.id not in (owner_id, interaction.guild.owner_id)):

            await interaction.response.send_message("Leadership only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await armory.refresh(raid.RAID_DB)
            await craft_catalog.refresh(raid.RAID_DB)
            await self.publish_panel(bump=True)
            await interaction.followup.send(
                "Ogre refreshed the private crafting pilot.", ephemeral=True)
        except (sqlite3.Error, discord.HTTPException, ValueError) as exc:
            await interaction.followup.send(f"Crafting refresh failed: {exc}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ProfessionBoard(bot))
