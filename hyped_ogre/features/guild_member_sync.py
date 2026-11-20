"""Read current Discord role membership for an officer-reviewed game rank plan."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sqlite3
from contextlib import closing
import time
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID


class RankExportError(ValueError):
    pass


def registered_characters(path: str) -> list[dict]:
    if not Path(path).is_file():
        raise RankExportError("The registered character database is unavailable.")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as con, con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT character_id,discord_id,name,is_main FROM characters ORDER BY discord_id,character_id LIMIT 1001").fetchall()
    if len(rows) > 1000:
        raise RankExportError("Too many registered characters for one export.")
    return [dict(row) for row in rows]


async def create_export(bot: Any, path: str | None = None) -> dict:
    from cogs import raid_signup as raid

    guild = bot.get_guild(GUILD_ID)
    try:
        role_id = int(os.getenv("HYPED_RANK_DISCORD_ROLE_ID") or os.getenv("FOREVER_ROLE_ID") or 0)
    except ValueError as exc:
        raise RankExportError("Configure the Forever Discord role ID.") from exc
    role = guild.get_role(role_id) if guild else None
    if guild is None or role is None or role_id == GUILD_ID:
        raise RankExportError("The configured Forever Discord role is unavailable in Hyped.")
    characters = await asyncio.to_thread(registered_characters, path or raid.RAID_DB)
    started = int(time.time())
    gate = asyncio.Semaphore(4)

    async def lookup(member_id: int) -> tuple[int, bool, bool]:
        async with gate:
            try:
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                return member_id, False, False
            except discord.HTTPException as exc:
                raise RankExportError("Discord membership could not be verified. Try exporting again.") from exc
            return member_id, True, any(r.id == role_id for r in member.roles)

    try:
        facts = await asyncio.wait_for(asyncio.gather(*(lookup(i) for i in sorted({int(c["discord_id"]) for c in characters}))), timeout=60)
    except asyncio.TimeoutError as exc:
        raise RankExportError("Discord membership lookup timed out. No partial export was produced.") from exc
    membership = {i: (present, eligible) for i, present, eligible in facts}
    members = []
    for row in characters:
        present, eligible = membership[int(row["discord_id"])]
        members.append({"character": row["name"], "characterId": row["character_id"],
                        "discordId": str(row["discord_id"]), "isMain": bool(row["is_main"]),
                        "memberPresent": present, "eligible": eligible})
    return {"format": "HGT_DISCORD_RANKS", "version": 1, "guild": "Hyped",
            "discordGuildId": str(GUILD_ID), "generatedAt": started,
            "roleId": str(role.id), "roleName": role.name, "members": members}


def member_export_text(snapshot: dict) -> str:
    """Versioned review snapshot; never an executable promotion plan."""
    if snapshot.get("guild") != "Hyped" or snapshot.get("format") != "HGT_DISCORD_RANKS" or snapshot.get("version") != 1:
        raise RankExportError("Invalid membership snapshot.")
    stamp = snapshot.get("generatedAt")
    if type(stamp) is not int or stamp < 1:
        raise RankExportError("Invalid membership timestamp.")
    members = snapshot.get("members")
    if not isinstance(members, list) or not 1 <= len(members) <= 1000:
        raise RankExportError("Export requires 1 to 1000 registered characters.")
    lines = [f"HGT|3|MEMBERS|Hyped|{stamp}"]
    seen = set()
    groups = {}
    for row in members:
        name = row.get("character")
        if (not isinstance(name, str) or not 2 <= len(name.encode("utf-8")) <= 80
                or not any(c.isalpha() for c in name)
                or name != name.strip() or any(not (c.isalpha() or c in " '-") for c in name)):
            raise RankExportError("A registered character has an invalid full name. Correct it before exporting.")
        normalized = re.sub(r"\s+", " ", name.replace("-", " ")).strip().lower()
        if normalized in seen:
            raise RankExportError(f"Duplicate registered character: {name}. Resolve the database entries first.")
        seen.add(normalized)
        if any(type(row.get(field)) is not bool for field in ("isMain", "memberPresent", "eligible")):
            raise RankExportError("Incomplete membership facts.")
        main = "main" if row["isMain"] else "alt"
        membership = "left" if not row["memberPresent"] else "forever" if row["eligible"] else "no_role"
        owner = row.get("discordId", name)
        group = groups.setdefault(owner, f"G{len(groups)+1}")
        lines.append(f"CHAR|{name}|{main}|{membership}|unknown|{group}")
    lines.append("END")
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > 32768:
        raise RankExportError("Member export exceeds the addon's 32 KB limit.")
    return text


class GuildRankSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    @app_commands.command(name="guild-member-export", description="Copy registered Discord characters into the addon for rank review")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.default_permissions(manage_guild=True)
    async def export_members(self, interaction: discord.Interaction):
        if interaction.guild_id != GUILD_ID or interaction.guild is None:
            await interaction.response.send_message("Use this in Hyped.", ephemeral=True)
            return
        authorized = (interaction.user.id == interaction.guild.owner_id
                      or any(r.id == LEADERSHIP_ROLE_ID for r in getattr(interaction.user, "roles", []))
                      or await self.bot.is_owner(interaction.user))
        if not authorized:
            await interaction.response.send_message("Member exports are for the owner or Leadership.", ephemeral=True)
            return
        if self.lock.locked():
            await interaction.response.send_message("An export is already being prepared. Try again shortly.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            try:
                text = member_export_text(await create_export(self.bot))
                inline = ("\n```text\n" + text + "\n```") if len(text) <= 1400 else ""
                await interaction.followup.send(
                    "Open the attached text file and copy all its contents into /hyped import. "
                    "Preview, Accept Import, then open Discord Members. View valid for 24 hours; prepare promotions within 15 minutes. "
                    "Choose the target rank and whether to include alts in the addon, then Prepare rank sync. Confirm each promotion in Rank Review. Regular+ is not assessed." + inline,
                    file=discord.File(io.BytesIO(text.encode("utf-8")), filename="hyped-members.txt"),
                    ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except (RankExportError, sqlite3.Error) as exc:
                await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())



async def setup(bot):
    await bot.add_cog(GuildRankSync(bot))
