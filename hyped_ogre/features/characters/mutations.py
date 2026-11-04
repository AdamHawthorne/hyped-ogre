"""SQLite character registration and profile mutations for Hyped Ogre.

These functions use the exact SQL previously in cogs.raid_signup and accept
the existing connection supplied by the active Discord extension. Never
open a second database or import Discord in this module.
"""

def promote_to_roster(con, discord_id, character_id):
    """Opt an owned, already-registered character into the guild roster.

    Reuse the same character_id, exports, LFG references and raid records.
    If this is a member's first roster character, make it their visible main.
    Never remove existing roster membership or set another member's data.
    """
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT roster_visible FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if row is None:
        raise ValueError("Choose your own registered character.")
    if row[0]:
        return False
    visible_main = con.execute(
        "SELECT character_id FROM characters WHERE discord_id=? "
        "AND roster_visible=1 AND is_main=1 LIMIT 1", (discord_id,),
    ).fetchone()
    if not visible_main:
        con.execute("UPDATE characters SET is_main=0 WHERE discord_id=?", (discord_id,))
    con.execute(
        "UPDATE characters SET roster_visible=1,is_main=? "
        "WHERE discord_id=? AND character_id=?",
        (0 if visible_main else 1, discord_id, character_id),
    )
    return True


def register_character(con, discord_id, name, class_name, spec, role, *,
                       roster_visible=True):
    """Upsert one shared character; LFG creation must not add them to the roster.

    A visible registration can promote an existing LFG record, but an LFG
    edit can never demote a roster character.
    """
    con.execute("BEGIN IMMEDIATE")
    previous = con.execute(
        "SELECT character_id,class_name FROM characters "
        "WHERE discord_id=? AND name=? COLLATE NOCASE "
        "ORDER BY character_id LIMIT 1", (discord_id, name),
    ).fetchone()
    if previous:
        if previous[1] != class_name:
            con.execute(
                "UPDATE characters SET off_spec='',off_role='' WHERE character_id=?",
                (previous[0],),
            )
        con.execute(
            "UPDATE characters SET class_name=?,spec=?,role=? "
            "WHERE character_id=? AND discord_id=?",
            (class_name, spec, role, previous[0], discord_id),
        )
        if roster_visible:
            promote_to_roster(con, discord_id, previous[0])
        return "updated"

    visible_main = con.execute(
        "SELECT 1 FROM characters WHERE discord_id=? "
        "AND is_main=1 AND roster_visible=1 LIMIT 1", (discord_id,),
    ).fetchone()
    any_main = con.execute(
        "SELECT 1 FROM characters WHERE discord_id=? AND is_main=1 LIMIT 1",
        (discord_id,),
    ).fetchone()
    if roster_visible and not visible_main:
        # Previously LFG-only mains cannot block the first roster main.
        con.execute("UPDATE characters SET is_main=0 WHERE discord_id=?", (discord_id,))
    is_main = (not visible_main) if roster_visible else (not any_main)
    con.execute(
        "INSERT INTO characters "
        "(discord_id,name,realm,class_name,spec,role,is_main,roster_visible) "
        "VALUES (?,?, '',?,?,?,?,?)",
        (discord_id, name, class_name, spec, role, int(is_main), int(roster_visible)),
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
