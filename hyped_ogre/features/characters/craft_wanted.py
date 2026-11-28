"""Guild wanted list: real member demand, sourced shortlist and verified supply.

A missing Blizzard known_recipes row means *not confirmed*, not proof a
player has never learned the recipe. Prices are not fabricated.
"""
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from urllib.parse import quote

from . import priority_recipes


def top_ten(path, limit=10):
    with closing(sqlite3.connect(path, timeout=20)) as con:
        con.row_factory = sqlite3.Row
        recipes = [dict(row) for row in con.execute(
            "SELECT recipe_id,profession,name FROM craft_catalog "
            "WHERE name<>'' ORDER BY recipe_id"
        )]
        wishes = con.execute(
            "SELECT recipe_id,COUNT(*) AS requests,COUNT(DISTINCT user_id) AS users "
            "FROM craft_desires WHERE recipe_id IS NOT NULL GROUP BY recipe_id"
        ).fetchall()
        verified = {
            row[0] for row in con.execute(
                "SELECT DISTINCT k.recipe_id FROM craft_known k "
                "JOIN characters c ON c.character_id=k.character_id "
                "LEFT JOIN character_board_overrides h "
                "ON h.character_id=c.character_id "
                "WHERE c.roster_visible=1 AND COALESCE(h.hidden,0)=0 "
                "UNION SELECT DISTINCT k.recipe_id FROM craft_known k "
                "JOIN craft_guild_members m ON k.character_id=-m.blizzard_id "
                "WHERE m.active=1"
            )
        }
        census = con.execute(
            "SELECT COUNT(*),SUM(CASE WHEN checked_at>0 THEN 1 ELSE 0 END) "
            "FROM craft_guild_members WHERE active=1",
        ).fetchone()
    demand = {r["recipe_id"]: (r["requests"], r["users"]) for r in wishes}
    curated = {}
    relevant = {
        "Alchemy", "Blacksmithing", "Enchanting", "Engineering",
        "Leatherworking", "Tailoring", "Cooking", "First Aid", "Fishing",
    }
    for prof, titles in priority_recipes.PRIORITIES.items():
        if prof not in relevant:
            continue
        for rank, title in enumerate(titles):
            curated[(prof, priority_recipes.normalized(title))] = max(0, 100 - rank * 8)

    matched = defaultdict(lambda: {"ids": [], "requests": 0, "users": 0,
                                  "points": 0, "profession": "", "name": ""})
    for recipe in recipes:
        key = (recipe["profession"], priority_recipes.normalized(recipe["name"]))
        base = curated.get(key, 0)
        req, users = demand.get(recipe["recipe_id"], (0, 0))
        if not base and not req:
            continue
        entry = matched[key]
        entry["ids"].append(recipe["recipe_id"])
        entry["requests"] += req
        entry["users"] += users
        entry["points"] = max(entry["points"], base)
        entry["profession"], entry["name"] = recipe["profession"], recipe["name"]

    result = []
    for entry in matched.values():
        if any(ident in verified for ident in entry["ids"]):
            continue
        result.append({
            "name": entry["name"], "profession": entry["profession"],
            "requests": entry["requests"], "requesters": entry["users"],
            "score": entry["points"] + 140 * entry["users"] + 12 * entry["requests"],
        })
    result.sort(key=lambda entry: (-entry["score"], -entry["requesters"],
                                   entry["profession"].casefold(),
                                   entry["name"].casefold()))
    return {"recipes": result[:max(0, min(int(limit), 10))],
            "guild_members": census[0] or 0, "guild_scanned": census[1] or 0}


def format_wanted(result):
    rows = result["recipes"]
    lines = ["**Ogre's 10 most wanted recipes**"]
    lines.append(
        "-# None have a confirmed crafter in Ogre's records yet. "
        "Blizzard can hide recipes, so this isn't proof nobody knows them."
    )
    for index, row in enumerate(rows, 1):
        url = "https://www.wowhead.com/forever/search?q=" + quote(row["name"], safe="")
        requests = (
            f" · {row['requesters']} guildie(s) asking"
            if row["requesters"] else " · raid/PvP shortlist"
        )
        lines.append(
            f"{index}. [{row['name']}](<{url}>) · "
            f"{row['profession']}{requests}"
        )
    if not rows:
        lines.append("Ogre's still fetching the recipe book. Ask for a craft to get things moving.")
    lines.append(
        f"-# Guild recipe scan: {result['guild_scanned']}/{result['guild_members']} "
        "members checked. Ranked by guild requests and a researched "
        "raid/PvP shortlist; no live Auctionator prices included."
    )
    return "\n".join(lines)[:1950]
