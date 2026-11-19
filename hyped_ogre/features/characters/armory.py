"""Optional, read-only Forever EU character profile enrichment.

The raid SQLite character_id/Discord owner remains authoritative. Blizzard
levels are cached separately and only used when a verified full name matches.
Forever API namespaces are still undocumented (November 2026); failures
must never edit registrations or stop the character board.
"""
import asyncio
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
CHECK_INTERVAL = 4 * 60 * 60
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
    with sqlite3.connect(path, timeout=20) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS character_armory_cache (
            character_id INTEGER PRIMARY KEY,
            character_name TEXT NOT NULL,
            level INTEGER,
            race TEXT NOT NULL DEFAULT '',
            guild TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL
        )""")


def cached(path):
    """Return only levels of characters still matching their stored name."""
    try:
        with sqlite3.connect(path, timeout=20) as con:
            return {
                int(cid): {"name": name, "level": level, "updated_at": updated}
                for cid, name, level, updated in con.execute(
                    "SELECT character_id,character_name,level,updated_at "
                    "FROM character_armory_cache")
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
    return {
        "level": level,
        "race": str(race.get("name") or "")[:48] if isinstance(race, dict) else "",
        "guild": str(guild.get("name") or "")[:100] if isinstance(guild, dict) else "",
    }


def candidates(path, now=None, limit=MAX_REFRESH):
    """Only visible guild-roster registrations, never LFG-only characters."""
    now = int(now if now is not None else time.time())
    try:
        with sqlite3.connect(path, timeout=20) as con:
            con.row_factory = sqlite3.Row
            records = con.execute(
                "SELECT c.character_id,c.name,a.character_name,a.updated_at "
                "FROM characters c LEFT JOIN character_armory_cache a "
                "ON c.character_id=a.character_id "
                "WHERE c.roster_visible=1 ORDER BY c.is_main DESC,c.character_id"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    result = []
    for row in records:
        name = row["name"]
        if not slug(name):
            continue
        if (row["character_name"] == name and row["updated_at"] is not None
                and now - row["updated_at"] < CHECK_INTERVAL):
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
        return parse_character(await response.json(), name)


async def refresh(path, *, now=None, limit=MAX_REFRESH):
    """Bounded profile sync; no network without explicit Battle.net credentials."""
    client_id = os.getenv("BLIZZARD_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLIZZARD_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return 0
    initialise(path)
    available = candidates(path, now=now, limit=limit)
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
                with sqlite3.connect(path, timeout=20) as con:
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
                        "(character_id,character_name,level,race,guild,updated_at) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(character_id) "
                        "DO UPDATE SET character_name=excluded.character_name,"
                        "level=excluded.level,race=excluded.race,"
                        "guild=excluded.guild,updated_at=excluded.updated_at",
                        (char_id, name, profile["level"] if profile else None,
                         profile["race"] if profile else "",
                         profile["guild"] if profile else "", now),
                    )
                updated += 1
            return updated
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        LOGGER.warning("Forever Armory credentials/API unavailable: %s", type(exc).__name__)
        return 0
