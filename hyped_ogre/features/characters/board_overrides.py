"""Leadership-only presentation overrides for the public Characters board.

Never modify canonical character identity, raid attendance, visibility for LFG,
or classroom access. All helpers use the caller's existing SQLite connection.
"""

def character(con, character_id):
    row = con.execute(
        "SELECT character_id,name FROM characters "
        "WHERE character_id=? AND is_main=1 AND roster_visible=1",
        (character_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Choose a registered main character from the roster.")
    return row[1]


def rename(con, character_id, display_name):
    original = character(con, character_id)
    name = " ".join((display_name or "").split())
    if not name or len(name) > 40:
        raise ValueError("Choose a display name between 1 and 40 characters.")
    con.execute(
        "INSERT INTO character_board_overrides(character_id,display_name,hidden) "
        "VALUES(?,?,0) ON CONFLICT(character_id) "
        "DO UPDATE SET display_name=excluded.display_name",
        (character_id, name),
    )
    return original, name


def reset_name(con, character_id):
    original = character(con, character_id)
    con.execute(
        "UPDATE character_board_overrides SET display_name='' WHERE character_id=?",
        (character_id,),
    )
    return original


def set_hidden(con, character_id, hidden):
    original = character(con, character_id)
    con.execute(
        "INSERT INTO character_board_overrides(character_id,display_name,hidden) "
        "VALUES(?,'',?) ON CONFLICT(character_id) "
        "DO UPDATE SET hidden=excluded.hidden",
        (character_id, int(bool(hidden))),
    )
    return original


def choices(con, query):
    """Return IDs for Discord's 25-result autocomplete, including hidden mains."""
    text = (query or "").strip().casefold()
    return con.execute(
        "SELECT c.character_id,c.name,c.class_name,"
        "COALESCE(o.display_name,''),COALESCE(o.hidden,0) "
        "FROM characters c LEFT JOIN character_board_overrides o "
        "ON o.character_id=c.character_id "
        "WHERE c.is_main=1 AND c.roster_visible=1 "
        "AND (LOWER(c.name) LIKE ? OR LOWER(COALESCE(o.display_name,'')) LIKE ? "
        "OR CAST(c.character_id AS TEXT) LIKE ?) "
        "ORDER BY LOWER(c.name),c.character_id LIMIT 25",
        tuple('%' + text + '%' for _ in range(3)),
    ).fetchall()


def changed(con):
    return con.execute(
        "SELECT c.character_id,c.name,o.display_name,o.hidden "
        "FROM character_board_overrides o JOIN characters c "
        "ON c.character_id=o.character_id "
        "WHERE c.is_main=1 AND c.roster_visible=1 "
        "AND (o.display_name<>'' OR o.hidden=1) "
        "ORDER BY LOWER(c.name),c.character_id"
    ).fetchall()
