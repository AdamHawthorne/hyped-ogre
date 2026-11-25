"""Optional, read-only Forever EU character profile enrichment.

The raid SQLite character_id/Discord owner remains authoritative. Blizzard
levels are cached separately and only used when a verified full name matches.
Forever API namespaces are still undocumented (November 2026); failures
must never edit registrations or stop the character board.
"""
import asyncio
from contextlib import closing
import json
import logging
import os
import re
import sqlite3
import time
from urllib.parse import quote

import aiohttp

LOGGER = logging.getLogger(__name__)
HOST = "https://eu.api.blizzard.com"
TOKEN_URL = "https://oauth.battle.net/token"
NAMESPACE = "profile-forever-eu"
# Refresh a character roughly hourly, with batches capped to avoid bursts.
# The character board itself still redraws from saved data every minute.
CHECK_INTERVAL = 60 * 60
MAX_REFRESH = 12
MAX_LEVEL = 60


def slug(name):
    """The Forever API uses first-last, with no realm or ruleset in the path."""
    parts = str(name or "").split()
    if len(parts) != 2 or any(not p.strip() for p in parts):
        return None
    if not all(re.fullmatch(r"[\w'-]+", part, re.UNICODE) for part in parts):
        return None
    return quote("-".join(parts).lower(), safe="-'")


def public_url(name):
    part = slug(name)
    return (f"https://worldofwarcraft.blizzard.com/en-gb/forever/eu/"
            f"armory/character/{part}") if part else None


def initialise(path):
    """Only additive, isolated cache storage. Never touch canonical characters."""
    with closing(sqlite3.connect(path, timeout=20)) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS character_armory_cache (
            character_id INTEGER PRIMARY KEY,
            character_name TEXT NOT NULL,
            level INTEGER,
            race TEXT NOT NULL DEFAULT '',
            guild TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            ilvl INTEGER,
            professions TEXT NOT NULL DEFAULT '[]',
            average_ilvl_version INTEGER NOT NULL DEFAULT 1
        )""")
        columns = {
            row[1] for row in con.execute("PRAGMA table_info(character_armory_cache)")
        }
        legacy = any(field not in columns for field in
                     ("ilvl", "professions", "average_ilvl_version"))
        if "ilvl" not in columns:
            con.execute("ALTER TABLE character_armory_cache ADD COLUMN ilvl INTEGER")
        if "professions" not in columns:
            con.execute(
                "ALTER TABLE character_armory_cache "
                "ADD COLUMN professions TEXT NOT NULL DEFAULT '[]'"
            )
        if "average_ilvl_version" not in columns:
            # Pre-existing entries favoured *equipped* iLvl. Force one
            # bounded refresh so V1 displays Blizzard's average iLvl instead.
            con.execute(
                "ALTER TABLE character_armory_cache ADD COLUMN "
                "average_ilvl_version INTEGER NOT NULL DEFAULT 1"
            )
        if legacy:
            # Invalidate only once on migration; future restarts respect TTL.
            con.execute("UPDATE character_armory_cache SET updated_at=0")


def cached(path):
    """Return only levels of characters still matching their stored name."""
    try:
        with closing(sqlite3.connect(path, timeout=20)) as con, con:
            return {
                int(cid): {
                    "name": name, "level": level, "updated_at": updated,
                    "ilvl": ilvl,
                    "profession_details": json.loads(professions or "[]"),
                }
                for cid, name, level, updated, ilvl, professions in con.execute(
                    "SELECT character_id,character_name,level,updated_at,"
                    "ilvl,professions FROM character_armory_cache")
            }
    except sqlite3.OperationalError:
        return {}


def parse_character(payload, expected_name):
    """Reject mismatched identity, malformed level, and unavailable characters."""
    if not isinstance(payload, dict):
        return None
    full = " ".join(
        str(payload.get(key) or "").strip()
        for key in ("name", "surname")
    ).strip()
    if full.casefold() != " ".join(expected_name.split()).casefold():
        return None
    level = payload.get("level")
    if not isinstance(level, int) or isinstance(level, bool):
        return None
    if not 1 <= level <= MAX_LEVEL:
        return None
    race = payload.get("race") or {}
    guild = payload.get("guild") or {}
    # Prefer Blizzard's average_item_level for the guild roster.
    # Equipped iLvl is only a fallback when the average is not available.
    # Never mistake character level for equipment item level.
    average = None
    for field in ("average_item_level", "equipped_item_level"):
        value = payload.get(field)
        if type(value) in (int, float) and 1 <= value <= 250:
            average = round(float(value), 1) if isinstance(value, float) else value
            break
    return {
        "level": level, "ilvl": average, "profession_details": [],
        "race": str(race.get("name") or "")[:48] if isinstance(race, dict) else "",
        "guild": str(guild.get("name") or "")[:100] if isinstance(guild, dict) else "",
    }


def parse_professions(payload):
    """Normalise Blizzard profile profession tiers to the same WoWSims shape."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("primaries", payload.get("primary_professions", []))
    if not isinstance(entries, list):
        return []
    results = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        profession = item.get("profession")
        if not isinstance(profession, dict):
            continue
        name = profession.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        tiers = item.get("tiers", [])
        skill = item.get("skill_points")
        if isinstance(tiers, list) and tiers:
            valid = [t.get("skill_points") for t in tiers
                     if isinstance(t, dict) and type(t.get("skill_points")) is int]
            if valid:
                skill = max(valid)
        if type(skill) is not int or not 1 <= skill <= 999:
            skill = None
        results.append({"name": name.strip()[:48], "level": skill})
        if len(results) == 2:
            break
    return results


def credentials_configured():
    """Report configuration only; never log or reveal credential values."""
    return bool(
        os.getenv("BLIZZARD_CLIENT_ID", "").strip()
        and os.getenv("BLIZZARD_CLIENT_SECRET", "").strip()
    )


def coverage(path):
    """Validate cached iLvl against the current canonical visible character."""
    initialise(path)
    with closing(sqlite3.connect(path, timeout=20)) as con, con:
        row = con.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN a.level BETWEEN 1 AND 60 "
            "AND a.character_name = c.name THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN a.ilvl BETWEEN 1 AND 250 "
            "AND a.character_name = c.name THEN 1 ELSE 0 END) "
            "FROM characters c LEFT JOIN character_armory_cache a "
            "ON a.character_id=c.character_id "
            "WHERE c.roster_visible=1"
        ).fetchone()
    return {
        "credentials": credentials_configured(),
        "visible": int(row[0] or 0),
        "levels": int(row[1] or 0),
        "ilvls": int(row[2] or 0),
    }


def candidates(path, now=None, limit=MAX_REFRESH, *, for_crafting=False):
    """Only visible guild-roster registrations, never LFG-only characters."""
    now = int(now if now is not None else time.time())
    if for_crafting:
        from . import crafting
        crafting.initialise(path)
    try:
        with closing(sqlite3.connect(path, timeout=20)) as con, con:
            con.row_factory = sqlite3.Row
            records = con.execute(
                "SELECT c.character_id,c.name,a.character_name,a.updated_at "
                "FROM characters c LEFT JOIN character_armory_cache a "
                "ON c.character_id=a.character_id "
                "WHERE c.roster_visible=1 ORDER BY c.is_main DESC,c.character_id"
            ).fetchall()
            verified = set()
            if for_crafting:
                verified = {
                    entry[0] for entry in con.execute(
                        "SELECT DISTINCT character_id FROM craft_professions "
                        "WHERE recipe_count>=0"
                    )
                }
    except sqlite3.OperationalError:
        return []
    result = []
    for row in records:
        name = row["name"]
        if not slug(name):
            continue
        fresh = (
            row["character_name"] == name and row["updated_at"] is not None
            and now - row["updated_at"] < (
                15 * 60 if for_crafting else CHECK_INTERVAL
            )
        )
        if fresh and (not for_crafting or row["character_id"] in verified):
            continue
        result.append((row["character_id"], name))
        if len(result) >= limit:
            break
    return result


async def fetch_profile(session, token, name):
    """Only Blizzard's fixed EU host; never accept arbitrary link/redirect."""
    path = slug(name)
    if path is None:
        return None
    url = f"{HOST}/profile/wow/character/{path}"
    async with session.get(
        url, params={"namespace": NAMESPACE, "locale": "en_GB"},
        headers={"Authorization": "Bearer " + token}, allow_redirects=False,
    ) as response:
        if response.status == 404:
            return None
        response.raise_for_status()
        profile = parse_character(await response.json(), name)
    if profile is None:
        return None
    # Professions are a separate endpoint. A 404 or temporary failure
    # does not invalidate the character's level or equipped item level.
    try:
        async with session.get(
            f"{HOST}/profile/wow/character/{path}/professions",
            params={"namespace": NAMESPACE, "locale": "en_GB"},
            headers={"Authorization": "Bearer " + token},
            allow_redirects=False,
        ) as response:
            if response.status == 200:
                profession_payload = await response.json()
                profile["profession_details"] = parse_professions(
                    profession_payload
                )
                # Only explicitly supplied known_recipes are treated as
                # verified character ownership; missing endpoints don't erase.
                profile["craft_snapshot"] = profession_payload
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        LOGGER.debug("Optional Blizzard professions unavailable: %s", name)
    return profile


async def refresh(path, *, now=None, limit=MAX_REFRESH,
                  for_crafting=False):
    """Bounded profile sync; no network without explicit Battle.net credentials."""
    client_id = os.getenv("BLIZZARD_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLIZZARD_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        LOGGER.info("Forever Armory sync unavailable: Blizzard credentials not configured.")
        return 0
    initialise(path)
    available = candidates(
        path, now=now, limit=limit, for_crafting=for_crafting
    )
    if not available:
        return 0
    now = int(now if now is not None else time.time())
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                TOKEN_URL,
                auth=aiohttp.BasicAuth(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                token = (await response.json()).get("access_token")
            if not isinstance(token, str) or not token:
                return 0
            updated = 0
            for char_id, name in available:
                try:
                    profile = await fetch_profile(session, token, name)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    LOGGER.warning("Forever Armory profile unavailable: %s", type(exc).__name__)
                    continue
                with closing(sqlite3.connect(path, timeout=20)) as con, con:
                    # A character may have been renamed/deleted while this
                    # HTTPS request was in flight. Match both ID and name.
                    present = con.execute(
                        "SELECT 1 FROM characters WHERE character_id=? "
                        "AND name=? AND roster_visible=1", (char_id, name)
                    ).fetchone()
                    if not present:
                        continue
                    con.execute(
                        "INSERT INTO character_armory_cache "
                        "(character_id,character_name,level,race,guild,updated_at,"
                        "ilvl,professions) VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(character_id) "
                        "DO UPDATE SET character_name=excluded.character_name,"
                        "level=CASE WHEN character_armory_cache.character_name=excluded.character_name "
                        "THEN COALESCE(excluded.level,character_armory_cache.level) ELSE excluded.level END,"
                        "race=excluded.race,guild=excluded.guild,updated_at=excluded.updated_at,"
                        "ilvl=CASE WHEN character_armory_cache.character_name=excluded.character_name "
                        "THEN COALESCE(excluded.ilvl,character_armory_cache.ilvl) ELSE excluded.ilvl END,"
                        "professions=CASE WHEN character_armory_cache.character_name=excluded.character_name "
                        "AND excluded.professions='[]' THEN character_armory_cache.professions "
                        "ELSE excluded.professions END",
                        (char_id, name, profile["level"] if profile else None,
                         profile["race"] if profile else "",
                         profile["guild"] if profile else "", now,
                         profile["ilvl"] if profile else None,
                         json.dumps(profile["profession_details"] if profile else [])),
                    )
                if profile and "craft_snapshot" in profile:
                    try:
                        from hyped_ogre.features.characters import crafting
                        crafting.initialise(path)
                        crafting.save_profile(
                            path, char_id, profile["craft_snapshot"], observed_at=now
                        )
                    except (sqlite3.Error, ValueError, TypeError):
                        LOGGER.warning(
                            "Crafting snapshot not saved for character %s", char_id
                        )
                updated += 1
            return updated
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        LOGGER.warning("Forever Armory credentials/API unavailable: %s", type(exc).__name__)
        return 0
