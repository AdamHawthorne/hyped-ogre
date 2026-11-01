"""Read-only raid signup and absence lookups.

The Discord extension retains caller-facing APIs, database paths, connections
and write operations; these helpers require an existing SQLite connection.
"""


def signup_note_details(con, raid_id, discord_id):
    return con.execute(
        "SELECT c.name,s.availability,s.note FROM signups s "
        "JOIN characters c ON c.character_id=s.character_id "
        "WHERE s.raid_id=? AND s.discord_id=?",
        (raid_id, discord_id),
    ).fetchone()


def my_signup(con, raid_id, discord_id):
    return con.execute(
        "SELECT c.name, s.availability FROM signups s "
        "JOIN characters c ON c.character_id=s.character_id "
        "WHERE s.raid_id=? AND s.discord_id=?",
        (raid_id, discord_id),
    ).fetchone()


def my_absence(con, raid_id, discord_id):
    return con.execute(
        "SELECT note, signed_off_at FROM raid_absences "
        "WHERE raid_id=? AND discord_id=?",
        (raid_id, discord_id),
    ).fetchone()
