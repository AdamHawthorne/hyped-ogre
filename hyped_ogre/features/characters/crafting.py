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

# V2 board display order: primary crafts, gathering, secondary skills.
CRAFTING_PROFESSIONS = (
    "Alchemy", "Blacksmithing", "Enchanting", "Engineering",
    "Leatherworking", "Tailoring",
)
GATHERING_PROFESSIONS = ("Herbalism", "Mining", "Skinning")
SECONDARY_PROFESSIONS = ("Cooking", "Fishing", "First Aid")
PROFESSIONS = (
    CRAFTING_PROFESSIONS + GATHERING_PROFESSIONS + SECONDARY_PROFESSIONS
)
# Filenames, not Discord emoji IDs. Resolve uploaded guild emoji at display.
PROFESSION_ICON_NAMES = {
    name: "ui_profession_" + name.lower().replace(" ", "")
    for name in PROFESSIONS
}
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
            CREATE TABLE IF NOT EXISTS craft_discoveries (
                character_id INTEGER NOT NULL, profession TEXT NOT NULL,
                recipe_id INTEGER NOT NULL, detected_at INTEGER NOT NULL,
                PRIMARY KEY(character_id,profession,recipe_id));
            CREATE TABLE IF NOT EXISTS craft_requests (
                request_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL, reply_id INTEGER,
                requested_at INTEGER NOT NULL, resolved_at INTEGER,
                resolved_by INTEGER);
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
                # A first imported snapshot is a baseline, not a newly
                # learned recipe. Only report additions after a prior
                # complete known_recipes snapshot was already recorded.
                prior_count = con.execute(
                    "SELECT recipe_count FROM craft_professions "
                    "WHERE character_id=? AND profession=?",
                    (character_id, name),
                ).fetchone()
                if prior_count is not None and prior_count[0] >= 0:
                    previous = {
                        row[0] for row in con.execute(
                            "SELECT recipe_id FROM craft_known "
                            "WHERE character_id=? AND profession=?",
                            (character_id, name),
                        )
                    }
                    con.executemany(
                        "INSERT OR IGNORE INTO craft_discoveries VALUES (?,?,?,?)",
                        ((character_id, name, ident, stamp)
                         for ident in recipes if ident not in previous),
                    )
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
                "skill_required=COALESCE(excluded.skill_required,"
                "craft_catalog.skill_required),"
                "output_name=CASE WHEN excluded.output_name<>'' "
                "THEN excluded.output_name ELSE craft_catalog.output_name END,"
                "observed_at=excluded.observed_at",
                (ident, prof, name[:140], str(item.get("output") or "")[:140],
                 item.get("skill_required") if
                 type(item.get("skill_required")) is int and
                 1 <= item["skill_required"] <= 450 else None,
                 "", "", 0, stamp))
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
    for profession, rows in grouped.items():
        # V2 no longer ranks or displays arbitrary featured recipe totals.
        # Prefer the most skilled crafters, with names breaking ties.
        rows.sort(key=lambda row: (-row["skill"], row["name"].casefold()))
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


def latest_high_skill_discoveries(path, limit=3):
    """Newly detected learned recipes requiring 300 skill, when verified.

    The Armory lacks the in-game time learned. detected_at is the first
    snapshot in which Ogre saw the recipe, not a claimed game timestamp.
    Do not mistake a character at 300 skill for a 300-required recipe.
    """
    with closing(connect(path)) as con:
        con.row_factory = sqlite3.Row
        entries = con.execute("""
            SELECT c.name AS character_name,d.profession,d.recipe_id,
                   d.detected_at,r.name AS recipe_name
            FROM craft_discoveries d
            JOIN characters c ON c.character_id=d.character_id
            LEFT JOIN character_board_overrides h
                ON h.character_id=c.character_id
            JOIN craft_catalog r ON r.recipe_id=d.recipe_id
            JOIN craft_known k ON k.character_id=d.character_id
                AND k.profession=d.profession AND k.recipe_id=d.recipe_id
            WHERE c.roster_visible=1 AND COALESCE(h.hidden,0)=0
                AND r.skill_required>=300
            ORDER BY d.detected_at DESC,d.rowid DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in entries]


def store_request(path, *, request_id, guild_id, channel_id, reply_id=None,
                  requested_at=None):
    with closing(connect(path)) as con, con:
        con.execute(
            "INSERT OR IGNORE INTO craft_requests "
            "(request_id,guild_id,channel_id,reply_id,requested_at) "
            "VALUES (?,?,?,?,?)",
            (request_id,guild_id,channel_id,reply_id,
             int(requested_at if requested_at is not None else time.time())),
        )


def resolve_request(path, *, request_id, user_id, resolved_at=None):
    with closing(connect(path)) as con, con:
        con.execute(
            "UPDATE craft_requests SET resolved_at=?,resolved_by=? "
            "WHERE request_id=? AND resolved_at IS NULL",
            (int(resolved_at if resolved_at is not None else time.time()),
             user_id,request_id),
        )
        return bool(con.total_changes)


def expired_requests(path, now=None):
    now = int(now if now is not None else time.time())
    with closing(connect(path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM craft_requests
            WHERE (resolved_at IS NOT NULL AND resolved_at + 86400 <= ?)
               OR (resolved_at IS NULL AND requested_at + 259200 <= ?)
        """, (now,now)).fetchall()
    return [dict(row) for row in rows]


def forget_request(path, request_id):
    with closing(connect(path)) as con, con:
        con.execute("DELETE FROM craft_requests WHERE request_id=?", (request_id,))


def request_exists(path, request_id, channel_id):
    with closing(connect(path)) as con:
        row = con.execute(
            "SELECT 1 FROM craft_requests "
            "WHERE request_id=? AND channel_id=?",
            (request_id,channel_id),
        ).fetchone()
    return bool(row)


# Informal in-game terminology mapped only to confirmed canonical recipe
# names. Ownership still must be proven via Blizzard known_recipes.
RECIPE_ALIASES = {
    "100 hp chest": "enchant chest major health",
    "100hp chest": "enchant chest major health",
    "100 health chest": "enchant chest major health",
    "chest 100 hp": "enchant chest major health",
    "chest 100 health": "enchant chest major health",
    "100 hp on chest": "enchant chest major health",
    "100 health on chest": "enchant chest major health",
    "100 hp on my chest": "enchant chest major health",
    "100 health on my chest": "enchant chest major health",
    "100hp on chest": "enchant chest major health",
    "major health chest": "enchant chest major health",
    "chest major health": "enchant chest major health",
    "major health enchant": "enchant chest major health",
    "100 hp chest enchant": "enchant chest major health",
    "100 health chest enchant": "enchant chest major health",
    "enchant 100 hp chest": "enchant chest major health",
    "100hp on my chest": "enchant chest major health",
    "100 hp to chest": "enchant chest major health",
    "30 spellpower weapon": "enchant weapon spell power",
    "30 spell power weapon": "enchant weapon spell power",
    "spellpower weapon": "enchant weapon spell power",
    "55 healing weapon": "enchant weapon healing power",
    "healing power weapon": "enchant weapon healing power",
    "fiery weapon": "enchant weapon fiery weapon",
    "arcanite bar": "transmute arcanite",
    "arcanite bars": "transmute arcanite",
    "crusader": "enchant weapon crusader",
    "crusader enchant": "enchant weapon crusader",
    "enchant crusader": "enchant weapon crusader",
    "weapon crusader": "enchant weapon crusader",
}


def _recipe_key(text):
    text = _search_terms(text)
    return text


def _suggestions(recipes, term, *, limit=3):
    from difflib import get_close_matches
    keys = {}
    for recipe in recipes:
        for value in (recipe["name"], recipe["output_name"]):
            if value:
                keys.setdefault(_recipe_key(value), recipe["name"])
    names = get_close_matches(term, list(keys), n=limit, cutoff=0.62)
    return list(dict.fromkeys(keys[name] for name in names))[:limit]


def _search_terms(text):
    text = re.sub(r"[^a-z0-9 ]", " ", text.casefold())
    text = re.sub(r"\s+", " ", text).strip()
    for word in ("recipe", "pattern", "plans", "formula", "schematic", "transmute"):
        text = re.sub(rf"\b{word}\b", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def query(path, question):
    """Resolve normal speech against actual catalog; never infer ownership.

    Uses explicit aliases for established enchant nicknames, then exact,
    full-token and unique substring matching. Uncertain requests return
    candidate names, not a randomly selected recipe or fabricated crafter.
    """
    original = _recipe_key(question)
    if len(original) < 3:
        return None
    target = RECIPE_ALIASES.get(original, original)
    with closing(connect(path)) as con:
        con.row_factory = sqlite3.Row
        all_recipes = [dict(row) for row in con.execute(
            "SELECT * FROM craft_catalog")]
        if not all_recipes:
            return {"status": "unavailable"}
        def aliases(recipe):
            return [
                _recipe_key(name) for name in
                (recipe["name"], recipe["output_name"])
                if name
            ]
        exact = [r for r in all_recipes if target in aliases(r)]
        if exact:
            matches = exact
        else:
            tokens = set(target.split())
            # Only remove generic words, not item names such as
            # "weapon", "chest", "health", "bar" or stat values.
            tokens -= {"enchant", "enchantment", "craft", "recipe", "on",
                       "my", "for", "the", "can", "do", "make", "an"}
            matches = [
                r for r in all_recipes if tokens and any(
                    tokens.issubset(set(name.split()))
                    for name in aliases(r)
                )
            ]
            if not matches:
                matches = [r for r in all_recipes if any(
                    target in name or name in target
                    for name in aliases(r) if len(name) >= 6
                )]
        if not matches:
            return {
                "status": "unknown",
                "suggestions": _suggestions(all_recipes, target),
            }
        if len(matches) > 1:
            # Multiple recipe IDs can legitimately refer to the same
            # spell name. Resolve those together, but do not combine
            # distinct crafts into one answer.
            names = {_recipe_key(r["name"]) for r in matches}
            if len(names) > 1:
                return {
                    "status": "ambiguous",
                    "matches": list(dict.fromkeys(
                        r["name"] for r in matches))[:5],
                }
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
