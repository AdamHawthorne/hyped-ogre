"""Additive permission journal in the existing raid/character SQLite database.

The character and Sheets tables are read only here. Open with mode=rw so a
wrong path cannot silently create an empty replacement character database.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from hyped_ogre.features.raids.signup_reference import CLASS_SPECS

from .config import CHANNELS, MEMBER_PERMISSIONS


class ClassroomError(ValueError):
    pass


class ClassroomStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=20)
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialise(self):
        with self.connection() as con:
            # Validate the canonical register before adding our own two tables.
            con.execute("SELECT discord_id,class_name,spec,role,off_spec,off_role FROM characters LIMIT 0")
            con.execute("""CREATE TABLE IF NOT EXISTS classroom_settings (
                guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS classroom_grants (
                guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                discord_id INTEGER NOT NULL, owned_permissions TEXT NOT NULL,
                pending INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, channel_id, discord_id)
            )""")

    def enabled(self, guild_id):
        with self.connection() as con:
            row = con.execute("SELECT enabled FROM classroom_settings WHERE guild_id=?",
                              (guild_id,)).fetchone()
        return bool(row and row[0])

    def set_enabled(self, guild_id, enabled):
        with self.connection() as con:
            con.execute("INSERT INTO classroom_settings (guild_id,enabled) VALUES (?,?) "
                        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled",
                        (guild_id, int(enabled)))

    def snapshot(self, guild_id):
        """One consistent snapshot of all mains/alts plus our permission journal."""
        with self.connection() as con:
            con.execute("BEGIN")
            characters = con.execute(
                "SELECT discord_id,class_name,spec,role,off_spec,off_role FROM characters"
            ).fetchall()
            rows = con.execute(
                "SELECT channel_id,discord_id,owned_permissions,pending "
                "FROM classroom_grants WHERE guild_id=?", (guild_id,)
            ).fetchall()
        desired = {channel.channel_id: set() for channel in CHANNELS}
        for ident, klass, spec, role, off_spec, off_role in characters:
            ident = int(ident)
            if ident <= 0:
                continue
            valid_roles = CLASS_SPECS.get(klass, {}).get(spec, ())
            off_roles = CLASS_SPECS.get(klass, {}).get(off_spec, ())
            for channel in CHANNELS:
                if (channel.class_name and channel.class_name == klass) or (
                    channel.role and (
                        (channel.role == role and role in valid_roles) or
                        (channel.role == off_role and off_role in off_roles)
                    )
                ):
                    desired[channel.channel_id].add(ident)
        journal = {}
        for channel_id, ident, owned, pending in rows:
            try:
                permissions = tuple(json.loads(owned))
            except (TypeError, ValueError) as exc:
                raise ClassroomError("Invalid classroom permission journal; no permissions changed.") from exc
            if not permissions or any(p not in MEMBER_PERMISSIONS for p in permissions):
                raise ClassroomError("Invalid classroom permission journal; no permissions changed.")
            journal[channel_id, ident] = (permissions, bool(pending))
        return desired, journal

    def prepare_grant(self, guild_id, channel_id, ident, owned):
        """Write intent BEFORE Discord, allowing a crash/HTTP timeout to recover."""
        with self.connection() as con:
            con.execute("INSERT INTO classroom_grants "
                        "(guild_id,channel_id,discord_id,owned_permissions,pending) "
                        "VALUES (?,?,?,?,1)",
                        (guild_id, channel_id, ident, json.dumps(list(owned))))

    def grant_applied(self, guild_id, channel_id, ident):
        with self.connection() as con:
            con.execute("UPDATE classroom_grants SET pending=0 "
                        "WHERE guild_id=? AND channel_id=? AND discord_id=?",
                        (guild_id, channel_id, ident))

    def forget_grant(self, guild_id, channel_id, ident):
        with self.connection() as con:
            con.execute("DELETE FROM classroom_grants "
                        "WHERE guild_id=? AND channel_id=? AND discord_id=?",
                        (guild_id, channel_id, ident))
