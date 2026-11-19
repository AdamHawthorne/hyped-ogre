"""Private-pilot roster using the LFG board's Components V2 card style.

One card per Discord account, with its main and alts displayed together.
All mutations flow through existing shared character commands/database.
"""
import discord

from cogs.emojis import markup
from . import roster

ACCENT = 0xA87535
CARDS_PER_MESSAGE = 7  # LFG's conservative Components V2 capacity
HEADER_IMAGE = roster.ROSTER_IMAGE


def groups(characters):
    """Group all roster-visible characters by immutable Discord ID."""
    members = {}
    for record in characters:
        did = int(record["discord_id"])
        members.setdefault(did, []).append(record)
    result = []
    for did, records in members.items():
        ordered = sorted(
            records,
            key=lambda row: (not bool(row["is_main"]),
                             roster.roster_sort_key(row)),
        )
        result.append((did, ordered))
    result.sort(key=lambda group: (
        roster.roster_sort_key(group[1][0]), group[0],
    ))
    return result


def chunks(grouped):
    return [grouped[i:i + CARDS_PER_MESSAGE]
            for i in range(0, len(grouped), CARDS_PER_MESSAGE)] or [[]]


def safe(text):
    return discord.utils.escape_mentions(
        discord.utils.escape_markdown(str(text or ""))
    )


def character_line(record, *, is_main):
    klass = record.get("class_name") or "Unknown"
    spec = record.get("spec") or ""
    icon = markup(klass) or ""
    level = record.get("level")
    level_text = f"Lv {level}" if isinstance(level, int) and 1 <= level <= 60 else "Lv ?"
    name = safe(record.get("name"))
    details = f"{safe(spec)} {safe(klass)}".strip()
    if is_main:
        return f"{icon} **{name}** · {level_text} · {details}".strip()
    # Discord's small muted text is the desired grey alt hierarchy.
    return f"-# {icon} {name} · {level_text} · {details}".strip()


def member_text(discord_id, records):
    main = next((c for c in records if c["is_main"]), None)
    lines = [f"**<@{discord_id}>**"]
    if main is not None:
        lines.append(character_line(main, is_main=True))
    else:
        lines.append("-# No main selected")
    for record in records:
        if record is not main:
            lines.append(character_line(record, is_main=False))
    return "\n".join(lines)


def avatar(guild, user_id):
    member = guild.get_member(user_id) if guild else None
    return str(member.display_avatar.url) if member else HEADER_IMAGE


class CharacterBoardV2(discord.ui.LayoutView):
    """Same V2 Section + Thumbnail + Container presentation as the LFG board."""

    def __init__(self, cog, channel_id, guild=None, *, grouped=(),
                 continuation=False):
        super().__init__(timeout=None)
        self.cog, self.channel_id = cog, channel_id
        if not continuation:
            header = discord.ui.Section(
                discord.ui.TextDisplay(
                    "## <Hyped> Characters\n"
                    "**Guild roster · Main characters and alts**\n"
                    "-# Private V2 pilot · levels: Blizzard when available, otherwise WoWSims"
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
        for discord_id, records in grouped:
            container = discord.ui.Container(accent_color=ACCENT)
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay(member_text(discord_id, records)),
                accessory=discord.ui.Thumbnail(
                    avatar(guild, discord_id),
                    description="Guild member Discord avatar",
                ),
            ))
            self.add_item(container)
        if not grouped:
            self.add_item(discord.ui.TextDisplay(
                "-# No characters registered yet." if not continuation
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
