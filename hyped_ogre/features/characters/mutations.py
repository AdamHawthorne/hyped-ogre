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
    if not getattr(con, "in_transaction", True):
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

def rename_character(con, discord_id, character_id, new_name):
    """Rename the same character ID without touching exports, LFG or raid history."""
    name = " ".join(str(new_name or "").split())
    parts = name.split()
    if (len(name) > 40 or len(parts) != 2
            or not all(part.replace("-", "").replace("'", "").isalpha()
                       for part in parts)):
        raise ValueError("Enter the full First Last character name (40 letters max).")
    current = con.execute(
        "SELECT name FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if current is None:
        raise ValueError("This character isn't registered to you.")
    if con.execute(
        "SELECT 1 FROM characters WHERE discord_id=? "
        "AND name=? COLLATE NOCASE AND character_id<>?",
        (discord_id, name, character_id),
    ).fetchone():
        raise ValueError("That character name is already registered on your account.")
    if current[0] == name:
        return False
    con.execute(
        "UPDATE characters SET name=? WHERE discord_id=? AND character_id=?",
        (name, discord_id, character_id),
    )
    return True


def remove_from_roster(con, discord_id, character_id):
    """Unlist a character without erasing raid attendance, gear or LFG links.

    Keep the original canonical ID. If the current main is unlisted, elect
    the oldest remaining visible character as main; never promote LFG-only
    characters behind the user's back.
    """
    row = con.execute(
        "SELECT roster_visible,is_main FROM characters "
        "WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if row is None:
        raise ValueError("This character isn't registered to that member.")
    if not row[0]:
        return False
    con.execute(
        "UPDATE characters SET roster_visible=0,is_main=0 "
        "WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    )
    if row[1]:
        next_id = con.execute(
            "SELECT character_id FROM characters "
            "WHERE discord_id=? AND roster_visible=1 "
            "ORDER BY character_id LIMIT 1", (discord_id,),
        ).fetchone()
        if next_id:
            con.execute(
                "UPDATE characters SET is_main=1 "
                "WHERE discord_id=? AND character_id=?",
                (discord_id, next_id[0]),
            )
    return True


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
        "SELECT roster_visible FROM characters WHERE discord_id=? AND character_id=?",
        (discord_id, character_id),
    ).fetchone()
    if not row:
        raise ValueError("Ogre cannot edit someone else's character.")
    if not row[0]:
        # An LFG-only character cannot displace a registered roster main.
        visible = con.execute(
            "SELECT 1 FROM characters WHERE discord_id=? AND roster_visible=1 LIMIT 1",
            (discord_id,),
        ).fetchone()
        if visible:
            raise ValueError(
                "Add this LFG character to the roster before making it Main.")
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
