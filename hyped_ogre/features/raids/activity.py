"""Durable raid-change outbox, written within the signup transaction."""

import sqlite3


def enqueue(con, raid_id, member_id, kind, note, created_at):
    con.execute("INSERT INTO raid_activity_events "
                "(raid_id,discord_id,kind,note,created_at) VALUES (?,?,?,?,?)",
                (raid_id, member_id, kind, note or "", created_at))


def pending(con):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(
        "SELECT * FROM raid_activity_events WHERE delivered_message_id IS NULL "
        "ORDER BY event_id LIMIT 20")]


def delivered(con, event_id, message_id):
    con.execute("UPDATE raid_activity_events SET delivered_message_id=? WHERE event_id=?",
                (message_id, event_id))
