"""Read-only character queries for raids and the LFG board.

Each helper accepts the caller's existing SQLite connection. The public
cogs.raid_signup wrappers retain the original path defaults and transaction
lifecycle.
"""


def list_characters(con, discord_id):
    return con.execute(
        "SELECT name, class_name, spec, role FROM characters "
        "WHERE discord_id=? ORDER BY is_main DESC, name",
        (discord_id,),
    ).fetchall()


def member_characters(con, discord_id):
    return con.execute(
        "SELECT character_id, name, class_name, spec, role, last_simc_at "
        "FROM characters WHERE discord_id=? ORDER BY is_main DESC, name",
        (discord_id,),
    ).fetchall()


def character_race(con, discord_id, character_id):
    row = con.execute(
        "SELECT race FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    return row[0] if row else ""


def main_character_ids(con, discord_id):
    return {row[0] for row in con.execute(
        "SELECT character_id FROM characters WHERE discord_id=? AND is_main=1",
        (discord_id,),
    )}


def has_wowsims_export(con, discord_id, character_id):
    """Only consider a WoWSims export belonging to this exact character."""
    return con.execute(
        "SELECT 1 FROM characters c JOIN gear_exports g "
        "ON g.character_id=c.character_id "
        "WHERE c.discord_id=? AND c.character_id=? AND g.format='wowsims_json' "
        "LIMIT 1", (discord_id, character_id)
    ).fetchone() is not None
