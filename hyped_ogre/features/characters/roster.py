"""Compact role-grouped roster embeds with no hidden/paginated characters."""

import sqlite3

import discord
from cogs import raid_signup as raid
from cogs.emojis import markup
from . import equipment, items

FOOTER = ("Attendance: per player, last 4 completed raids; bench counts. "
          "iLvl: saved WoWSims gear. N/A: no attendance history or incomplete gear data.")


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
        average, known, equipped = equipment.average_item_level(parsed, catalog)
        character["ilvl"] = (f"{average:.1f}" if average is not None and known == equipped else "N/A")
    return characters


def safe_name(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def render(characters):
    """One embed normally; additional visible messages only at Discord limits.

    Inline columns are client-controlled and may stack on mobile. Build each
    three-column block from exactly the same rows; never truncate a roster.
    """
    def new_embed(first):
        result = discord.Embed(title="<Hyped> Roster" if first else "<Hyped> Roster (continued)",
                               description="Ogre keep track. You add or update." if first else None,
                               colour=0xB84A3A)
        result.set_footer(text=FOOTER)
        return result

    embeds = [new_embed(True)]
    roles = (("Tanks", "Tank"), ("Healers", "Healer"), ("DPS", "DPS"))
    for label, role in roles:
        members = sorted((r for r in characters if
                          (r['role'] if r['role'] in ('Tank', 'Healer') else 'DPS') == role),
                         key=lambda r: (not r['is_main'], r['name'].casefold(), r['character_id']))
        blocks, block, length = [], [], 0
        for row in members:
            name = f"{markup(row['class_name'])} {safe_name(row['name'])} / <@{row['discord_id']}>"
            # 1024 is the per-field value limit, including emoji and mention markup.
            if block and (length + len(name) + 1 > 1000 or len(block) >= 10):
                blocks.append(block)
                block, length = [], 0
            block.append((name, row['attendance'], row['ilvl']))
            length += len(name) + 1
        if block:
            blocks.append(block)
        if not blocks:
            blocks = [[("No registered characters", "N/A", "N/A")]]
        for index, block in enumerate(blocks):
            heading = f"{label} ({len(members)})" + (" · continued" if index else "")
            values = ["\n".join(row[col] for row in block) for col in range(3)]
            names = ("Character", "Attendance", "iLvl")
            addition = len(heading) + 1 + sum(map(len, values)) + sum(map(len, names))
            embed = embeds[-1]
            if len(embed.fields) + 4 > 25 or len(embed) + addition > 5900:
                embed = new_embed(False)
                embeds.append(embed)
            embed.add_field(name=heading, value="\u200b", inline=False)
            for name, value in zip(names, values):
                embed.add_field(name=name, value=value, inline=True)
    return embeds
