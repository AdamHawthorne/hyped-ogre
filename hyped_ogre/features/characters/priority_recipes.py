"""Curated high-value Forever + Classic recipes and verified guild ownership.

Order is a raid/PvP/utility shortlist, not a universal BiS ranking.
Forever examples follow the 2026-11-19 Wowhead profession overview:
https://www.wowhead.com/forever/guide/professions/overview
Classic crafts follow Wowhead Classic endgame crafting guides.

A recipe in the catalogue is NEVER proof that a member has learned it.
Only Blizzard's known_recipes relation establishes a positive result.
"""
import re
import sqlite3
from contextlib import closing
from urllib.parse import quote

PRIORITIES = {
    "Alchemy": (
        "Transmute: Arcanite", "Flask of the Titans",
        "Flask of Supreme Power", "Flask of Distilled Wisdom",
        "Elixir of the Mongoose", "Greater Arcane Elixir",
        "Mageblood Potion", "Flask of Natural Accuracy",
        "Flask of Natural Aggression", "Flask of Natural Precision",
        "Flask of Natural Swiftness", "Transmute: Legionite",
    ),
    "Blacksmithing": (
        "Lionheart Helm", "Stronghold Gauntlets", "Titanic Leggings",
        "Arcanite Reaper", "Nightfall", "Sulfuron Hammer",
        "Enriched Thorium Breastplate", "Enriched Thorium Helm",
        "Enriched Thorium Leggings", "Master Forge",
        "Crusader's Boots",
    ),
    "Enchanting": (
        "Enchant Weapon - Crusader", "Enchant Weapon - Spell Power",
        "Enchant Weapon - Healing Power", "Enchant Weapon - Agility",
        "Enchant 2H Weapon - Agility", "Enchant Chest - Greater Stats",
        "Enchant Chest - Major Health", "Enchant Gloves - Superior Strength",
        "Enchant 2H Weapon - Mighty Spell Power",
        "Enchant 2H Weapon - Mighty Healing Power",
        "Enchant Gloves - Natural Power",
    ),
    "Engineering": (
        "Goblin Sapper Charge", "Arcane Bomb", "Dark Iron Bomb",
        "Thorium Grenade", "Dense Dynamite", "EZ-Thro Dark Bomb",
        "EZ-Thro Mana Bomb", "Satchel of Dark Iron Bombs",
        "SAF-T Nitro Boosts", "Swiftstrike Shot", "Swiftfeather Arrow",
        "Anarchist's Workbench",
    ),
    "Leatherworking": (
        "Devilsaur Gauntlets", "Devilsaur Leggings", "Hide of the Wild",
        "Onyxia Scale Cloak", "Corehound Belt", "Chromatic Cloak",
        "Black Dragonscale Helm", "Red Dragonscale Leggings",
        "Wild Leather Armor Kit", "Sewing Machine",
    ),
    "Tailoring": (
        "Mooncloth", "Robe of the Archmage", "Truefaith Vestments",
        "Robe of the Void", "Bloodvine Vest", "Bottomless Bag",
        "Bottomless Reagent Bag", "Robes of Fiery Devastation",
        "Felblood Soul Bag", "Loom",
    ),
    "Cooking": (
        "Swiftstrike Steak", "Iron Oven", "Sage's Tea",
        "Plated Armorfish", "Raptor Rouladen", "Savory Stag Sliders",
        "Steaming Stag Steak", "Prehistoric Pulled Raptor",
        "Cookie's Feast", "Savory Turtle Stew",
    ),
    "First Aid": (
        "Major Healing Potion", "Superior Healing Potion",
        "Powerful Poultice", "Surgical Tourniquet",
        "Potent Anti-Venom", "Plague Doctor's Laboratory",
        "Greater Healing Potion", "Toxin Study",
        "Clever Poultice", "Leather Tourniquet",
    ),
    "Fishing": (
        "Fish Bowl", "Fishing Rack", "Fishing Hut",
    ),
}


def normalized(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name).casefold()).strip()


def audit(path, profession):
    """Return priority recipes with real verified learners or unknowns.

    'not_recorded' means no confirmed owner in current records, never that
    nobody can craft it. 'not_indexed' means missing from local catalogue.
    Recipe variants must match precisely within the requested profession.
    """
    if profession not in PRIORITIES:
        return []
    with closing(sqlite3.connect(path, timeout=20)) as con:
        con.row_factory = sqlite3.Row
        indexed = con.execute(
            "SELECT recipe_id,name FROM craft_catalog WHERE profession=?",
            (profession,),
        ).fetchall()
        # Build by strict normalised name, not fuzzy suggestions.
        by_name = {}
        for recipe in indexed:
            by_name.setdefault(normalized(recipe["name"]), []).append(
                recipe["recipe_id"]
            )
        result = []
        for recipe_name in PRIORITIES[profession]:
            ids = by_name.get(normalized(recipe_name), [])
            if not ids:
                result.append({
                    "name": recipe_name, "status": "not_indexed",
                    "members": [],
                })
                continue
            placeholders = ",".join("?" for _ in ids)
            rows = con.execute(
                "SELECT DISTINCT c.discord_id,c.name AS character_name "
                "FROM craft_known k "
                "JOIN characters c ON c.character_id=k.character_id "
                "LEFT JOIN character_board_overrides h "
                "ON h.character_id=c.character_id "
                "WHERE k.profession=? AND k.recipe_id IN ("
                + placeholders + ") "
                "AND c.roster_visible=1 AND COALESCE(h.hidden,0)=0 "
                "ORDER BY LOWER(c.name) LIMIT 8",
                [profession, *ids],
            ).fetchall()
            members = []
            seen = set()
            for row in rows:
                user_id = row["discord_id"]
                if (type(user_id) is not int or user_id <= 0
                        or user_id in seen):
                    continue
                seen.add(user_id)
                members.append({
                    "discord_id": user_id,
                    "character_name": row["character_name"],
                })
            result.append({
                "name": recipe_name,
                "status": "confirmed" if members else "not_recorded",
                "members": members,
            })
    return result


def format_audit(rows, profession, *, max_members=3):
    """Safe Discord text below the message size limit."""
    lines = [f"**{profession}: priority recipes**"]
    lines.append("-# Confirmed = Blizzard learned recipe. No record ≠ nobody knows it.")
    for number, row in enumerate(rows, 1):
        query = quote(row["name"], safe="")
        link = f"[{row['name']}](<https://www.wowhead.com/forever/search?q={query}>)"
        if row["status"] == "confirmed":
            users = ", ".join(
                f"<@{m['discord_id']}>" for m in row["members"][:max_members]
            )
            more = len(row["members"]) - max_members
            owner = users + (f" (+{more} more)" if more > 0 else "")
        elif row["status"] == "not_indexed":
            owner = "Not indexed by Ogre yet"
        else:
            owner = "No verified guild crafter recorded"
        lines.append(f"{number}. {link} · {owner}")
    return "\n".join(lines)
