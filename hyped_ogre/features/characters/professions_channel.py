"""Hyped professions directory in the existing guild professions channel."""
import asyncio
import logging
import os
import re
import sqlite3
import time

import discord
import aiohttp
from discord import app_commands
from discord.ext import commands, tasks

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from . import crafting, craft_catalog, armory, camp_tools

LOG = logging.getLogger(__name__)
COLOUR = 0xA87535
CHANNEL_NAME = "professions"
AUTO_CREATED_TOPIC = "Ask Ogre who can make a craft. Verified guild recipes and crafters."
MAX_PER_PROFESSION = 6
REFRESH_SECONDS = 900


def format_crafters(grouped, status=None):
    """V2: one full-width section per profession, with crafters beneath it."""
    embed = discord.Embed(
        title="<Hyped> Professions V2",
        description=(
            "**Guild crafters · 200+ skill**\n"
            "Ordered by confirmed learned recipes. Unknown recipe totals "
            "appear last and are never treated as zero.\n"
            "Ask: **Can anyone make Arcanite Bar?**"
        ),
        colour=COLOUR,
    )
    total = 0
    for profession in crafting.PROFESSIONS:
        people = grouped.get(profession, [])
        if not people:
            continue
        total += len(people)
        lines = []
        for person in people[:MAX_PER_PROFESSION]:
            name = discord.utils.escape_markdown(person["name"])
            count = person["recipe_count"]
            label = (
                f"{count} recipes"
                if type(count) is int and count >= 0
                else "recipes not verified"
            )
            source = (
                " · WSE" if person.get("source") == "WoWSims"
                else ""
            )
            lines.append(f"**{name}** · {person['skill']}/300 · {label}{source}")
        if len(people) > MAX_PER_PROFESSION:
            lines.append(f"*+{len(people) - MAX_PER_PROFESSION} others*")
        embed.add_field(
            name=f"{profession}  ({len(people)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    if not total:
        embed.add_field(
            name="No eligible professions recorded",
            value=(
                "Ogre hasn't confirmed any 200+ profession skills yet. "
                "Use **/profession-status** in private Ogre to check "
                "Blizzard connectivity and WoWSims data."
            ),
            inline=False,
        )
    if status:
        credential = (
            "configured" if status.get("credentials") else "NOT configured"
        )
        embed.set_footer(text=(
            f"Private pilot · Blizzard credentials {credential} · "
            f"{status.get('blizzard_characters', 0)} API profiles · "
            f"{status.get('wowsims_exports', 0)} WoWSims exports · "
            f"{status.get('verified_recipes', 0)} verified recipe records"
        )[:2048])
    else:
        embed.set_footer(text="Private pilot · Verified counts only")
    return embed

def featured_selection(profession, people):
    """Display at most three ranked crafters in each profession."""
    return list(people[:3])


def profession_emoji(guild, profession):
    """Custom Discord emoji only, when the guild has uploaded that icon."""
    stem = crafting.PROFESSION_ICON_NAMES.get(profession, "")
    if not guild or not stem:
        return ""
    candidates = (
        stem, "prof_" + profession.casefold().replace(" ", ""),
        profession.casefold().replace(" ", ""),
    )
    # User-uploaded :tailoring: may not have the UI_Profession prefix.
    if profession == "Tailoring":
        candidates += ("tailoring",)
    for name in candidates:
        emoji = discord.utils.get(guild.emojis, name=name)
        if emoji:
            return str(emoji)
    return ""


class ProfessionsV2(discord.ui.LayoutView):
    """One persistent V2 board, edited in place and pinned."""

    def __init__(self, grouped, guild=None, *, new_recipes=(),
                 camp_owners=None, updated_at=None):
        super().__init__(timeout=None)
        from . import roster
        updated_at = int(time.time() if updated_at is None else updated_at)
        elements = [
            discord.ui.Section(
                discord.ui.TextDisplay(
                    '## <Hyped> Guild Crafters\n'
                    'Ask Ogre, "Who can enchant Crusader?", '
                    '"Who can make Arcanite Bar?", etc.'
                ),
                accessory=discord.ui.Thumbnail(
                    roster.ROSTER_IMAGE, description="Hyped guild logo"
                ),
            ),
        ]
        shown = 0
        for group_name, names in (
            ("Primary professions", crafting.CRAFTING_PROFESSIONS),
            ("Gathering", crafting.GATHERING_PROFESSIONS),
        ):
            blocks = []
            for profession in names:
                people = grouped.get(profession, [])
                if not people:
                    continue
                selected = featured_selection(profession, people)
                icon = profession_emoji(guild, profession)
                heading = (
                    f"-# **{icon + ' ' if icon else ''}{profession} ({len(people)})**"
                    + camp_tools.heading_suffix(profession, camp_owners or {})
                )
                lines = [heading]
                for person in selected:
                    label = discord.utils.escape_mentions(
                        discord.utils.escape_markdown(person["name"])
                    )
                    discord_id = person.get("discord_id")
                    # Display the registered Discord handle without pinging
                    # every crafter whenever the board refreshes.
                    handle = (
                        f" / <@{discord_id}>"
                        if type(discord_id) is int and discord_id > 0
                        else ""
                    )
                    lines.append(
                        f"-# **{label}**{handle} · {person['skill']} skill"
                    )
                blocks.append("\n".join(lines))
                shown += len(selected)
            if blocks:
                elements.append(discord.ui.TextDisplay(
                    f"### {group_name}\n" + "\n\n".join(blocks)
                ))
        if not shown:
            elements.append(discord.ui.TextDisplay(
                "-# No eligible crafters found yet. Try /profession-status."
            ))
        note_emoji = (
            str(discord.utils.get(guild.emojis, name="updates_note"))
            if guild and discord.utils.get(guild.emojis, name="updates_note")
            else ""
        )
        fresh = [
            f"### {note_emoji + ' ' if note_emoji else ''}New recipes"
        ]
        for recipe in new_recipes[:5]:
            # Blizzard recipe IDs are not guaranteed to be Wowhead spell IDs.
            # A Wowhead Forever *search* link avoids linking the wrong spell.
            from urllib.parse import quote
            name = discord.utils.escape_markdown(recipe["recipe_name"])
            url = "https://www.wowhead.com/forever/search?q=" + quote(
                recipe["recipe_name"], safe=""
            )
            player = discord.utils.escape_mentions(
                discord.utils.escape_markdown(recipe["character_name"])
            )
            fresh.append(
                f"-# [{name}](<{url}>) · {player}"
            )
        if len(fresh) == 1:
            fresh.append("-# Newly learning 300 recipes will be listed here.")
        elements.append(discord.ui.TextDisplay("\n".join(fresh)))
        # Discord renders both absolute and relative times in the reader's
        # timezone. This is the board's last successful render, not a claim
        # that every Blizzard character profile was refreshed successfully.
        elements.append(discord.ui.TextDisplay(
            f"-# Last updated: <t:{updated_at}:f> · <t:{updated_at}:R>"
        ))
        self.add_item(discord.ui.Container(*elements, accent_color=COLOUR))


def extract_question(content):
    """Recognise common crafting requests without replying to general chat.

    A question need not use one particular template. Gate on either a
    craft/action verb, a recipe/profession keyword, or a short 'who can'
    construction. Exact recipe verification still happens in crafting.query.
    """
    text = " ".join(str(content or "").split()).strip()
    if not 3 <= len(text) <= 240:
        return None
    # Remove mentions/addressing Ogre and trailing punctuation.
    text = re.sub(r"^(?:(?:hey|hi|ogre|hyped ogre)[,:! ]+)+", "",
                  text, flags=re.IGNORECASE).strip()
    text = re.sub(r"[?!.]+$", "", text).strip()
    if not text:
        return None

    # Specific request wordings, including informal guild chat.
    patterns = (
        # Acquisition and reagent requests (never invent source/materials).
        r"^(?:where (?:can|do) (?:i|we) (?:get|learn|find|buy) "
        r"|where does (?:the )?|how (?:do|can) (?:i|we) "
        r"(?:get|learn|find) )(?:the )?(?:recipe for )?(.+)$",
        r"^(?:what (?:mats|materials|reagents|ingredients) "
        r"(?:are needed for|do i need for|for|to (?:make|craft|enchant)) "
        r"|(?:mats|materials|reagents|ingredients) "
        r"(?:for|needed for) )(.+)$",
        # Recognise a member supplying a full canonical enchant name.
        r"^(?:(?:chest|weapon|gloves|boots|bracer|bracers|cloak|shield)"
        r"\s*:\s*)?((?:enchant|transmute|recipe|pattern|schematic|plans)"
        r"\s+.+)$",
        r"^(?:who (?:has|knows|does|can do|can make|can craft|can enchant)"
        r"\s+)(.+)$",
        r"^(?:can (?:we|someone|anybody|anyone) (?:get|sort|provide|do)"
        r"\s+)(.+)$",
        r"^(?:anyone (?:for|with)\s+)(.+)$",
        r"^(?:who(?:'s| is)? (?:able to |around to )?"
        r"(?:can |could |knows how to |knows |has )?)"
        r"(?:make|craft|enchant|transmute|forge|smith|brew|sew|do|learned|got|know)"
        r"\s+(.+)$",
        r"^(?:can|could|does|is) (?:anyone|anybody|someone|somebody|you|we)"
        r"\s+(?:(?:here|in (?:the )?guild)\s+)?"
        r"(?:(?:make|craft|enchant|transmute|forge|smith|brew|sew|do|know|"
        r"have|got|learned|provide|create)\s+)(.+)$",
        r"^(?:anyone|anybody|someone|somebody)\s+"
        r"(?:able to |who can |that can |know |knows |got |have |has |"
        r"make |makes |craft |crafts |enchant |enchants |do |does |"
        r"transmute |transmutes )(.+)$",
        r"^(?:(?:i(?:'m| am)? )?(?:looking for|after|needing|need|want|"
        r"looking to get|could use)|(?:lf|wtb|lfg))\s+(.+)$",
        r"^(?:do (?:we|you) have|is there (?:anyone|a crafter) (?:for|who can))"
        r"\s+(.+)$",
        r"^(?:(?:please |could you |can you )?(?:find|show|tell me|"
        r"check|ask ogre)\s+(?:me )?(?:a |someone |who |whether )?"
        r"(?:can |to )?(?:make|craft|enchant|transmute|do)?\s*)(.+)$",
        r"^(?:make|craft|enchant|transmute|forge|smith|brew|sew)\s+(.+)$",
    )
    request = None
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            request = match.group(1).strip(" ,.!?")
            break
    if request is None:
        # Shorthand requests like "Crusader enchant?", "Arcanite bar crafter?"
        # need an explicit profession/craft noun to avoid unrelated replies.
        if re.search(
            r"\b(?:enchant(?:ment|ing|er)?|crafter|crafting|recipe|"
            r"transmute|transmutation|blacksmith|alchemist|tailor|"
            r"leatherworker|engineer)\b", text, flags=re.IGNORECASE
        ) and (text.endswith("?") or "?" in str(content)):
            request = text
        else:
            return None
    request = re.sub(
        r"\s+(?:drop|drops|recipe|pattern|plans|schematic)$",
        "", request, flags=re.IGNORECASE
    ).strip()
    # Preserve canonical recipe names, e.g. "Enchant Chest - Major
    # Health", when supplied in full in a follow-up or sourcing question.
    if re.match(
        r"^(?:enchant|transmute) (?:chest|weapon|boots|gloves|"
        r"bracer|bracers|cloak|shield)\b",
        request, flags=re.IGNORECASE
    ):
        return request
    # Strip boilerplate without removing meaningful recipe names.
    request = re.sub(
        r"^(?:someone (?:to|who can) |"
        r"(?:to |for |me |us |a |an |the |some |any )+|"
        r"(?:a )?crafter (?:for|to) |"
        r"(?:someone )?(?:to )?(?:make|craft|do|enchant|transmute|"
        r"forge|smith|brew|sew)\s+)",
        "", request, flags=re.IGNORECASE
    ).strip(" ,.!?")
    request = re.sub(
        r"^(?:(?:someone|somebody) (?:to|who can) |"
        r"(?:a )?crafter (?:for|to) |"
        r"(?:make|craft|do|enchant|transmute|forge|smith|brew|sew) )+",
        "", request, flags=re.IGNORECASE
    ).strip()
    request = re.sub(
        r"^(?:a |an |the |some |my |their |our |one |"
        r"recipe (?:for |to )?|enchant (?:for |on |my |the )?)+",
        "", request, flags=re.IGNORECASE
    ).strip()
    return request if len(request) >= 3 else None

def request_kind(content):
    """Return the response family without inferring unverified item data."""
    text = str(content or "").lower().strip()
    if re.match(
        r"^(?:where (?:can|do|does)|how (?:do|can) (?:i|we) (?:get|learn|find))",
        text
    ):
        return "source"
    if re.match(
        r"^(?:what (?:mats|materials|reagents|ingredients)|"
        r"(?:mats|materials|reagents|ingredients) (?:for|needed for))",
        text
    ):
        return "materials"
    return "crafter"


def answer(path, term, *, kind="crafter"):
    result = crafting.query(path, term)
    if not result or result["status"] == "unknown":
        options = (result or {}).get("suggestions", [])
        if options:
            names = ", ".join(
                f"**{discord.utils.escape_markdown(name)}**"
                for name in options[:3]
            )
            return (
                f"Ogre couldn't verify **{discord.utils.escape_markdown(term)}** "
                f"as a recipe. Did you mean {names}? "
                "Reply with a recipe name and Ogre will check the guild."
            )
        return (
            f"Ogre couldn't match **{discord.utils.escape_markdown(term)}** "
            "to the Forever recipe database. Try a full recipe name, "
            "an item, or an enchant bonus and equipment slot, "
            "for example '+100 HP on chest'."
        )
    if result["status"] == "unavailable":
        return ("Ogre hasn't finished collecting the Forever recipe list. "
                "Try again after the next Blizzard sync.")
    if result["status"] == "ambiguous":
        labels = ", ".join(result["matches"][:4])
        return (f"Ogre found multiple matching crafts: {labels}. "
                "Which one do you need? Reply with its full name.")
    recipes = result["recipes"]
    people = result["crafters"]
    recipe_label = recipes[0]["name"] if len(recipes) == 1 else term
    if kind == "materials":
        return (
            f"Ogre found **{recipe_label}**, but verified reagent quantities "
            "aren't in the crafting database yet. Ogre won't guess. "
            "The guild crafter list can still be checked with "
            f"'Who can make {recipe_label}?'"
        )
    if kind == "source":
        known = next(
            (recipe for recipe in recipes if
             recipe["verified"] and recipe["acquisition"]), None
        )
        if known:
            extra = (
                f" Source: {known['source_url']}"
                if known["source_url"] else ""
            )
            return (
                f"**{recipe_label}**: {known['acquisition']}." + extra
            )
        return (
            f"Ogre recognises **{recipe_label}**, but hasn't verified "
            "where its recipe is learned or obtained."
        )
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
    return (
        f"Ogre recognises **{recipe_label}**, but no verified guild crafter "
        "is currently recorded as knowing it. "
        "Recipe acquisition source not verified yet."
    )


class ProfessionBoard(commands.GroupCog, group_name="professions",
                      group_description="Manage Hyped's crafting channel"):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.refresh_at = 0.0
        self.catalog_at = 0.0
        self.scheduled = {}
        self.previous_signature = None
        self.public_activation_done = False

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
        """Resolve only a previously configured existing guild text channel."""
        record = await asyncio.to_thread(crafting.channel, raid.RAID_DB, GUILD_ID)
        if not record:
            return None
        channel = self.bot.get_channel(record[0])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(record[0])
            except (discord.NotFound, discord.Forbidden):
                return None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
            return None
        return channel

    async def activate_existing_public_channel(self):
        """Release ONLY an existing unambiguous #professions channel.

        Never create/delete a channel or select one in a different guild.
        Previously hidden channels are made public again by restoring
        @everyone visibility while leaving other overwrites untouched.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return False
        channels = [
            channel for channel in guild.text_channels
            if channel.name == CHANNEL_NAME and channel.id != raid.TEST_CHANNEL_ID
        ]
        if len(channels) != 1:
            LOG.warning(
                "Professions release needs exactly one existing #%s channel; found %s. "
                "No public channel created or changed.",
                CHANNEL_NAME, len(channels),
            )
            return False
        channel = channels[0]
        existing = await asyncio.to_thread(
            crafting.channel, raid.RAID_DB, GUILD_ID
        )
        if existing and existing[0] == channel.id:
            return True
        try:
            # The earlier private-pilot rollback explicitly hid @everyone.
            # Restore visibility without replacing other role/user settings.
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.view_channel is False:
                overwrite.view_channel = True
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite,
                    reason="Guild leader approved public professions release",
                )
            await asyncio.to_thread(
                crafting.set_channel, raid.RAID_DB, GUILD_ID, channel.id, 0,
            )
            LOG.info("Professions now active in existing public channel %s", channel.id)
            return True
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error):
            LOG.exception("Cannot activate existing public professions channel")
            return False

    async def publish_panel(self, *, bump=False):
        channel = await self._channel()
        if channel is None:
            return False
        async with self.lock:
            record = await asyncio.to_thread(
                crafting.channel, raid.RAID_DB, GUILD_ID)
            grouped = await asyncio.to_thread(crafting.board_rows, raid.RAID_DB)
            newest = await asyncio.to_thread(
                crafting.latest_high_skill_discoveries, raid.RAID_DB, 5)
            learned_tools = await asyncio.to_thread(
                camp_tools.owners, raid.RAID_DB
            )
            panel = ProfessionsV2(
                grouped, channel.guild, new_recipes=newest,
                camp_owners=learned_tools,
            )
            prior = None
            if record and record[1]:
                try:
                    prior = await channel.fetch_message(record[1])
                except (discord.NotFound, discord.Forbidden):
                    pass
            if prior:
                # Never delete/repost the large board in response to chat.
                await prior.edit(
                    content=None, embed=None, view=panel,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if not prior.pinned:
                    try:
                        await prior.pin(reason="Hyped crafting reference board")
                    except discord.HTTPException:
                        LOG.warning("Could not pin crafter board")
                return True
            new = await channel.send(
                view=panel, allowed_mentions=discord.AllowedMentions.none()
            )
            try:
                await new.pin(reason="Hyped crafting reference board")
            except discord.HTTPException:
                LOG.warning("Could not pin crafter board")
            await asyncio.to_thread(
                crafting.set_panel, raid.RAID_DB, GUILD_ID, channel.id, new.id)
        return True

    @commands.Cog.listener()
    async def on_message(self, message):
        if (message.author.bot or message.guild is None
                or message.guild.id != GUILD_ID):
            return
        record = await asyncio.to_thread(
            crafting.channel, raid.RAID_DB, GUILD_ID)
        if (not record or not record[1]
                or message.channel.id != record[0]):
            return
        term = extract_question(message.content)
        if not term:
            return
        reply_id = None
        try:
            answer_text = await asyncio.to_thread(
                answer, raid.RAID_DB, term,
                kind=request_kind(message.content),
            )
            reply = await message.reply(
                answer_text, mention_author=False,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=False,
                    replied_user=False,
                ),
            )
            reply_id = reply.id
        except (sqlite3.Error, discord.HTTPException):
            LOG.exception("Crafting answer unavailable")
        check = discord.utils.get(message.guild.emojis, name="check")
        if check is not None:
            try:
                await message.add_reaction(check)
            except discord.HTTPException:
                LOG.warning("Could not add custom :check: reaction to craft request")
        else:
            LOG.warning("Guild :check: emoji missing; crafting request tracked without tick")
        await asyncio.to_thread(
            crafting.store_request, raid.RAID_DB,
            request_id=message.id, guild_id=GUILD_ID,
            channel_id=message.channel.id, reply_id=reply_id,
            requested_at=int(message.created_at.timestamp()),
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if (payload.guild_id != GUILD_ID
                or (self.bot.user and payload.user_id == self.bot.user.id)):
            return
        guild = self.bot.get_guild(GUILD_ID)
        check = discord.utils.get(guild.emojis, name="check") if guild else None
        if check is None or payload.emoji.id != check.id:
            return
        record = await asyncio.to_thread(
            crafting.channel, raid.RAID_DB, GUILD_ID
        )
        if not record or payload.channel_id != record[0]:
            return
        if not await asyncio.to_thread(
            crafting.request_exists, raid.RAID_DB,
            payload.message_id, payload.channel_id,
        ):
            return
        await asyncio.to_thread(
            crafting.resolve_request, raid.RAID_DB,
            request_id=payload.message_id, user_id=payload.user_id,
        )

    async def expire_requests(self):
        """Clean ONLY tracked craft requests and their bot replies.

        A tick starts a one-day timer; otherwise requests expire three
        days after the original message, even across bot restarts.
        """
        due = await asyncio.to_thread(
            crafting.expired_requests, raid.RAID_DB)
        if not due:
            return 0
        channel = await self._channel()
        if channel is None:
            return 0
        removed = 0
        for request in due:
            if request["channel_id"] != channel.id:
                continue
            try:
                request_msg = await channel.fetch_message(request["request_id"])
                await request_msg.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                LOG.warning("Unable to delete expired crafting request")
                continue
            if request["reply_id"]:
                try:
                    reply_msg = await channel.fetch_message(request["reply_id"])
                    if reply_msg.author.id == self.bot.user.id:
                        await reply_msg.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    LOG.warning("Unable to delete old Ogre crafting reply")
                    continue
            await asyncio.to_thread(
                crafting.forget_request, raid.RAID_DB,
                request["request_id"],
            )
            removed += 1
        return removed

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def maintenance(self):
        try:
            if not self.public_activation_done:
                self.public_activation_done = await self.activate_existing_public_channel()
            # Refresh profession ownership every 15 minutes, including
            # members whose level/iLvl cache is otherwise still fresh.
            # 36 requests is bounded to approximately the current roster.
            # Failed API responses retain previous verified recipe snapshots.
            if armory.credentials_configured():
                try:
                    await armory.refresh(
                        raid.RAID_DB, for_crafting=True, limit=36,
                    )
                except (sqlite3.Error, ValueError, aiohttp.ClientError):
                    LOG.warning("Profession API refresh delayed.")
            await craft_catalog.refresh(raid.RAID_DB)
            await self.expire_requests()
            record = await asyncio.to_thread(
                crafting.channel, raid.RAID_DB, GUILD_ID)
            if record and record[0] != raid.TEST_CHANNEL_ID:
                await self.publish_panel()
        except (discord.HTTPException, sqlite3.Error, ValueError):
            LOG.exception("Crafting channel maintenance delayed")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="preview",
        description="Guild owner: post or update the public professions board",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def preview(self, interaction: discord.Interaction):
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if (interaction.guild_id != GUILD_ID
                or interaction.user.id not in (owner_id, interaction.guild.owner_id)):
            await interaction.response.send_message(
                "Only the guild owner can post the professions board.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            refreshed = await armory.refresh(
                raid.RAID_DB, for_crafting=True, limit=6,
            )
            active = await self.activate_existing_public_channel()
            if not active:
                await interaction.followup.send(
                    "Ogre can't find exactly one existing #professions channel. "
                    "No channel was created. Restore/create one manually and try again.",
                    ephemeral=True,
                )
                return
            self.public_activation_done = True
            posted = await self.publish_panel(bump=True)
            status = await asyncio.to_thread(crafting.diagnostics, raid.RAID_DB)
            await interaction.followup.send(
                ("Guild professions board posted. " if posted else
                 "Ogre could not post the guild professions board. ")
                + f"Refreshed {refreshed} Armory profiles; "
                + f"{status['blizzard_characters']} with recorded professions; "
                + f"{status['verified_recipes']} confirmed recipe records. "
                + ("Blizzard credentials are configured." if status['credentials']
                   else "Blizzard credentials are NOT configured on Fly.io. ")
                + "Use /profession-status for details.",
                ephemeral=True,
            )
        except (sqlite3.Error, discord.HTTPException, ValueError):
            LOG.exception("Private crafting preview failed")
            await interaction.followup.send(
                "Ogre could not post the guild crafter board.", ephemeral=True,
            )

    @app_commands.command(name="refresh", description="Refresh the existing professions board")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def refresh(self, interaction: discord.Interaction):
        member = interaction.user
        owner_id = int(os.getenv("OWNER_ID") or 0)
        if (interaction.guild_id != GUILD_ID
                or interaction.user.id not in (owner_id, interaction.guild.owner_id)):

            await interaction.response.send_message("Guild owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await armory.refresh(raid.RAID_DB, for_crafting=True, limit=6)
            await craft_catalog.refresh(raid.RAID_DB)
            active = await self.activate_existing_public_channel()
            posted = await self.publish_panel(bump=True) if active else False
            await interaction.followup.send(
                "Ogre refreshed the public crafting board." if posted
                else "No existing #professions destination found.", ephemeral=True)
        except (sqlite3.Error, discord.HTTPException, ValueError) as exc:
            await interaction.followup.send(f"Crafting refresh failed: {exc}", ephemeral=True)


class ProfessionPilotCommands(commands.Cog):
    """Guild-owner management of the existing professions channel."""

    def __init__(self, bot, board):
        self.bot = bot
        self.board = board

    @staticmethod
    def authorised(interaction):
        owner_id = int(os.getenv("OWNER_ID") or 0)
        return (
            interaction.guild_id == GUILD_ID
            and interaction.user.id in (
                owner_id, getattr(interaction.guild, "owner_id", None)
            )
        )

    @app_commands.command(
        name="postprofessions-v2",
        description="Guild owner: post the V2 board in existing #professions",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def post_professions(self, interaction: discord.Interaction):
        # Delegate to the same owner-only handler as /professions preview.
        await self.board.preview.callback(self.board, interaction)

    @app_commands.command(
        name="profession-status",
        description="Guild owner: inspect Blizzard professions API coverage",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def profession_status(self, interaction: discord.Interaction):
        if not self.authorised(interaction):
            await interaction.response.send_message(
                "Guild owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        stats = await asyncio.to_thread(crafting.diagnostics, raid.RAID_DB)
        await interaction.followup.send(
            "**Ogre professions diagnostics**\n"
            f"Blizzard credentials: **{'configured' if stats['credentials'] else 'MISSING'}**\n"
            f"Registered visible characters: **{stats['visible']}**\n"
            f"Characters with Blizzard professions: **{stats['blizzard_characters']}**\n"
            f"Profession entries with verified recipe lists: **{stats['verified_professions']}**\n"
            f"Individual learned-recipe records: **{stats['verified_recipes']}**\n"
            f"Official recipe catalogue: **{stats['catalog']}**\n"
            f"Saved WoWSims gear exports: **{stats['wowsims_exports']}**\n"
            "WoWSims supplies profession skills only, not verified recipe ownership. "
            "If credentials are missing, configure BLIZZARD_CLIENT_ID and "
            "BLIZZARD_CLIENT_SECRET on Fly.io.",
            ephemeral=True,
        )


async def setup(bot):
    board = ProfessionBoard(bot)
    await bot.add_cog(board)
    await bot.add_cog(ProfessionPilotCommands(bot, board))
