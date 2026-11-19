"""Private compact Components V2 roster, matching raid sign-up role sections.

Each roster-visible character occupies exactly one line. Mains are prominent,
alts use Discord's muted typography, and pagination never drops a character.
Edit controls and canonical character identity are shared with the existing
raid, LFG and Google Sheets features.
"""
import discord

from cogs.emojis import markup
from hyped_ogre.features.raids.schedule import signup_role_group
from . import roster

ACCENT = 0xA87535
HEADER_IMAGE = roster.ROSTER_IMAGE
ROLE_ORDER = ("Tanks", "Healers", "Melee", "Ranged")
# Leave room for the V2 header/controls and Discord markdown overhead.
PAGE_CHAR_LIMIT = 3500
SECTION_CHAR_LIMIT = 2850


def groups(characters):
    """Exactly the same four role categories as the raid sign-up feature."""
    by_role = {role: [] for role in ROLE_ORDER}
    for record in characters:
        role = signup_role_group(
            record.get("class_name"), record.get("spec"), record.get("role")
        )
        by_role[role].append(record)
    for role in ROLE_ORDER:
        by_role[role].sort(key=lambda row: (
            not bool(row.get("is_main")),
            roster.roster_sort_key(row),
        ))
    return [(role, by_role[role]) for role in ROLE_ORDER if by_role[role]]


def safe(text):
    return discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(text or ""))
    )


def discord_name(guild, user_id, verified=None):
    # Caller may have checked the Discord API to resolve an uncached member.
    info = (verified or {}).get(user_id)
    if info and info[0] == "departed":
        return f"Discord ID {user_id} (left server)"
    if info and info[0] == "member":
        return f"[{safe(info[1])}](https://discord.com/users/{user_id})"
    member = guild.get_member(user_id) if guild else None
    if member:
        display = safe(
            getattr(member, "display_name", "") or getattr(member, "name", "")
        )
        # A profile link avoids Discord's non-pinging mention cache issue.
        return f"[{display}](https://discord.com/users/{user_id})"
    return f"Discord ID {user_id} (unverified)"


def character_line(record, guild=None, verified=None):
    """One plain line: class, name, level, iLvl, professions, alt and Discord."""
    klass = record.get("class_name") or ""
    class_icon = markup(klass) or ""
    level = record.get("level")
    level_label = str(level) if (
        type(level) is int and 1 <= level <= 60
    ) else "?"
    ilvl = record.get("ilvl")
    ilvl_label = str(ilvl) if ilvl not in (None, "", "N/A") else "?"
    professions = (record.get("professions") or "N/A").replace(" / ", ", ")
    name = safe(record.get("name"))
    is_main = bool(record.get("is_main"))
    label = f"**{name}**" if is_main else name
    lead = class_icon if is_main else f"Alt: {class_icon}"
    user_id = int(record["discord_id"])
    line = (
        f"{lead} {label} / {level_label} / {safe(ilvl_label)} / "
        f"{professions} - {discord_name(guild, user_id, verified)}"
    ).strip()
    # Only alt rows are muted: one line per entry, no player cards/columns.
    return line if is_main else "-# " + line


def _role_blocks(role, members, guild, verified):
    """Split oversized roles without truncating their individual character rows."""
    base = f"**{role} ({len(members)})**"
    heading = base
    lines = []
    size = len(heading)
    for record in members:
        line = character_line(record, guild, verified)
        # Each entry is bounded by the character registration's length,
        # profession labels and Discord's maximum username length.
        if lines and size + len(line) + 1 > SECTION_CHAR_LIMIT:
            yield heading + "\n" + "\n".join(lines)
            heading = base + " · continued"
            lines = []
            size = len(heading)
        lines.append(line)
        size += len(line) + 1
    if lines:
        yield heading + "\n" + "\n".join(lines)


def chunks(grouped, guild=None, verified=None):
    """Pack role blocks into bounded messages; never hide entries behind pages."""
    blocks = [
        block for role, members in grouped
        for block in _role_blocks(role, members, guild, verified)
    ]
    pages = [[]]
    length = 0
    for block in blocks:
        if pages[-1] and length + len(block) + 2 > PAGE_CHAR_LIMIT:
            pages.append([])
            length = 0
        pages[-1].append(block)
        length += len(block) + 2
    return pages


class CharacterBoardV2(discord.ui.LayoutView):
    """One compact role list per message, with persistent owner-checked controls."""

    def __init__(self, cog, channel_id, guild=None, *, grouped=(),
                 continuation=False):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id
        if not continuation:
            header = discord.ui.Section(
                discord.ui.TextDisplay(
                    "## <Hyped> Characters\n"
                    "-# Guild roster · Roles, mains and alts · "
                    "Armory with newer WoWSims overrides"
                ),
                accessory=discord.ui.Thumbnail(
                    HEADER_IMAGE, description="Hyped roster",
                ),
            )
            self.add_item(discord.ui.Container(header, accent_color=ACCENT))
            actions = discord.ui.ActionRow()
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
                actions.add_item(button)
            self.add_item(actions)
        for content in grouped:
            self.add_item(discord.ui.TextDisplay(content))
        if not grouped:
            self.add_item(discord.ui.TextDisplay(
                "-# No registered characters." if not continuation
                else "-# No further registrations."
            ))

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
