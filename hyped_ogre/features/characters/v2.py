"""Private guild roster matching the compact RaidEventPanel V2 structure.

One V2 container with the guild thumbnail and role-grouped TextDisplays,
followed by a row of interactive controls. A second message is created only
if Discord's display limits make a single message impossible.
"""
import re

import discord

from cogs.emojis import markup
from hyped_ogre.features.raids.schedule import signup_role_group
from . import roster

ACCENT = 0xA87535
HEADER_IMAGE = roster.ROSTER_IMAGE
ROLE_ORDER = ("Tanks", "Melee", "Ranged", "Healers")
SECTION_CHAR_LIMIT = 3700
PAGE_CHAR_LIMIT = 14500
MAX_BLOCKS = 8

SPEC_SHORT = {
    "Protection": "Prot",
    "Restoration": "Resto",
    "Discipline": "Disc",
    "Retribution": "Ret",
    "Enhancement": "Enh",
    "Elemental": "Ele",
    "Beast Mastery": "BM",
    "Marksmanship": "MM",
}


def groups(characters):
    """Keep all registered mains and alts in the four raid-signup role groups."""
    by_role = {role: [] for role in ROLE_ORDER}
    for record in characters:
        role = signup_role_group(
            record.get("class_name"), record.get("spec"), record.get("role")
        )
        by_role[role].append(record)
    for role in ROLE_ORDER:
        by_role[role].sort(key=lambda row: (
            not bool(row.get("is_main")), roster.roster_sort_key(row),
        ))
    return [(role, by_role[role]) for role in ROLE_ORDER if by_role[role]]


def safe(text):
    return discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(text or ""))
    )


def discord_name(guild, user_id, verified=None):
    """Retain accurate Discord identity for the existing leadership controls."""
    info = (verified or {}).get(user_id)
    if info and info[0] == "departed":
        return f"Discord ID {user_id} (left server)"
    if info and info[0] == "member":
        return f"[{safe(info[1])}](https://discord.com/users/{user_id})"
    member = guild.get_member(user_id) if guild else None
    if member:
        name = safe(
            getattr(member, "display_name", "") or getattr(member, "name", "")
        )
        return f"[{name}](https://discord.com/users/{user_id})"
    return f"Discord ID {user_id} (unverified)"


def profession_summary(value):
    """Use short, clear profession labels, retaining reported skill values."""
    if not isinstance(value, str) or not value or value == "N/A":
        return "Profs: N/A"
    names = {
        "Min": "Mining", "Herb": "Herbalism", "Eng": "Engineering",
        "Ench": "Enchanting", "Alch": "Alchemy", "BS": "BS",
        "LW": "Leatherworking", "Skin": "Skinning", "Tail": "Tailoring",
        "JC": "Jewelcrafting", "Insc": "Inscription",
    }
    parts = []
    for item in value.split(" / ")[:2]:
        label, sep, level = item.partition(":")
        label = names.get(label.strip(), label.strip())
        if not label:
            continue
        skill = level.strip()
        parts.append(f"{label}({skill})" if sep and skill.isdecimal()
                     else label)
    return " and ".join(parts) if parts else "Profs: N/A"


def character_line(record, guild=None, verified=None):
    """Screenshot layout: icon, name, iLvl/spec/class, crafts, attendance."""
    klass = str(record.get("class_name") or "Unknown")
    spec = str(record.get("spec") or "")
    display_spec = SPEC_SHORT.get(spec, spec)
    name = safe(record.get("name"))
    icon = markup(klass) or ""
    ilvl = record.get("ilvl")
    level_text = str(ilvl) if ilvl not in (None, "", "N/A") else "N/A"
    attendance = record.get("attendance") or "N/A"
    craft = profession_summary(record.get("professions"))
    is_main = bool(record.get("is_main"))
    label = f"**{name}**" if is_main else name
    tag = "" if is_main else "Alt: "
    spec_class = " ".join(p for p in (display_spec, klass) if p)
    line = (f"{tag}{icon} {label} - iLvl {safe(level_text)} "
            f"{safe(spec_class)} - {craft} - Attendance: {safe(attendance)}")
    # Historical departures are an ownership check, not a signal to delete.
    uid = int(record["discord_id"])
    if (verified or {}).get(uid, ("", ""))[0] == "departed":
        line += " (Discord departed)"
    return "-# " + line


def _role_blocks(role, members, guild, verified):
    """Exactly the raid sign-up's small muted heading and character lines."""
    heading = f"-# **{role} ({len(members)})**"
    current = heading
    for member in members:
        line = character_line(member, guild, verified)
        if len(current) + len(line) + 1 > SECTION_CHAR_LIMIT and current != heading:
            yield current
            current = heading + " · continued"
        current += "\n" + line
    if current != heading:
        yield current


def chunks(grouped, guild=None, verified=None):
    """A single page for normal guild size; lossless overflow only when needed."""
    blocks = [
        block for role, members in grouped
        for block in _role_blocks(role, members, guild, verified)
    ]
    pages = [[]]
    total = 0
    for block in blocks:
        if pages[-1] and (total + len(block) > PAGE_CHAR_LIMIT
                          or len(pages[-1]) >= MAX_BLOCKS):
            pages.append([])
            total = 0
        pages[-1].append(block)
        total += len(block)
    return pages


class CharacterBoardV2(discord.ui.LayoutView):
    """One raid-style V2 container with logo, role lists and action buttons."""

    def __init__(self, cog, channel_id, guild=None, *, grouped=(),
                 continuation=False):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id
        elements = []
        if not continuation:
            elements.append(discord.ui.Section(
                discord.ui.TextDisplay(
                    "## <Hyped> Characters\n"
                    "-# Guild roster · Main characters and alts"
                ),
                accessory=discord.ui.Thumbnail(
                    HEADER_IMAGE, description="Hyped guild logo",
                ),
            ))
        for text in grouped:
            elements.append(discord.ui.TextDisplay(text))
        if not grouped:
            elements.append(discord.ui.TextDisplay(
                "-# No characters registered." if not continuation
                else "-# No further characters."
            ))
        self.add_item(discord.ui.Container(*elements, accent_color=ACCENT))
        if not continuation:
            controls = discord.ui.ActionRow()
            definitions = [
                ("Add", discord.ButtonStyle.success,
                 "hyped_characters_v2_add", self.add_character),
                ("Edit", discord.ButtonStyle.secondary,
                 "hyped_characters_v2_manage", self.edit_character),
                ("WoWSims", discord.ButtonStyle.secondary,
                 "hyped_characters_v2_wse", self.wowsims),
                ("Remove / Absent", discord.ButtonStyle.danger,
                 "hyped_characters_v2_remove", self.remove),
                ("Leadership", discord.ButtonStyle.secondary,
                 "hyped_characters_v2_leadership", self.leadership),
            ]
            for label, style, cid, callback in definitions:
                button = discord.ui.Button(
                    label=label, style=style, custom_id=cid,
                )
                button.callback = callback
                controls.add_item(button)
            self.add_item(controls)

    async def interaction_check(self, interaction):
        from hyped_ogre.features.classrooms.config import GUILD_ID
        if interaction.guild_id == GUILD_ID:
            return True
        await interaction.response.send_message(
            "This board belongs to Hyped.", ephemeral=True)
        return False

    async def add_character(self, interaction):
        # Reuse the main/alt database and LFG duplicate-prevention controls.
        from .hub import RosterAddView
        await interaction.response.send_message(
            "Add an existing character or register a new one.",
            view=RosterAddView(
                self.cog, self.channel_id, interaction.user.id
            ), ephemeral=True,
        )

    async def edit_character(self, interaction):
        await self.cog.open_characters(interaction)

    async def wowsims(self, interaction):
        cog = self.cog.bot.get_cog("RaidPrototype")
        if cog is None:
            await interaction.response.send_message(
                "Ogre's WoWSims menu is temporarily unavailable.",
                ephemeral=True)
            return
        await cog.on_gear(interaction)

    async def leadership(self, interaction):
        from .admin_ui import authorised, LeaderCharacterPicker
        if not authorised(interaction):
            await interaction.response.send_message(
                "Leadership corrections are only available to the guild "
                "owner in Ogre's private channel.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Select a character. Rename, change main, remove from the "
            "visible roster or delete an unused registration.",
            view=LeaderCharacterPicker(
                self.cog, interaction.user.id,
            ),
            ephemeral=True,
        )

    async def remove(self, interaction):
        from .absence_ui import AbsenceActions
        await interaction.response.send_message(
            "Choose Remove character to delete unused entries. Characters "
            "with raid history are protected from deletion.",
            view=AbsenceActions(interaction.user.id), ephemeral=True,
        )
