"""Owner-only character corrections in the private V2 pilot.

All writes use the canonical raid_signup SQLite database. Never erase a
character's raid history or infer Discord ownership from a Blizzard lookup.
"""
import asyncio
import logging
import os
import sqlite3

import discord

from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID

LOG = logging.getLogger(__name__)
PAGE_SIZE = 24


def authorised(interaction, actor_id=None):
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    owner = int(os.getenv("OWNER_ID") or 0)
    return bool(
        getattr(interaction, "guild_id", None) == GUILD_ID
        and getattr(interaction, "channel_id", None) == raid.TEST_CHANNEL_ID
        and user is not None
        and (actor_id is None or user.id == actor_id)
        and (user.id == owner or user.id == getattr(guild, "owner_id", None))
    )


def current(target_id, character_id):
    with raid.database(raid.RAID_DB) as con:
        return con.execute(
            "SELECT character_id,discord_id,name,class_name,spec,is_main "
            "FROM characters WHERE discord_id=? AND character_id=? "
            "AND roster_visible=1", (target_id, character_id)
        ).fetchone()


def entries():
    with raid.database(raid.RAID_DB) as con:
        return con.execute(
            "SELECT character_id,discord_id,name,class_name,spec,is_main "
            "FROM characters WHERE roster_visible=1 "
            "ORDER BY LOWER(name),character_id"
        ).fetchall()


def safe(value):
    return discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(value or ""))
    )


async def audit_members(guild, user_ids):
    """Verify uncached Discord IDs through the API, never assume they've left.

    'departed' only means Discord returned a definitive NotFound response.
    Forbidden/network errors remain unknown and are never auto-cleaned.
    """
    statuses = {}
    semaphore = asyncio.Semaphore(4)

    async def verify(user_id):
        if guild.get_member(user_id) is not None:
            statuses[user_id] = "member"
            return
        async with semaphore:
            try:
                await guild.fetch_member(user_id)
            except discord.NotFound:
                statuses[user_id] = "departed"
            except (discord.Forbidden, discord.HTTPException):
                statuses[user_id] = "unknown"
            else:
                statuses[user_id] = "member"

    await asyncio.gather(*(verify(uid) for uid in set(user_ids)))
    return statuses


async def update_after_change(cog, interaction, target_id, result):
    """Acknowledge the write, then refresh all private views and class access."""
    raid.notify_classrooms(interaction, target_id)
    await interaction.response.send_message(result, ephemeral=True)
    try:
        await raid.refresh_lfg_for_member(interaction, target_id)
    except (sqlite3.Error, discord.HTTPException):
        LOG.warning("Linked LFG refresh delayed for %s", target_id)
    try:
        async with cog.lock:
            saved = next(
                (p for p in cog.v2_panels() if p[0] == raid.TEST_CHANNEL_ID), None
            )
            if saved:
                await cog.refresh_v2(saved[0], saved[1])
    except (ValueError, sqlite3.Error, discord.HTTPException):
        LOG.warning("Private V2 roster refresh delayed following owner edit.")


class LeaderCharacterPicker(discord.ui.View):
    """Paged selector, since a 25-option Discord menu would truncate alts."""

    def __init__(self, cog, actor_id, page=0, *, member_ids=None):
        super().__init__(timeout=300)
        self.cog, self.actor_id = cog, actor_id
        self.member_ids = frozenset(member_ids) if member_ids is not None else None
        all_entries = [
            row for row in entries()
            if self.member_ids is None or row[1] in self.member_ids
        ]
        self.pages = max(1, (len(all_entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(page, self.pages - 1))
        visible = all_entries[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        if visible:
            selector = discord.ui.Select(
                placeholder="Choose a character to correct",
                options=[
                    discord.SelectOption(
                        label=row[2][:100], value=str(row[0]),
                        description=(
                            f"Discord {row[1]} · {row[3]} / {row[4]} · "
                            f"{'Main' if row[5] else 'Alt'}"
                        )[:100],
                    ) for row in visible
                ],
            )

            async def choose(interaction):
                if not authorised(interaction, self.actor_id):
                    await interaction.response.send_message(
                        "Leadership-only private roster controls.", ephemeral=True)
                    return
                selected = next(
                    (row for row in entries() if str(row[0]) == selector.values[0]),
                    None,
                )
                if selected is None:
                    await interaction.response.send_message(
                        "That character is no longer on the roster.", ephemeral=True)
                    return
                await interaction.response.edit_message(
                    content=(f"**{safe(selected[2])}** · "
                             f"Discord ID {selected[1]}"),
                    view=LeaderCharacterActions(
                        self.cog, self.actor_id, selected[1], selected[0],
                    ),
                )

            selector.callback = choose
            self.add_item(selector)
        for label, delta in (("Previous", -1), ("Next", 1)):
            button = discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary,
                disabled=not (0 <= self.page + delta < self.pages),
            )

            async def turn(interaction, direction=delta):
                if not authorised(interaction, self.actor_id):
                    await interaction.response.send_message(
                        "Leadership-only private roster controls.", ephemeral=True)
                    return
                await interaction.response.edit_message(
                    content="Choose a character to edit or remove.",
                    view=LeaderCharacterPicker(
                        self.cog, self.actor_id, self.page + direction,
                        member_ids=self.member_ids,
                    ),
                )

            button.callback = turn
            self.add_item(button)

        if self.member_ids is None:
            audit = discord.ui.Button(
                label="Audit missing members", style=discord.ButtonStyle.primary,
            )

            async def run_audit(interaction):
                if not authorised(interaction, self.actor_id):
                    await interaction.response.send_message(
                        "Leadership-only private roster controls.", ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                saved = entries()
                member_ids = {row[1] for row in saved}
                result = await audit_members(interaction.guild, member_ids)
                departed = {
                    user_id for user_id, status in result.items()
                    if status == "departed"
                }
                unresolved = sum(status == "unknown" for status in result.values())
                still_here = sum(status == "member" for status in result.values())
                if departed:
                    await interaction.followup.send(
                        f"**{len(departed)} confirmed departed Discord accounts** "
                        f"with {sum(row[1] in departed for row in saved)} visible characters. "
                        f"{still_here} accounts are still members; "
                        f"{unresolved} could not be verified.\n"
                        "Select each stale character to remove it safely, "
                        "or delete it if it has no protected history. "
                        "Nothing was automatically changed.",
                        view=LeaderCharacterPicker(
                            self.cog, self.actor_id, member_ids=departed,
                        ),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"No confirmed departed accounts. "
                        f"{still_here} verified members; {unresolved} uncertain. "
                        "Unverified entries are not automatically removed.",
                        ephemeral=True,
                    )

            audit.callback = run_audit
            self.add_item(audit)


class LeaderRename(discord.ui.Modal, title="Correct character name"):
    def __init__(self, cog, actor_id, target_id, character_id, name):
        super().__init__(timeout=300)
        self.cog, self.actor_id = cog, actor_id
        self.target_id, self.character_id = target_id, character_id
        self.name = discord.ui.TextInput(
            label="Full character name", default=name, max_length=40,
            placeholder="First Last",
        )
        self.add_item(self.name)

    async def on_submit(self, interaction):
        if not authorised(interaction, self.actor_id):
            await interaction.response.send_message(
                "Leadership-only private roster controls.", ephemeral=True)
            return
        if current(self.target_id, self.character_id) is None:
            await interaction.response.send_message(
                "Character no longer on the roster.", ephemeral=True)
            return
        try:
            changed = raid.rename_character(
                self.target_id, self.character_id, self.name.value,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't correct name: {exc}", ephemeral=True)
            return
        LOG.info(
            "LEADERSHIP CHARACTER RENAME actor=%s discord=%s character_id=%s",
            self.actor_id, self.target_id, self.character_id,
        )
        await update_after_change(
            self.cog, interaction, self.target_id,
            f"{safe(self.name.value.strip())} renamed in the shared database."
            if changed else "That character already has this name.",
        )


class LeaderConfirm(discord.ui.View):
    def __init__(self, cog, actor_id, target_id, character_id, *, permanent):
        super().__init__(timeout=180)
        self.cog, self.actor_id = cog, actor_id
        self.target_id, self.character_id = target_id, character_id
        self.permanent = permanent

        confirm = discord.ui.Button(
            label="Confirm delete" if permanent else "Confirm roster removal",
            style=discord.ButtonStyle.danger,
        )
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary,
        )

        async def do_confirm(interaction):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            if current(self.target_id, self.character_id) is None:
                await interaction.response.send_message(
                    "Character no longer on the roster.", ephemeral=True)
                return
            try:
                if self.permanent:
                    result = raid.delete_character(
                        self.target_id, self.character_id
                    )
                else:
                    result = raid.remove_character_from_roster(
                        self.target_id, self.character_id
                    )
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Ogre refused removal: {exc}", ephemeral=True)
                return
            LOG.info(
                "LEADERSHIP CHARACTER %s actor=%s discord=%s character_id=%s",
                "DELETE" if self.permanent else "UNLIST",
                self.actor_id, self.target_id, self.character_id,
            )
            await update_after_change(
                self.cog, interaction, self.target_id,
                ("Deleted unused character, including its saved exports."
                 if self.permanent else
                 "Removed from the roster. Raid history, gear and LFG links "
                 "are retained; the member can add it back."),
            )

        async def do_cancel(interaction):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            await interaction.response.edit_message(
                content="No changes saved.",
                view=LeaderCharacterActions(
                    self.cog, self.actor_id, self.target_id, self.character_id,
                ),
            )

        confirm.callback = do_confirm
        cancel.callback = do_cancel
        self.add_item(confirm)
        self.add_item(cancel)


class LeaderCharacterActions(discord.ui.View):
    """Private controls; every action rechecks actor and character identity."""

    def __init__(self, cog, actor_id, target_id, character_id):
        super().__init__(timeout=300)
        self.cog, self.actor_id = cog, actor_id
        self.target_id, self.character_id = target_id, character_id

        def make(label, style, callback):
            button = discord.ui.Button(label=label, style=style)
            button.callback = callback
            self.add_item(button)

        async def rename(interaction):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            row = current(self.target_id, self.character_id)
            if row is None:
                await interaction.response.send_message(
                    "Character no longer on the roster.", ephemeral=True)
                return
            await interaction.response.send_modal(
                LeaderRename(
                    self.cog, self.actor_id, self.target_id,
                    self.character_id, row[2],
                ),
            )

        async def set_main(interaction):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            if current(self.target_id, self.character_id) is None:
                await interaction.response.send_message(
                    "Character no longer on the roster.", ephemeral=True)
                return
            try:
                raid.set_main_character(self.target_id, self.character_id)
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Ogre couldn't change main: {exc}", ephemeral=True)
                return
            await update_after_change(
                self.cog, interaction, self.target_id,
                "Main updated in the shared roster database.",
            )

        async def remove(interaction, permanent=False):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            await interaction.response.edit_message(
                content=(
                    "Delete unused character permanently? Ogre will refuse "
                    "if raid or attendance history or current LFG links exist."
                    if permanent else
                    "Remove this character from the visible roster? The "
                    "registration, raid history and LFG data will be kept. "
                    "Existing raid sign-ups are not cancelled."
                ),
                view=LeaderConfirm(
                    self.cog, self.actor_id, self.target_id,
                    self.character_id, permanent=permanent,
                ),
            )

        async def back(interaction):
            if not authorised(interaction, self.actor_id):
                await interaction.response.send_message(
                    "Leadership-only private roster controls.", ephemeral=True)
                return
            await interaction.response.edit_message(
                content="Choose a registered character to correct.",
                view=LeaderCharacterPicker(self.cog, self.actor_id),
            )

        make("Rename", discord.ButtonStyle.secondary, rename)
        make("Set Main", discord.ButtonStyle.secondary, set_main)

        async def unlist(interaction):
            await remove(interaction, permanent=False)

        async def delete(interaction):
            await remove(interaction, permanent=True)

        make("Remove from roster", discord.ButtonStyle.secondary, unlist)
        make("Delete unused", discord.ButtonStyle.danger, delete)
        make("Back", discord.ButtonStyle.secondary, back)
