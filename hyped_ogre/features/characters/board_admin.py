"""Leadership display controls for the public Characters board only."""

import asyncio
import logging
import os
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID
from . import board_overrides

LOG = logging.getLogger(__name__)


def allowed(interaction):
    if interaction.guild_id != GUILD_ID or interaction.guild is None:
        return False
    owner_id = int(os.getenv("OWNER_ID") or 0)
    return (
        interaction.user.id in (owner_id, interaction.guild.owner_id)
        or any(role.id == LEADERSHIP_ROLE_ID for role in getattr(interaction.user, "roles", ()))
    )


async def choose_character(interaction: discord.Interaction, current: str):
    if not allowed(interaction):
        return []
    try:
        def query():
            with raid.database(raid.RAID_DB) as con:
                return board_overrides.choices(con, current)
        records = await asyncio.to_thread(query)
    except sqlite3.Error:
        return []
    output = []
    for character_id, name, klass, displayed, hidden in records:
        label = f"{name} ({klass}, ID {character_id})"
        if displayed:
            label += f" as {displayed}"
        if hidden:
            label += " [hidden]"
        output.append(app_commands.Choice(name=label[:100], value=str(character_id)))
    return output


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RosterAdmin(commands.GroupCog, group_name="roster",
                  group_description="Manage public Characters board names and visibility"):
    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction):
        if allowed(interaction):
            return True
        await interaction.response.send_message(
            "Only Hyped Leadership can manage roster display.",
            ephemeral=True,
        )
        return False

    async def cog_app_command_error(self, interaction, error):
        LOG.error("Roster display command failed", exc_info=getattr(error, "original", error))
        message = "Ogre couldn't update the board. Check the logs; registered characters are unchanged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @staticmethod
    def apply_change(operation, *args):
        with raid.database(raid.RAID_DB) as con:
            return operation(con, *args)

    async def refresh_public(self):
        hub = self.bot.get_cog("CharacterHub")
        if hub is None:
            return False
        refreshed = True
        try:
            async with hub.lock:
                panels = await asyncio.to_thread(hub.panels)
                for channel_id, message_id, page in panels:
                    try:
                        await hub.refresh(channel_id, message_id, page)
                    except (ValueError, sqlite3.Error, discord.HTTPException):
                        LOG.warning("Roster display saved, board %s refresh delayed.", channel_id)
                        refreshed = False
        except sqlite3.Error:
            return False
        return refreshed

    async def change(self, interaction, operation, character_id, *args, success):
        await interaction.response.defer(ephemeral=True)
        try:
            number = int(character_id)
            result = await asyncio.to_thread(self.apply_change, operation, number, *args)
        except (TypeError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except sqlite3.Error:
            LOG.exception("Could not save Characters display override")
            await interaction.followup.send(
                "Ogre couldn't save that roster setting. No character was edited.",
                ephemeral=True,
            )
            return
        label = success(result)
        refreshed = await self.refresh_public()
        if not refreshed:
            label += " Saved, but the board could not refresh yet. Ogre will retry automatically."
        # Hiding a main must also remove it from the website-facing Sheet.
        # Never make the user wait for the next raid sync to protect privacy.
        try:
            from hyped_ogre.integrations import website_roster
            await asyncio.to_thread(website_roster.sync_once, raid.RAID_DB)
        except Exception:
            LOG.exception("Website roster sync failed after board override")
            label += " Website roster Sheet refresh delayed; check before publishing."
        await interaction.followup.send(
            label, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="name", description="Change a character's name only on the public board")
    @app_commands.describe(character="Select a registered main", display_name="New board display name (40 characters maximum)")
    @app_commands.autocomplete(character=choose_character)
    async def board_name(self, interaction: discord.Interaction, character: str, display_name: str):
        await self.change(
            interaction, board_overrides.rename, character, display_name,
            success=lambda result: f"Roster now shows {result[0]} as {result[1]}. Original name unchanged.",
        )

    @app_commands.command(name="reset-name", description="Restore a character's registered name on the public board")
    @app_commands.autocomplete(character=choose_character)
    async def board_reset_name(self, interaction: discord.Interaction, character: str):
        await self.change(
            interaction, board_overrides.reset_name, character,
            success=lambda result: f"Roster now uses the registered name {result}.",
        )

    @app_commands.command(name="hide", description="Hide a main from the public board without removing registration")
    @app_commands.autocomplete(character=choose_character)
    async def board_hide(self, interaction: discord.Interaction, character: str):
        await self.change(
            interaction, board_overrides.set_hidden, character, True,
            success=lambda result: f"{result} hidden from public Characters. Raid and classroom access unchanged.",
        )

    @app_commands.command(name="show", description="Show a previously hidden main on the public board")
    @app_commands.autocomplete(character=choose_character)
    async def board_show(self, interaction: discord.Interaction, character: str):
        await self.change(
            interaction, board_overrides.set_hidden, character, False,
            success=lambda result: f"{result} restored to public Characters.",
        )

    @app_commands.command(name="overrides", description="List renamed or hidden mains privately")
    async def board_overrides_list(self, interaction: discord.Interaction):
        def query():
            with raid.database(raid.RAID_DB) as con:
                return board_overrides.changed(con)
        await interaction.response.defer(ephemeral=True)
        try:
            records = await asyncio.to_thread(query)
        except sqlite3.Error:
            await interaction.followup.send("Ogre couldn't read roster display settings.", ephemeral=True)
            return
        if not records:
            text = "No character display names or visibility settings have been overridden."
        else:
            lines = ["**Characters display overrides**"]
            for character_id, name, displayed, hidden in records:
                detail = "hidden" if hidden else "visible"
                if displayed:
                    detail += f"; shown as {displayed}"
                lines.append(f"ID {character_id}: {name} ({detail})")
            text = "\n".join(lines)
        await interaction.followup.send(
            text[:1900], ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(RosterAdmin(bot))
