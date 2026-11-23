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

# Artisan/advanced campfire blueprints in the 19 Nov 2026 Forever
# Wowhead professions guide, in its "What is New" sections.
# These REPLACE the mid-tier stations. Never infer learned ownership from
# profession skill or possession of an unconsumed blueprint.
GUIDE_URL = "https://www.wowhead.com/forever/guide/professions/overview"
ADVANCED_STATIONS = {
    "Alchemy": "Alchemy Laboratory",
    "Blacksmithing": "Master Forge",
    "Enchanting": "Arcane Forge",
    "Engineering": "Anarchist's Workbench",
    "Leatherworking": "Sewing Machine",
    "Tailoring": "Loom",
    "Herbalism": "Seed Hybridizer",
    "Mining": "Molten Foundry",
    "Skinning": "Trapper's Workbench",
}
# Compatibility alias for external callers; each lookup now means advanced.
TIER_TWO = ADVANCED_STATIONS
BLUEPRINT_URLS = {}


def link(profession):
    name = ADVANCED_STATIONS.get(profession)
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
    """Get verified learners of the advanced campsite blueprint per profession.

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
        """, tuple(ADVANCED_STATIONS)).fetchall()
    result = {profession: [] for profession in ADVANCED_STATIONS}
    seen = {profession: set() for profession in TIER_TWO}
    for row in rows:
        prof = row["profession"]
        if _normalise(row["recipe_name"]) != _normalise(ADVANCED_STATIONS[prof]):
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
    """Render the advanced campfire tool beneath each profession heading."""
    target = link(profession)
    if target is None:
        return ""
    players = ownership.get(profession, [])
    if not players:
        return f"\n-# {target} · Ogre not found anyone with this yet"
    tags = ", ".join(
        f"<@{player['discord_id']}>" for player in players[:max_owners]
    )
    hidden = len(players) - max_owners
    if hidden > 0:
        tags += f" +{hidden} more"
    return f"\n-# {target} · {tags}"
