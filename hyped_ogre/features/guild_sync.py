"""Authenticated, idempotent intake for the local Hyped Guild Companion."""

from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from hyped_ogre.features.classrooms.config import GUILD_ID

LOG = logging.getLogger(__name__)
MAX_CLOCK_SKEW = 300
MAX_CAPTURE_AGE = 30 * 24 * 60 * 60
MAX_LEVEL_ROWS = 500
MAX_BANK_ROWS = 2000
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class SyncPayloadError(ValueError):
    """The request is authenticated but does not match the sync schema."""


class SyncDeliveryError(RuntimeError):
    """Accepted data could not yet be announced to the configured channel."""


def _db_path(path: str | None = None) -> str:
    return path or os.getenv("GUILD_SYNC_DB_PATH", "guild_sync.db")


def _canonical_name(name: str) -> str:
    # Character names cannot contain a hyphen. The suffix is a realm when an
    # addon/API returns a full character-realm name.
    return name.split("-", 1)[0].strip().casefold()


def _valid_text(value: Any, maximum: int) -> bool:
    return (isinstance(value, str) and 1 <= len(value.strip()) <= maximum
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _valid_time(value: Any, now: int) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool) and value > 0
            and value <= now + MAX_CLOCK_SKEW and now - value <= MAX_CAPTURE_AGE)


def validate_payload(payload: Any, now: int | None = None, expected_guild: str | None = None) -> dict[str, Any]:
    now = int(time.time()) if now is None else int(now)
    expected_guild = expected_guild or os.getenv("HYPED_SYNC_GUILD_NAME", "Hyped")
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise SyncPayloadError("unsupported payload version")
    if payload.get("source") != "HypedGuildTools":
        raise SyncPayloadError("unsupported payload source")
    if payload.get("guild") != expected_guild:
        raise SyncPayloadError("unexpected guild")
    roster = payload.get("roster", [])
    bank_events = payload.get("bankEvents", [])
    if not isinstance(roster, list) or len(roster) > MAX_LEVEL_ROWS:
        raise SyncPayloadError("invalid roster list")
    if not isinstance(bank_events, list) or len(bank_events) > MAX_BANK_ROWS:
        raise SyncPayloadError("invalid bank event list")

    clean_roster = []
    seen_roster_keys = set()
    for row in roster:
        if not isinstance(row, dict):
            raise SyncPayloadError("invalid roster row")
        name = row.get("name")
        level = row.get("level")
        captured = row.get("capturedAt", payload.get("rosterCapturedAt"))
        class_token = row.get("class")
        if (not _valid_text(name, 96) or "@" in name or "|" in name
                or not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 60
                or not _valid_time(captured, now)):
            raise SyncPayloadError("invalid roster data")
        if class_token is not None and (not isinstance(class_token, str)
                or not re.fullmatch(r"[A-Z_]{2,16}", class_token)):
            raise SyncPayloadError("invalid class token")
        name_key = _canonical_name(name)
        if not name_key or name_key in seen_roster_keys:
            raise SyncPayloadError("duplicate roster character")
        seen_roster_keys.add(name_key)
        clean_roster.append({"name": name.strip(), "key": name_key,
                             "level": level, "class": class_token, "capturedAt": captured})

    clean_events = []
    seen_ids = set()
    allowed_kinds = {"deposit", "withdraw", "money_deposit", "money_withdraw"}
    for row in bank_events:
        if not isinstance(row, dict):
            raise SyncPayloadError("invalid bank event")
        event_id = row.get("eventId")
        kind = row.get("kind")
        name = row.get("character")
        item = row.get("item")
        count = row.get("count")
        tab = row.get("tab", "-")
        age = row.get("ageSeconds")
        captured = row.get("capturedAt")
        if (not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id)
                or event_id in seen_ids or kind not in allowed_kinds
                or not _valid_text(name, 96) or "@" in name or "|" in name
                or not _valid_text(item, 128) or "@" in item or "|" in item
                or not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 2_147_483_647
                or not isinstance(tab, (str, int)) or isinstance(tab, bool)
                or (isinstance(tab, str) and not re.fullmatch(r"(?:-|[1-9][0-9]?)", tab))
                or (isinstance(tab, int) and not 1 <= tab <= 10)
                or not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 10 * 365 * 86400
                or not _valid_time(captured, now)):
            raise SyncPayloadError("invalid bank event data")
        seen_ids.add(event_id)
        clean_events.append({"eventId": event_id, "kind": kind, "character": name.strip(),
                             "item": item.strip(), "count": count, "tab": str(tab),
                             "ageSeconds": age, "capturedAt": captured})
    return {"roster": clean_roster, "bankEvents": clean_events, "guild": expected_guild}


def ensure_schema(path: str | None = None) -> None:
    database_path = _db_path(path)
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path, timeout=5) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS guild_sync_levels (
            character_key TEXT PRIMARY KEY,
            character_name TEXT NOT NULL,
            class_token TEXT,
            level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 60),
            captured_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS guild_bank_events (
            event_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            character_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            tab_name TEXT NOT NULL,
            age_seconds INTEGER NOT NULL,
            captured_at INTEGER NOT NULL,
            received_at INTEGER NOT NULL,
            announced_at INTEGER
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_guild_bank_pending ON guild_bank_events(announced_at, received_at)")


def main_roster_names(raid_db_path: str | None = None) -> dict[str, str]:
    """Return only uniquely named registered mains; ambiguous names are omitted."""
    path = raid_db_path or os.getenv("RAID_DB_PATH", "raid_signup.db")
    if not Path(path).is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=3) as con:
            rows = con.execute("SELECT name FROM characters WHERE is_main=1").fetchall()
    except sqlite3.Error:
        raise
    candidates: dict[str, list[str]] = {}
    for row in rows:
        name = row[0]
        if _valid_text(name, 96):
            candidates.setdefault(_canonical_name(name), []).append(name)
    return {key: names[0] for key, names in candidates.items() if len(names) == 1}


def persist_payload(payload: dict[str, Any], raid_db_path: str | None = None,
                    database_path: str | None = None, now: int | None = None) -> dict[str, int]:
    validated = validate_payload(payload, now=now)
    ensure_schema(database_path)
    now_value = int(time.time()) if now is None else int(now)
    resolved_raid_db = raid_db_path or os.getenv("RAID_DB_PATH", "raid_signup.db")
    if validated["roster"] and not Path(resolved_raid_db).is_file():
        raise sqlite3.OperationalError("raid character roster database is not available")
    allowed_mains = main_roster_names(resolved_raid_db)
    updated = ignored = inserted_events = 0
    with sqlite3.connect(_db_path(database_path), timeout=5) as con:
        for row in validated["roster"]:
            registered_name = allowed_mains.get(row["key"])
            if not registered_name:
                ignored += 1
                continue
            cursor = con.execute("""INSERT INTO guild_sync_levels
                (character_key,character_name,class_token,level,captured_at,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(character_key) DO UPDATE SET
                    character_name=excluded.character_name,
                    class_token=excluded.class_token,
                    level=excluded.level,
                    captured_at=excluded.captured_at,
                    updated_at=excluded.updated_at
                WHERE excluded.captured_at > guild_sync_levels.captured_at""",
                (row["key"], registered_name, row["class"], row["level"], row["capturedAt"], now_value))
            updated += cursor.rowcount
        for row in validated["bankEvents"]:
            cursor = con.execute("""INSERT OR IGNORE INTO guild_bank_events
                (event_id,kind,character_name,item_name,item_count,tab_name,age_seconds,
                 captured_at,received_at,announced_at)
                VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
                (row["eventId"], row["kind"], row["character"], row["item"], row["count"],
                 row["tab"], row["ageSeconds"], row["capturedAt"], now_value))
            inserted_events += cursor.rowcount
    return {"levelsUpdated": updated, "levelsIgnored": ignored,
            "bankEventsReceived": inserted_events}


def latest_levels_by_name(database_path: str | None = None, now: int | None = None) -> dict[str, dict[str, Any]]:
    path = _db_path(database_path)
    if not Path(path).is_file():
        return {}
    now_value = int(time.time()) if now is None else int(now)
    try:
        with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=3) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT character_key,character_name,class_token,level,captured_at "
                               "FROM guild_sync_levels WHERE captured_at>=?",
                               (now_value - MAX_CAPTURE_AGE,)).fetchall()
    except sqlite3.Error:
        return {}
    return {row["character_key"]: dict(row) for row in rows}


def _format_age(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 3600:
        return f"about {max(1, seconds // 60)}m ago"
    if seconds < 86400:
        return f"about {seconds // 3600}h ago"
    return f"about {seconds // 86400}d ago"


def _money_text(copper: int) -> str:
    gold, remainder = divmod(copper, 10_000)
    silver, copper = divmod(remainder, 100)
    return f"{gold}g {silver}s {copper}c"


def _event_line(row: sqlite3.Row, now_value: int) -> str:
    import discord

    character = discord.utils.escape_markdown(discord.utils.escape_mentions(row["character_name"]))
    item = discord.utils.escape_markdown(discord.utils.escape_mentions(row["item_name"]))
    is_money = row["kind"].startswith("money_")
    action = row["kind"].removeprefix("money_")
    action_text = "deposited" if action == "deposit" else "withdrew"
    amount = _money_text(row["item_count"]) if is_money else f"{row['item_count']} × {item}"
    tab = "" if row["tab_name"] == "-" else f" · tab {row['tab_name']}"
    age = int(row["age_seconds"]) + max(0, now_value - int(row["captured_at"]))
    return f"**{character}** {action_text} {amount}{tab} · log { _format_age(age) }"


async def announce_pending(bot: Any, database_path: str | None = None, channel_id: int | None = None) -> int:
    import discord

    channel_value = channel_id or int(os.getenv("HYPED_SYNC_BANK_CHANNEL_ID") or 0)
    if not channel_value:
        with sqlite3.connect(_db_path(database_path), timeout=5) as con:
            pending = con.execute("SELECT COUNT(*) FROM guild_bank_events WHERE announced_at IS NULL").fetchone()[0]
        if pending:
            raise SyncDeliveryError("bank announcement channel is not configured")
        return 0
    channel = bot.get_channel(channel_value)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_value)
        except Exception as exc:
            raise SyncDeliveryError("bank announcement channel is not available") from exc
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
        raise SyncDeliveryError("bank announcement destination must be a regular Hyped text channel")

    with sqlite3.connect(_db_path(database_path), timeout=5) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM guild_bank_events WHERE announced_at IS NULL "
                           "ORDER BY received_at,event_id LIMIT 50").fetchall()
    sent = 0
    now_value = int(time.time())
    for start in range(0, len(rows), 10):
        batch = rows[start:start + 10]
        embed = discord.Embed(title="Hyped Guild Bank Activity", colour=0xB84A3A,
                              description="\n".join(_event_line(row, now_value) for row in batch))
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            LOG.warning("Guild bank announcement deferred (%s)", type(exc).__name__)
            raise SyncDeliveryError("Discord could not accept the bank announcement") from exc
        with sqlite3.connect(_db_path(database_path), timeout=5) as con:
            con.executemany("UPDATE guild_bank_events SET announced_at=? WHERE event_id=? AND announced_at IS NULL",
                            [(now_value, row["event_id"]) for row in batch])
        sent += len(batch)
    with sqlite3.connect(_db_path(database_path), timeout=5) as con:
        remaining = con.execute("SELECT COUNT(*) FROM guild_bank_events WHERE announced_at IS NULL").fetchone()[0]
    if remaining:
        raise SyncDeliveryError("additional bank entries remain queued for the next sync retry")
    return sent


def _valid_signature(secret: str, timestamp: str, signature: str, body: bytes, now: int) -> bool:
    if not timestamp.isdecimal() or not signature:
        return False
    try:
        stamp = int(timestamp)
    except ValueError:
        return False
    if abs(now - stamp) > MAX_CLOCK_SKEW:
        return False
    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def register_route(app: Any, bot: Any) -> None:
    """Install the route only after the operator explicitly enables sync."""
    secret = os.getenv("HYPED_SYNC_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("HYPED_SYNC_ENABLED requires HYPED_SYNC_SECRET with at least 32 characters")

    async def hyped_sync(request: Any):
        from aiohttp import web

        now_value = int(time.time())
        body = await request.read()
        timestamp = request.headers.get("X-Hyped-Timestamp", "")
        signature = request.headers.get("X-Hyped-Signature", "")
        if len(body) > 256 * 1024 or not _valid_signature(secret, timestamp, signature, body, now_value):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
            validated = validate_payload(payload, now=now_value)
            stats = await asyncio.to_thread(
                persist_payload, payload, os.getenv("RAID_DB_PATH", "raid_signup.db"),
                os.getenv("GUILD_SYNC_DB_PATH", "guild_sync.db"), now_value)
            announced = await announce_pending(bot)
        except SyncPayloadError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "invalid_json"}, status=400)
        except SyncDeliveryError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Guild companion sync storage failed (%s)", type(exc).__name__)
            return web.json_response({"error": "sync_storage_unavailable"}, status=503)
        return web.json_response({"ok": True, **stats, "bankAnnouncements": announced,
                                  "acceptedRosterRows": len(validated["roster"])})

    app.router.add_post("/hyped/sync", hyped_sync)

