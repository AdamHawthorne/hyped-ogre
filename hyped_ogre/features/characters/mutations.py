"""SQLite character registration and profile mutations for Hyped Ogre.

These functions use the exact SQL previously in cogs.raid_signup and accept
the existing connection supplied by the active Discord extension. Never
open a second database or import Discord in this module.
"""

def register_character(con, discord_id, name, class_name, spec, role):
    """Insert or update one member character under the original SQLite lock."""
    # Hold a write lock across lookup+insert to prevent competing clicks
    # registering the same character twice.
    con.execute("BEGIN IMMEDIATE")
    previous = con.execute(
        "SELECT character_id, class_name FROM characters "
        "WHERE discord_id=? AND name=? COLLATE NOCASE "
        "ORDER BY character_id LIMIT 1",
        (discord_id, name),
    ).fetchone()
    if previous:
        if previous[1] != class_name:
            con.execute("UPDATE characters SET off_spec='', off_role='' WHERE character_id=?",
                        (previous[0],))
        con.execute(
            "UPDATE characters SET class_name=?, spec=?, role=? "
            "WHERE character_id=? AND discord_id=?",
            (class_name, spec, role, previous[0], discord_id),
        )
        return "updated"
    has_main = con.execute(
        "SELECT 1 FROM characters WHERE discord_id=? AND is_main=1 LIMIT 1",
        (discord_id,),
    ).fetchone()
    con.execute(
        "INSERT INTO characters "
        "(discord_id, name, realm, class_name, spec, role, is_main) "
        "VALUES (?, ?, '', ?, ?, ?, ?)",
        (discord_id, name, class_name, spec, role, 0 if has_main else 1),
    )
    return "created"


def set_character_race(con, discord_id, character_id, race, race_classes):
    """Validate ownership and classic class/race compatibility."""
    row = con.execute(
        "SELECT class_name FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if not row:
        raise ValueError("That character isn't yours.")
    if row[0] not in race_classes.get(race, ()):
        raise ValueError("This race cannot play that class in Forever.")
    con.execute(
        "UPDATE characters SET race=? WHERE discord_id=? AND character_id=?",
        (race, discord_id, character_id),
    )


def set_main_character(con, discord_id, character_id):
    """Keep exactly one owned Main without deleting linked records."""
    row = con.execute(
        "SELECT 1 FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if not row:
        raise ValueError("Ogre cannot edit someone else's character.")
    con.execute("UPDATE characters SET is_main=0 WHERE discord_id=?", (discord_id,))
    con.execute(
        "UPDATE characters SET is_main=1 WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    )


def update_character_profile(con, discord_id, character_id, class_name,
                             spec, role, race_classes):
    """Edit in place, clear an incompatible race and retain raid references."""
    if not getattr(con, "in_transaction", False):
        con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT name,race,class_name FROM characters WHERE character_id=? AND discord_id=?",
        (character_id, discord_id),
    ).fetchone()
    if not row:
        raise ValueError("Ogre cannot edit someone else's character.")
    if row[2] != class_name:
        con.execute("UPDATE characters SET off_spec='', off_role='' WHERE character_id=?",
                    (character_id,))
    # A class change may invalidate the old race; clear it so a player
    # can choose a compatible race again from Characters.
    race = row[1] if class_name in race_classes.get(row[1], ()) else ""
    con.execute(
        "UPDATE characters SET class_name=?, spec=?, role=?, race=? "
        "WHERE character_id=? AND discord_id=?",
        (class_name, spec, role, race, character_id, discord_id),
    )
    return row[0]
