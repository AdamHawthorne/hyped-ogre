"""Read current Discord role membership for an officer-reviewed game rank plan."""
from __future__ import annotations

import asyncio
import io
import json
import os
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


class GuildRankSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    @app_commands.command(name="guild-rank-export", description="Export current Forever role membership for guild rank review")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.default_permissions(manage_guild=True)
    async def export_roles(self, interaction: discord.Interaction):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message("Use this in Hyped.", ephemeral=True)
            return
        authorized = (interaction.user.id == interaction.guild.owner_id
                      or any(r.id == LEADERSHIP_ROLE_ID for r in getattr(interaction.user, "roles", []))
                      or await self.bot.is_owner(interaction.user))
        if not authorized:
            await interaction.response.send_message("Guild rank exports are for the owner or Leadership.", ephemeral=True)
            return
        if self.lock.locked():
            await interaction.response.send_message("An export is already being prepared. Try again shortly.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            try:
                result = await create_export(self.bot)
                data = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
                await interaction.followup.send(
                    "Load this JSON in Hyped Companion's Rank Sync tab within 15 minutes. It contains verified role membership and registered full names. Review each proposal in game.",
                    file=discord.File(io.BytesIO(data), filename="hyped-discord-ranks.json"), ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none())
            except (RankExportError, sqlite3.Error) as exc:
                await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def register_route(app: Any, bot: Any, secret: str) -> None:
    from aiohttp import web
    from .guild_sync import _valid_signature
    lock = asyncio.Lock()
    last_started = 0.0

    async def rank_roster(request):
        nonlocal last_started
        body = await request.read()
        if len(body) > 1024 or not _valid_signature(secret, request.headers.get("X-Hyped-Timestamp", ""),
                                                  request.headers.get("X-Hyped-Signature", ""), body, int(time.time())):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = json.loads(body.decode())
        except (ValueError, UnicodeError):
            return web.json_response({"error": "invalid_json"}, status=400)
        if payload != {"version": 1, "guild": "Hyped"} or type(payload.get("version")) is not int:
            return web.json_response({"error": "invalid_request"}, status=400)
        if lock.locked() or time.monotonic() - last_started < 15:
            return web.json_response({"error": "wait_15_seconds_before_refreshing"}, status=429)
        async with lock:
            last_started = time.monotonic()
            try:
                return web.json_response(await create_export(bot))
            except (RankExportError, sqlite3.Error):
                return web.json_response({"error": "discord_membership_unavailable"}, status=503)
    app.router.add_post("/hyped/rank-roster", rank_roster)


async def setup(bot):
    await bot.add_cog(GuildRankSync(bot))
