"""Compact role-grouped roster embeds for main characters only."""

import math
import sqlite3

import discord
from cogs import raid_signup as raid
from cogs.emojis import markup
from hyped_ogre.features.raids.schedule import signup_role_group
from . import armory, equipment, items

ROSTER_IMAGE = "https://i.vgy.me/asI88C.png"
ROSTER_COLOUR = 0xA87535  # Muted gold, shared with other Ogre panels.

# Short enough to keep two primary professions aligned on one Discord row.
PROFESSION_SHORT_NAMES = {
    "alchemy": "Alch",
    "blacksmithing": "BS",
    "enchanting": "Ench",
    "engineering": "Eng",
    "herbalism": "Herb",
    "inscription": "Insc",
    "jewelcrafting": "JC",
    "leatherworking": "LW",
    "mining": "Min",
    "skinning": "Skin",
    "tailoring": "Tail",
}


def profession_text(profile):
    """Two latest exported primary professions; never invent skill levels."""
    if not profile:
        return "N/A"
    details = profile.get("profession_details") or ()
    labels = []
    for entry in details[:2]:
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        short = PROFESSION_SHORT_NAMES.get(name.casefold(), name[:20])
        skill = entry.get("level")
        label = f"{short}: {skill}" if (
            isinstance(skill, int) and not isinstance(skill, bool)
            and 1 <= skill <= 999
        ) else short
        labels.append(discord.utils.escape_mentions(discord.utils.escape_markdown(label)))
    return " / ".join(labels) if labels else "N/A"


def rows(path):
    with raid.database(path) as con:
        con.row_factory = sqlite3.Row
        characters = [dict(row) for row in con.execute(
            "SELECT c.character_id,c.discord_id,c.name,c.class_name,c.spec,c.role,"
            "c.is_main,c.off_spec,c.off_role,"
            "COALESCE(b.display_name,'') AS board_name,"
            "COALESCE(b.hidden,0) AS board_hidden "
            "FROM characters c LEFT JOIN character_board_overrides b "
            "ON b.character_id=c.character_id "
            "WHERE c.roster_visible=1 ORDER BY c.is_main DESC,LOWER(c.name),c.character_id")]
        window = [r[0] for r in con.execute(
            "SELECT raid_id FROM raids WHERE archived_at IS NOT NULL ORDER BY raid_at DESC LIMIT 4")]
        credits = {}
        if window:
            for row in con.execute(
                "SELECT discord_id,COUNT(DISTINCT raid_id) FROM attendance WHERE raid_id IN ("
                + ",".join("?" for _ in window) + ") AND status IN ('Attended','Late','AFK') GROUP BY discord_id",
                window):
                credits[row[0]] = row[1]
    armory_levels = armory.cached(path)
    # When Blizzard omits iLvl, use only a member's own latest WoWSims
    # export if every equipped item has verified cached level metadata.
    exports = {entry["character_id"]: entry
               for entry in equipment.latest_profiles(path)}
    catalog = items.cached_catalog(path) if exports else {}
    for character in characters:
        character["attendance"] = (f"{100 * credits.get(character['discord_id'], 0) / len(window):.0f}%"
                                   if window else "N/A")
        # Blizzard takes precedence for all public stats. A complete,
        # member-submitted WoWSims equipment snapshot may fill only iLvl.
        live = armory_levels.get(character["character_id"], {})
        if live.get("name") != character["name"]:
            live = {}
        live_level = live.get("level")
        valid_live = type(live_level) is int and 1 <= live_level <= 60
        character["level"] = live_level if valid_live else None
        character["level_source"] = "Blizzard" if valid_live else None

        armory_ilvl = live.get("ilvl")
        if type(armory_ilvl) in (int, float) and 1 <= armory_ilvl <= 250:
            character["ilvl"] = (
                str(int(armory_ilvl)) if float(armory_ilvl).is_integer()
                else f"{armory_ilvl:.1f}"
            )
        else:
            character["ilvl"] = "N/A"
        character["ilvl_source"] = "Blizzard" if character["ilvl"] != "N/A" else None
        if character["ilvl_source"] is None and character["character_id"] in exports:
            parsed = equipment.parse_export(
                exports[character["character_id"]]["raw_text"]
            )
            average, known, equipped = equipment.average_item_level(parsed, catalog)
            if (average is not None and equipped > 0 and known == equipped
                    and 1 <= average <= 250):
                character["ilvl"] = display_ilvl(average)
                character["ilvl_source"] = "WoWSims"
        character["professions"] = profession_text(live)
        character["professions_source"] = (
            "Blizzard" if character["professions"] != "N/A" else None
        )

    return characters


def safe_name(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def roster_sort_key(record):
    """Highest saved level, then attendance, then name; unknown values last."""
    level = record.get("level")
    if not (isinstance(level, int) and not isinstance(level, bool)
            and 1 <= level <= 60):
        level = -1
    attendance = record.get("attendance")
    percent = -1.0
    if isinstance(attendance, str) and attendance.endswith("%"):
        try:
            percent = float(attendance[:-1])
        except ValueError:
            pass
    if not 0 <= percent <= 100:
        percent = -1.0
    return (-level, -percent, record["name"].casefold(), record["character_id"])


def display_ilvl(value):
    """Use only plausible average item levels, never character levels."""
    if isinstance(value, bool):
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "N/A"
    if not math.isfinite(number) or not 1 <= number <= 250:
        return "N/A"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def v1_class_ilvl_sort_key(record):
    """Pin Big Snore first; otherwise class then highest average iLvl.

    Missing or invalid iLvls appear below all known values within their
    own class. Ties sort by character name for a stable roster. Keep the
    older level/attendance sort key for other character-board consumers.
    """
    klass = str(record.get("class_name") or "").casefold()
    average = display_ilvl(record.get("ilvl"))
    ilvl = float(average) if average != "N/A" else -1.0
    # Guild leader's character stays first within its role regardless
    # of class or item level. Other characters retain class/iLvl sorting.
    pin_big_snore = str(record.get("name") or "").strip().casefold() == "big snore"
    return (
        0 if pin_big_snore else 1,
        klass, -ilvl, str(record.get("name") or "").casefold(),
        record["character_id"],
    )


# Keep all three Discord columns aligned without visibly printing N/A.
EMPTY_ROW_CELL = "\u2800"

def render(characters):
    """Compact main-only V1 board with consistent links across every role.

    A Discord embed field holds at most 1024 characters. When any role would
    exceed that with profile links, drop *all* links so one column never looks
    different from the others. Keep splitting only when the plain roster
    itself exceeds Discord's field or embed limits.
    """
    visible = [row for row in characters if row["is_main"] and not row.get("board_hidden")]
    total_mains = len(visible)
    roles = ("Tanks", "Healers", "Melee", "Ranged")
    grouped = {
        label: sorted(
            (r for r in visible if signup_role_group(
                r.get("class_name"), r.get("spec"), r.get("role")
            ) == label),
            key=v1_class_ilvl_sort_key,
        )
        for label in roles
    }

    def new_embed(first):
        result = discord.Embed(
            title=None if first else "Characters (continued)",
            colour=ROSTER_COLOUR,
        )
        if first:
            result.set_author(name=f"Characters ({total_mains})", icon_url=ROSTER_IMAGE)
        return result

    def lines_for_role(members, *, linked):
        result = []
        for row in members:
            name = safe_name(row.get("board_name") or row["name"])
            if linked:
                name = (
                    f"[{name}]"
                    f"(https://discord.com/users/{int(row['discord_id'])})"
                )
            level = row.get("level")
            level_text = str(level) if type(level) is int and 1 <= level <= 60 else ""
            ilvl_text = display_ilvl(row.get("ilvl"))
            if ilvl_text == "N/A":
                level_and_ilvl = level_text
            else:
                level_and_ilvl = f"{level_text} ({ilvl_text})" if level_text else f"({ilvl_text})"
            professions = row.get("professions")
            if not professions or professions == "N/A":
                professions = EMPTY_ROW_CELL
            result.append((
                f"{markup(row['class_name'])} {name}",
                level_and_ilvl or EMPTY_ROW_CELL,
                professions,
            ))
        return result

    # Preflight *every* role. One role exceeding the linked field limit
    # switches the entire board, including Tanks and Healers, to plain names.
    linked = True
    for members in grouped.values():
        entries = lines_for_role(members, linked=True)
        if any(
            len("\n".join(entry[column] for entry in entries)) > 1024
            for column in range(3)
        ):
            linked = False
            break

    embeds = [new_embed(True)]
    for label, members in grouped.items():
        entries = lines_for_role(members, linked=linked)
        blocks, block = [], []
        column_lengths = [0, 0, 0]
        for entry in entries:
            next_lengths = [
                column_lengths[col] + len(entry[col]) + int(bool(block))
                for col in range(3)
            ]
            if block and any(n > 1024 for n in next_lengths):
                blocks.append(block)
                block = []
                column_lengths = [0, 0, 0]
                next_lengths = [len(entry[col]) for col in range(3)]
            block.append(entry)
            column_lengths = next_lengths
        if block:
            blocks.append(block)
        if not blocks:
            blocks = [[("No mains registered", EMPTY_ROW_CELL, EMPTY_ROW_CELL)]]
        for index, block in enumerate(blocks):
            heading = f"{label} ({len(members)})" + (" · continued" if index else "")
            values = ["\n".join(row[col] for row in block) for col in range(3)]
            names = (heading, "Level (iLvl)", "Professions")
            addition = sum(map(len, values)) + sum(map(len, names))
            embed = embeds[-1]
            if len(embed.fields) + 3 > 25 or len(embed) + addition > 5900:
                embed = new_embed(False)
                embeds.append(embed)
            for name, value in zip(names, values):
                embed.add_field(name=name, value=value, inline=True)

    return embeds
