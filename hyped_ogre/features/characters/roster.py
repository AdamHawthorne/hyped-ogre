"""Compact role-grouped roster embeds for main characters only."""

import math
import sqlite3

import discord
from cogs import raid_signup as raid
from cogs.emojis import markup
from hyped_ogre.features.raids.schedule import signup_role_group
from . import armory, equipment, items

ROSTER_IMAGE = "https://i.vgy.me/asI88C.png"

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
            "SELECT character_id,discord_id,name,class_name,spec,role,is_main,off_spec,off_role "
            "FROM characters WHERE roster_visible=1 ORDER BY is_main DESC,LOWER(name),character_id")]
        window = [r[0] for r in con.execute(
            "SELECT raid_id FROM raids WHERE archived_at IS NOT NULL ORDER BY raid_at DESC LIMIT 4")]
        credits = {}
        if window:
            for row in con.execute(
                "SELECT discord_id,COUNT(DISTINCT raid_id) FROM attendance WHERE raid_id IN ("
                + ",".join("?" for _ in window) + ") AND status IN ('Attended','Late','AFK') GROUP BY discord_id",
                window):
                credits[row[0]] = row[1]
    catalog = items.cached_catalog(path)
    gear = {r["character_id"]: r for r in equipment.latest_profiles(path)}
    armory_levels = armory.cached(path)
    for character in characters:
        character["attendance"] = (f"{100 * credits.get(character['discord_id'], 0) / len(window):.0f}%"
                                   if window else "N/A")
        export = gear.get(character["character_id"])
        parsed = equipment.parse_export(export["raw_text"]) if export else None
        # Blizzard Armory takes precedence for level and average item level.
        # Old WoWSims exports must not mask valid Blizzard stats. WoWSims
        # still supplies immediate fallback values when an Armory field is
        # unavailable, plus the latest submitted professions. Neither
        # source changes canonical registration or Discord ownership.
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
        character["professions"] = profession_text(live)
        character["professions_source"] = (
            "Blizzard" if character["professions"] != "N/A" else None
        )

        if parsed:
            wse_level = parsed.get("level")
            if not valid_live and type(wse_level) is int and 1 <= wse_level <= 60:
                character["level"] = wse_level
                character["level_source"] = "WoWSims"
            wse_profs = profession_text(parsed)
            if wse_profs != "N/A":
                character["professions"] = wse_profs
                character["professions_source"] = "WoWSims"
            average, known, equipped = equipment.average_item_level(parsed, catalog)
            if (character["ilvl_source"] is None
                    and average is not None and equipped > 0 and known == equipped):
                character["ilvl"] = f"{average:.1f}"
                character["ilvl_source"] = "WoWSims"
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


def render(characters):
    """One embed normally; additional visible messages only at Discord limits.

    Inline columns are client-controlled and may stack on mobile. Build each
    three-column block from exactly the same rows; never truncate a roster.
    """
    # V1 shows mains only, so the heading must not count hidden alt entries.
    total_mains = sum(1 for record in characters if record["is_main"])

    def new_embed(first):
        # A thumbnail reserves width beside the role fields on desktop.
        # The author icon keeps the guild mark beside the heading without
        # narrowing the character / level / profession columns.
        result = discord.Embed(
            title=None if first else "Characters (continued)",
            colour=0xB84A3A,
        )
        if first:
            result.set_author(name=f"Characters ({total_mains})", icon_url=ROSTER_IMAGE)
        return result

    embeds = [new_embed(True)]
    roles = ("Tanks", "Healers", "Melee", "Ranged")
    for label in roles:
        members = sorted(
            (r for r in characters if r["is_main"] and
             signup_role_group(r.get("class_name"), r.get("spec"), r.get("role")) == label),
            key=v1_class_ilvl_sort_key,
        )
        blocks, block, length = [], [], 0
        for row in members:
            # Discord renders the three inline fields independently. A full
            # Discord username/mention often wraps onto a second line and
            # misaligns the Level and Professions rows. Link the character
            # name to the member's Discord profile instead, retaining a
            # clickable owner without repeating a long display name.
            linked_name = (
                f"[{safe_name(row['name'])}]"
                f"(https://discord.com/users/{int(row['discord_id'])})"
            )
            name = f"{markup(row['class_name'])} {linked_name}"
            # 1024 is the per-field value limit, including emoji and mention markup.
            if block and (length + len(name) + 1 > 1000 or len(block) >= 10):
                blocks.append(block)
                block, length = [], 0
            # Keep character level and average item level side by side,
            # using the Armory cache and saved WoWSims values already in rows().
            # Missing values stay explicit rather than being invented.
            level = row.get("level")
            level_text = (str(level) if type(level) is int
                          and 1 <= level <= 60 else "N/A")
            ilvl_text = display_ilvl(row.get("ilvl"))
            block.append((name, f"{level_text} ({ilvl_text})",
                          row.get("professions") or "N/A"))
            length += len(name) + 1
        if block:
            blocks.append(block)
        if not blocks:
            blocks = [[("No mains registered", "N/A", "N/A")]]
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
