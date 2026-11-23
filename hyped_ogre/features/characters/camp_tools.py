"""Forever's tier-2 campsite tools and independently verified guild learners.

Known recipe ownership is sourced only from Blizzard's per-character
known_recipes records, never from skill, character inventories, or blueprint
item ownership. A blueprint is consumed to *learn* a craft, so do not treat
having its item as proof that the player knows the recipe.
"""
import re
import sqlite3
from contextlib import closing
from urllib.parse import quote

# Names confirmed against Blizzard's 2026 camping guide/Wowhead Forever.
# Recipe books and completed camp items are distinct objects. Keep the
# concrete Spinning Wheel blueprint URL supplied by the guild leader.
TIER_TWO = {
    "Alchemy": "Fermenter",
    "Blacksmithing": "Anvil",
    "Enchanting": "Arcane Salvager",
    "Engineering": "Repair Bot",
    "Leatherworking": "Tanning Rack",
    "Tailoring": "Spinning Wheel",
    "Herbalism": "Greenhouse",
    "Mining": "Rock Garden",
    "Skinning": "Field Guide",
}
BLUEPRINT_URLS = {
    "Tailoring": (
        "https://www.wowhead.com/forever/item=273099/blueprint-spinning-wheel"
    ),
}


def link(profession):
    name = TIER_TWO.get(profession)
    if not name:
        return None
    url = BLUEPRINT_URLS.get(profession)
    if not url:
        # Search for the blueprint rather than inventing item identifiers.
        url = (
            "https://www.wowhead.com/forever/search?q="
            + quote("Blueprint: " + name, safe="")
        )
    return f"[{name}](<{url}>)"


def _normalise(label):
    if not label:
        return ""
    text = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
    for prefix in (
        "blueprint ", "recipe ", "pattern ", "schematic ", "plans ",
        "craft a ", "craft ", "create ", "build ", "place ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip()


def owners(path):
    """Get verified learners of one tier-2 campsite tool per profession.

    Require *known* recipe IDs with matching profession and recipe name,
    exclude hidden/LFG-only characters, and never infer known status merely
    from being a skill-140+ crafter. A catalog entry isn't proof by itself.
    """
    with closing(sqlite3.connect(path, timeout=20)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT k.profession, r.name AS recipe_name, c.discord_id,
                   c.name AS character_name, k.recipe_id
            FROM craft_known k
            JOIN craft_catalog r ON r.recipe_id=k.recipe_id
            JOIN characters c ON c.character_id=k.character_id
            LEFT JOIN character_board_overrides h
              ON h.character_id=c.character_id
            WHERE c.roster_visible=1 AND COALESCE(h.hidden,0)=0
              AND k.profession IN (?,?,?,?,?,?,?,?,?)
            ORDER BY LOWER(c.name),k.recipe_id
        """, tuple(TIER_TWO)).fetchall()
    result = {profession: [] for profession in TIER_TWO}
    seen = {profession: set() for profession in TIER_TWO}
    for row in rows:
        prof = row["profession"]
        if _normalise(row["recipe_name"]) != _normalise(TIER_TWO[prof]):
            continue
        discord_id = row["discord_id"]
        if type(discord_id) is not int or discord_id <= 0:
            continue
        # A Discord account's one or more chars shouldn't produce duplicate
        # mentions. Keep one character for evidence and future drilldown.
        if discord_id in seen[prof]:
            continue
        seen[prof].add(discord_id)
        result[prof].append({
            "discord_id": discord_id,
            "character_name": row["character_name"],
            "recipe_id": row["recipe_id"],
        })
    return result


def heading_suffix(profession, ownership, *, max_owners=3):
    """Compact markdown on the heading line, without assuming ownership."""
    target = link(profession)
    if target is None:
        return ""
    players = ownership.get(profession, [])
    if not players:
        return f" · {target} · not verified"
    tags = ", ".join(
        f"<@{player['discord_id']}>" for player in players[:max_owners]
    )
    hidden = len(players) - max_owners
    if hidden > 0:
        tags += f" +{hidden} more"
    return f" · {target} · {tags}"
