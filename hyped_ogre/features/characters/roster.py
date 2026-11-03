"""Compact role-grouped roster embeds for main characters only."""

import sqlite3

import discord
from cogs import raid_signup as raid
from cogs.emojis import markup
from hyped_ogre.features.raids.schedule import signup_role_group
from . import equipment, items

ROSTER_IMAGE = "https://i.vgy.me/asI88C.png"

def rows(path):
    with raid.database(path) as con:
        con.row_factory = sqlite3.Row
        characters = [dict(row) for row in con.execute(
            "SELECT character_id,discord_id,name,class_name,spec,role,is_main,off_spec,off_role "
            "FROM characters ORDER BY is_main DESC,LOWER(name),character_id")]
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
    for character in characters:
        character["attendance"] = (f"{100 * credits.get(character['discord_id'], 0) / len(window):.0f}%"
                                   if window else "N/A")
        export = gear.get(character["character_id"])
        parsed = equipment.parse_export(export["raw_text"]) if export else None
        # A saved WoWSims export contains a snapshot, not a live character level.
        level = parsed.get("level") if parsed else None
        character["level"] = (level if isinstance(level, int)
                              and not isinstance(level, bool)
                              and 1 <= level <= 60 else None)
        average, known, equipped = equipment.average_item_level(parsed, catalog)
        character["ilvl"] = (f"{average:.1f}" if average is not None and known == equipped else "N/A")
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


def render(characters):
    """One embed normally; additional visible messages only at Discord limits.

    Inline columns are client-controlled and may stack on mobile. Build each
    three-column block from exactly the same rows; never truncate a roster.
    """
    def new_embed(first):
        result = discord.Embed(title="<Hyped> Roster" if first else "<Hyped> Roster (continued)",
                               description="Ogre keep track. You add or update.\n\u200b" if first else None,
                               colour=0xB84A3A)
        if first:
            result.set_thumbnail(url=ROSTER_IMAGE)
        return result

    embeds = [new_embed(True)]
    roles = ("Tanks", "Healers", "Melee", "Ranged")
    for label in roles:
        members = sorted(
            (r for r in characters if r["is_main"] and
             signup_role_group(r.get("class_name"), r.get("spec"), r.get("role")) == label),
            key=roster_sort_key,
        )
        blocks, block, length = [], [], 0
        for row in members:
            name = f"{markup(row['class_name'])} {safe_name(row['name'])} / <@{row['discord_id']}>"
            # 1024 is the per-field value limit, including emoji and mention markup.
            if block and (length + len(name) + 1 > 1000 or len(block) >= 10):
                blocks.append(block)
                block, length = [], 0
            level = row.get("level")
            level_text = (str(level) if isinstance(level, int)
                          and not isinstance(level, bool)
                          and 1 <= level <= 60 else "N/A")
            block.append((name, row["attendance"], level_text))
            length += len(name) + 1
        if block:
            blocks.append(block)
        if not blocks:
            blocks = [[("No mains registered", "N/A", "N/A")]]
        for index, block in enumerate(blocks):
            heading = f"{label} ({len(members)})" + (" · continued" if index else "")
            values = ["\n".join(row[col] for row in block) for col in range(3)]
            names = (heading, "Attendance", "Lvl")
            addition = sum(map(len, values)) + sum(map(len, names))
            embed = embeds[-1]
            if len(embed.fields) + 3 > 25 or len(embed) + addition > 5900:
                embed = new_embed(False)
                embeds.append(embed)
            for name, value in zip(names, values):
                embed.add_field(name=name, value=value, inline=True)
    return embeds
