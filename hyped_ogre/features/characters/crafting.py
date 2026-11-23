"""Verified Forever crafting records. SQLite persists across Fly.io restarts.

Only Blizzard profile known_recipes confirm ownership. Blizzard's static
catalogue supplies spell/recipe names. Acquisition notes require an explicit
trusted source; missing information is never guessed.
"""
import re
import sqlite3
import time
from contextlib import closing
from difflib import get_close_matches

PROFESSIONS = ("Alchemy", "Blacksmithing", "Cooking", "Enchanting",
               "Engineering", "First Aid", "Fishing", "Herbalism",
               "Leatherworking", "Mining", "Skinning", "Tailoring")
COOKING = {"cooking", "first aid", "fishing"}
MIN_SKILL = 200
# Verified independent Forever item and classic vendor references, not guesses.
CURATED = {
    "transmute arcanite": ("Alchemy", "Arcanite Bar", 275,
        "Recipe: Transmute Arcanite from Alchemist Pestlezugg in Gadgetzan, Tanaris",
        "https://wowforever.ninja/professions/alchemy/17187-transmute-arcanite/"),
}


def connect(path):
    return sqlite3.connect(path, timeout=20)


def initialise(path):
    with closing(connect(path)) as con, con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS craft_professions (
                character_id INTEGER NOT NULL, profession TEXT NOT NULL,
                skill INTEGER NOT NULL, recipe_count INTEGER NOT NULL DEFAULT 0,
                observed_at INTEGER NOT NULL,
                PRIMARY KEY(character_id,profession));
            CREATE TABLE IF NOT EXISTS craft_known (
                character_id INTEGER NOT NULL, profession TEXT NOT NULL,
                recipe_id INTEGER NOT NULL, observed_at INTEGER NOT NULL,
                PRIMARY KEY(character_id,profession,recipe_id));
            CREATE TABLE IF NOT EXISTS craft_catalog (
                recipe_id INTEGER PRIMARY KEY, profession TEXT NOT NULL,
                name TEXT NOT NULL, output_name TEXT NOT NULL DEFAULT '',
                skill_required INTEGER, acquisition TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0, observed_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS craft_channel (
                guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                panel_id INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS craft_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # Known Forever craft: a checked seed so Arcanite lookups work even
        # before the static Blizzard catalogue's first successful import.
        prof, output, skill, source, url = CURATED["transmute arcanite"]
        con.execute(
            "INSERT OR IGNORE INTO craft_catalog "
            "(recipe_id,profession,name,output_name,skill_required,"
            "acquisition,source_url,verified,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (17187, prof, "Transmute: Arcanite", output, skill,
             source, url, 1, int(time.time())),
        )


def _recipes_for(entry):
    """Accept API variants and tier-level known_recipes; never infer unknown IDs."""
    found = {}
    for tier in [entry] + [
        item for item in entry.get("tiers", []) if isinstance(item, dict)
    ]:
        for item in tier.get("known_recipes", []) or []:
            if not isinstance(item, dict):
                continue
            ident = item.get("id")
            if type(ident) is not int or ident <= 0:
                continue
            name = item.get("name")
            if not isinstance(name, str):
                name = ""
            found[ident] = name.strip()[:120]
    return found


def parse_profile(payload):
    """Professions with actual recipe lists; returns None if API is incomplete."""
    if not isinstance(payload, dict) or not any(
        key in payload for key in (
            "primaries", "primary_professions", "secondaries",
            "secondary_professions"
        )
    ):
        return None
    groups = []
    for key in ("primaries", "primary_professions", "secondaries", "secondary_professions"):
        value = payload.get(key, [])
        if isinstance(value, list):
            groups.extend(item for item in value if isinstance(item, dict))
    output = []
    for entry in groups:
        prof = entry.get("profession")
        name = prof.get("name") if isinstance(prof, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        tiers = [t for t in entry.get("tiers", []) if isinstance(t, dict)]
        skills = [entry.get("skill_points")] + [t.get("skill_points") for t in tiers]
        valid = [s for s in skills if type(s) is int and 0 <= s <= 999]
        skill = max(valid) if valid else 0
        # Some responses omit known_recipes when the API is incomplete.
        has_list = any("known_recipes" in obj for obj in [entry, *tiers])
        recipes = _recipes_for(entry) if has_list else None
        output.append((name, skill, recipes))
    return output


def save_profile(path, character_id, payload, observed_at=None):
    """Replace only professions whose known recipe lists are present.

    Missing /professions requests never clear a previous verified snapshot.
    """
    parsed = parse_profile(payload)
    if parsed is None:
        return False
    stamp = int(observed_at if observed_at is not None else time.time())
    with closing(connect(path)) as con, con:
        # An authoritative full snapshot must retire a profession a player
        # unlearned. A partial snapshot cannot prove a profession was dropped.
        both_groups_present = (
            ("primaries" in payload or "primary_professions" in payload)
            and ("secondaries" in payload or "secondary_professions" in payload)
        )
        if both_groups_present:
            current = {name for name, _, _ in parsed}
            previous = con.execute(
                "SELECT profession FROM craft_professions WHERE character_id=?",
                (character_id,)
            ).fetchall()
            for (old_name,) in previous:
                if old_name not in current:
                    con.execute(
                        "DELETE FROM craft_known WHERE character_id=? AND profession=?",
                        (character_id, old_name))
                    con.execute(
                        "DELETE FROM craft_professions "
                        "WHERE character_id=? AND profession=?",
                        (character_id, old_name))
        for name, skill, recipes in parsed:
            if recipes is None:
                # If the endpoint omits known_recipes altogether, do not
                # present a fabricated zero-recipe count as verification.
                existing = con.execute(
                    "SELECT recipe_count FROM craft_professions "
                    "WHERE character_id=? AND profession=?",
                    (character_id, name)
                ).fetchone()
                count = existing[0] if existing is not None else -1
            else:
                count = len(recipes)
                con.execute(
                    "DELETE FROM craft_known WHERE character_id=? AND profession=?",
                    (character_id, name))
                con.executemany(
                    "INSERT INTO craft_known VALUES (?,?,?,?)",
                    ((character_id, name, ident, stamp) for ident in recipes))
                # Profile names are official and may enrich a static catalog gap.
                for ident, label in recipes.items():
                    if label:
                        con.execute(
                            "INSERT OR IGNORE INTO craft_catalog "
                            "(recipe_id,profession,name,observed_at) VALUES (?,?,?,?)",
                            (ident, name, label, stamp))
            con.execute(
                "INSERT INTO craft_professions VALUES (?,?,?,?,?) "
                "ON CONFLICT(character_id,profession) DO UPDATE SET "
                "skill=excluded.skill,recipe_count=excluded.recipe_count,"
                "observed_at=excluded.observed_at",
                (character_id, name, skill, count, stamp))
    return True


def save_catalog(path, recipes, observed_at=None):
    """Store Blizzard static recipe data. Never erase acquisition verification."""
    stamp = int(observed_at if observed_at is not None else time.time())
    with closing(connect(path)) as con, con:
        for item in recipes:
            ident = item.get("id")
            name = item.get("name")
            prof = item.get("profession")
            if type(ident) is not int or not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(prof, str) or not prof.strip():
                continue
            con.execute(
                "INSERT INTO craft_catalog (recipe_id,profession,name,output_name,"
                "skill_required,acquisition,source_url,verified,observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET "
                "profession=excluded.profession,name=excluded.name,"
                "output_name=CASE WHEN excluded.output_name<>'' "
                "THEN excluded.output_name ELSE craft_catalog.output_name END,"
                "observed_at=excluded.observed_at",
                (ident, prof, name[:140], str(item.get("output") or "")[:140],
                 None, "", "", 0, stamp))
        # Apply carefully sourced overrides only to name-matched recipes.
        for recipe_name, (prof, product, skill, source, url) in CURATED.items():
            con.execute(
                "UPDATE craft_catalog SET output_name=?,skill_required=?,"
                "acquisition=?,source_url=?,verified=1 "
                "WHERE LOWER(REPLACE(name,':',''))=? AND LOWER(profession)=?",
                (product, skill, source, url, recipe_name, prof.casefold()))


def catalog_due(path, now=None):
    with closing(connect(path)) as con:
        row = con.execute(
            "SELECT value FROM craft_state WHERE key='catalog_updated'").fetchone()
    return not row or int(now or time.time()) - int(row[0]) > 86400


def mark_catalog(path, now=None):
    with closing(connect(path)) as con, con:
        con.execute(
            "INSERT INTO craft_state VALUES ('catalog_updated',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(int(now or time.time())),))


def leaderboard(path):
    """Only known registrants, no hidden characters, minimum 200 skill."""
    with closing(connect(path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT c.character_id,c.discord_id,c.name,p.profession,p.skill,
                   p.recipe_count,p.observed_at
            FROM craft_professions p JOIN characters c
            ON c.character_id=p.character_id
            LEFT JOIN character_board_overrides h
            ON h.character_id=c.character_id
            WHERE c.roster_visible=1 AND COALESCE(h.hidden,0)=0
              AND p.skill>=?
            ORDER BY LOWER(p.profession),p.recipe_count DESC,
                     p.skill DESC,LOWER(c.name)
        """, (MIN_SKILL,)).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["profession"], []).append(dict(row))
    return grouped


def board_rows(path):
    """Guild crafters 200+: verified API first, WSE skill-only fallback.

    A WoWSims skill value is never proof of a learned recipe. Only
    explicit Blizzard known_recipes create a verified recipe count.
    """
    grouped = leaderboard(path)
    seen = {
        (row["character_id"], prof.casefold())
        for prof, rows in grouped.items() for row in rows
    }
    from . import equipment
    try:
        exports = equipment.latest_profiles(path)
    except sqlite3.OperationalError:
        exports = []
    with closing(connect(path)) as con:
        meta = {
            int(row[0]): (row[1], bool(row[2]))
            for row in con.execute(
                "SELECT c.character_id,c.is_main,COALESCE(h.hidden,0) "
                "FROM characters c LEFT JOIN character_board_overrides h "
                "ON h.character_id=c.character_id WHERE c.roster_visible=1"
            )
        }
    for export in exports:
        char_id = export["character_id"]
        if char_id not in meta or meta[char_id][1]:
            continue
        parsed = equipment.parse_export(export["raw_text"])
        if not parsed:
            continue
        for item in parsed.get("profession_details") or []:
            name = item.get("name")
            skill = item.get("level")
            if (not isinstance(name, str) or name not in PROFESSIONS
                    or type(skill) is not int or skill < MIN_SKILL):
                continue
            key = (char_id, name.casefold())
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(name, []).append({
                "character_id": char_id,
                "discord_id": export["discord_id"],
                "name": export["name"],
                "profession": name,
                "skill": skill,
                "recipe_count": -1,
                "source": "WoWSims",
            })
    for rows in grouped.values():
        rows.sort(key=lambda row: (
            -(row["recipe_count"] if row["recipe_count"] >= 0 else -1),
            -row["skill"], row["name"].casefold()
        ))
    return grouped


def diagnostics(path):
    """Safe coverage counters; no developer credentials or member secrets."""
    from . import armory, equipment
    with closing(connect(path)) as con:
        visible = con.execute(
            "SELECT COUNT(*) FROM characters WHERE roster_visible=1"
        ).fetchone()[0]
        counts = con.execute(
            "SELECT COUNT(DISTINCT character_id), "
            "SUM(CASE WHEN recipe_count>=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN recipe_count>=0 AND recipe_count>0 THEN recipe_count ELSE 0 END) "
            "FROM craft_professions"
        ).fetchone()
        index_count = con.execute(
            "SELECT COUNT(*) FROM craft_catalog"
        ).fetchone()[0]
    try:
        exports = len(equipment.latest_profiles(path))
    except sqlite3.OperationalError:
        exports = 0
    return {
        "credentials": armory.credentials_configured(),
        "visible": visible, "blizzard_characters": counts[0] or 0,
        "verified_professions": counts[1] or 0,
        "verified_recipes": counts[2] or 0,
        "catalog": index_count, "wowsims_exports": exports,
    }


def _search_terms(text):
    text = re.sub(r"[^a-z0-9 ]", " ", text.casefold())
    text = re.sub(r"\s+", " ", text).strip()
    for word in ("recipe", "pattern", "plans", "formula", "schematic", "transmute"):
        text = re.sub(rf"\b{word}\b", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def query(path, question):
    """Return exact or distinct fuzzy match; never claim skill implies ownership."""
    term = _search_terms(question)
    if not term or len(term) < 3:
        return None
    with closing(connect(path)) as con:
        con.row_factory = sqlite3.Row
        all_recipes = [dict(row) for row in con.execute(
            "SELECT * FROM craft_catalog")]
        if not all_recipes:
            return {"status": "unavailable"}
        def aliases(recipe):
            return [recipe["name"], recipe["output_name"]]
        exact = [r for r in all_recipes if term in
                 [_search_terms(x) for x in aliases(r) if x]]
        if exact:
            matches = exact
        else:
            matches = [r for r in all_recipes if any(
                term in _search_terms(x) for x in aliases(r) if x)]
        if not matches:
            return {"status": "unknown"}
        # Avoid pretending we identified a recipe when a term matches dozens.
        if len(matches) > 5:
            return {"status": "ambiguous", "matches": [r["name"] for r in matches[:5]]}
        match_ids = [r["recipe_id"] for r in matches]
        found = con.execute(
            "SELECT c.discord_id,c.name,k.recipe_id,p.skill "
            "FROM craft_known k JOIN characters c ON c.character_id=k.character_id "
            "JOIN craft_professions p ON p.character_id=k.character_id "
            "AND p.profession=k.profession "
            "LEFT JOIN character_board_overrides h ON h.character_id=c.character_id "
            "WHERE k.recipe_id IN (" + ",".join("?" for _ in match_ids) + ") "
            "AND c.roster_visible=1 AND COALESCE(h.hidden,0)=0 "
            "ORDER BY p.skill DESC,LOWER(c.name) LIMIT 12", match_ids).fetchall()
        crafters = [dict(row) for row in found]
    return {"status": "found", "recipes": matches, "crafters": crafters}


def channel(path, guild_id):
    with closing(connect(path)) as con:
        row = con.execute(
            "SELECT channel_id,panel_id FROM craft_channel WHERE guild_id=?",
            (guild_id,)).fetchone()
    return tuple(row) if row else None


def set_channel(path, guild_id, channel_id, panel_id=0):
    with closing(connect(path)) as con, con:
        con.execute(
            "INSERT INTO craft_channel VALUES (?,?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,"
            "panel_id=excluded.panel_id",
            (guild_id, channel_id, panel_id))


def set_panel(path, guild_id, channel_id, panel_id):
    with closing(connect(path)) as con, con:
        con.execute(
            "UPDATE craft_channel SET panel_id=? WHERE guild_id=? AND channel_id=?",
            (panel_id, guild_id, channel_id))
