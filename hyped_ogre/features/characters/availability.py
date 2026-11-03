"""Private long-term availability and durable roster notifications."""

import sqlite3
from datetime import datetime, timezone


def initialise(con):
    con.execute("""CREATE TABLE IF NOT EXISTS character_absences (
        discord_id INTEGER PRIMARY KEY, period TEXT NOT NULL, note TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS character_activity_events (
        event_id INTEGER PRIMARY KEY, discord_id INTEGER NOT NULL,
        kind TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
        delivered_message_id INTEGER)""")


def enqueue(con, member_id, kind, note):
    con.execute("INSERT INTO character_activity_events (discord_id,kind,note,created_at) VALUES (?,?,?,?)",
                (member_id, kind, note, datetime.now(timezone.utc).isoformat()))


def save(path, member_id, period, note):
    from cogs.raid_signup import database
    period, note = period.strip(), note.strip()
    if not period or len(period) > 100 or len(note) > 1000:
        raise ValueError("Enter when you will be away (100 characters maximum) and an optional note (1000 maximum).")
    with database(path) as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT period,note FROM character_absences WHERE discord_id=?", (member_id,)).fetchone()
        if current == (period, note):
            return False
        con.execute("INSERT INTO character_absences (discord_id,period,note,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(discord_id) DO UPDATE SET period=excluded.period,note=excluded.note,updated_at=excluded.updated_at",
                    (member_id, period, note, datetime.now(timezone.utc).isoformat()))
        enqueue(con, member_id, "long_absence", f"When: {period}\n{note}")
    return True


def clear(path, member_id):
    from cogs.raid_signup import database
    with database(path) as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("DELETE FROM character_absences WHERE discord_id=?", (member_id,)).rowcount:
            return False
        enqueue(con, member_id, "returned", "Long-term absence cleared by the member.")
    return True


def pending(con):
    con.row_factory = sqlite3.Row
    return [dict(r, scope="character") for r in con.execute(
        "SELECT * FROM character_activity_events WHERE delivered_message_id IS NULL ORDER BY event_id LIMIT 20")]


def delivered(con, event_id, message_id):
    con.execute("UPDATE character_activity_events SET delivered_message_id=? WHERE event_id=?", (message_id, event_id))


def notify(interaction):
    interaction.client.dispatch("raid_activity_changed", interaction.guild_id)
