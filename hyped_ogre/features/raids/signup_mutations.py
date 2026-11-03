"""Raid signup and absence SQL operations for Hyped Ogre.

The active Discord extension still owns each SQLite connection, its
transaction boundary, default DB path, note validation and WoWSims policy
function. Do not open another connection or import Discord here. Keeping
the same SQL ordering protects existing player signups and absent records.
"""

from . import activity


def record_signup(con, raid_id, discord_id, character_id, path, note,
                  requirement_for_signup, now_iso):
    """Save the one-character signup, preserving policy checks and prior notes."""
    if not getattr(con, "in_transaction", False):
        con.execute("BEGIN IMMEDIATE")
    event = con.execute(
        "SELECT signup_open, archived_at FROM raids WHERE raid_id=?",
        (raid_id,),
    ).fetchone()
    if not event or not event[0] or event[1]:
        raise ValueError("Sign-ups aren't open for this raid.")
    needed = requirement_for_signup(
        raid_id, discord_id, character_id, path
    )
    if needed:
        # Never erase a valid existing signup when leadership changes
        # this policy or when someone merely edits their note.
        prior = con.execute(
            "SELECT character_id FROM signups WHERE raid_id=? AND discord_id=? "
            "AND availability='Available' AND NOT EXISTS "
            "(SELECT 1 FROM raid_absences a "
            "WHERE a.raid_id=signups.raid_id AND a.discord_id=signups.discord_id)",
            (raid_id, discord_id),
        ).fetchone()
        if not prior or prior[0] != character_id:
            if needed == "fresh":
                raise ValueError(
                    "Ogre need fresh WoWSims export for this raid. "
                    "Click WoWSims on raid panel."
                )
            raise ValueError(
                "Ogre need WoWSims export first. Click WoWSims on raid panel."
            )
    character = con.execute(
        "SELECT character_id FROM characters "
        "WHERE character_id=? AND discord_id=?",
        (character_id, discord_id),
    ).fetchone()
    if not character:
        raise ValueError("Choose one of your own saved characters.")
    previous = con.execute("SELECT note FROM signups WHERE raid_id=? AND discord_id=?",
                           (raid_id, discord_id)).fetchone()
    was_absent = con.execute("SELECT 1 FROM raid_absences WHERE raid_id=? AND discord_id=?",
                             (raid_id, discord_id)).fetchone() is not None
    con.execute("""
        INSERT INTO signups(raid_id, discord_id, character_id, availability, note, submitted_at)
        VALUES (?, ?, ?, 'Available', ?, ?)
        ON CONFLICT(raid_id, discord_id) DO UPDATE SET
            character_id=excluded.character_id, availability='Available',
            note=CASE
                WHEN ? IS NULL AND signups.availability='Available'
                THEN signups.note
                WHEN ? IS NULL THEN NULL
                ELSE excluded.note END,
            submitted_at=CASE
                WHEN signups.character_id=excluded.character_id
                 AND signups.availability='Available'
                THEN signups.submitted_at
                ELSE excluded.submitted_at END
    """, (raid_id, discord_id, character_id, note, now_iso(), note, note))
    # Returning after an absence restores the player's Available status.
    con.execute(
        "DELETE FROM raid_absences WHERE raid_id=? AND discord_id=?",
        (raid_id, discord_id),
    )
    current = con.execute("SELECT note FROM signups WHERE raid_id=? AND discord_id=?",
                          (raid_id, discord_id)).fetchone()[0] or ""
    before = (previous[0] or "") if previous else ""
    if was_absent:
        activity.enqueue(con, raid_id, discord_id, "available", current, now_iso())
    elif current != before:
        activity.enqueue(con, raid_id, discord_id, "note", current, now_iso())


def record_absence(con, raid_id, discord_id, note, timestamp):
    """Save an absent state, preserving prior character and signup note."""
    if not getattr(con, "in_transaction", False):
        con.execute("BEGIN IMMEDIATE")
    event = con.execute(
        "SELECT raid_id, archived_at FROM raids WHERE raid_id=?", (raid_id,),
    ).fetchone()
    if not event:
        raise ValueError("Raid no longer exists.")
    if event[1]:
        raise ValueError("Archived raids are read-only.")
    previous = con.execute("SELECT note FROM raid_absences WHERE raid_id=? AND discord_id=?",
                           (raid_id, discord_id)).fetchone()
    con.execute("""
        INSERT INTO raid_absences(raid_id, discord_id, note, signed_off_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(raid_id, discord_id) DO UPDATE SET
            note=excluded.note, signed_off_at=excluded.signed_off_at
    """, (raid_id, discord_id, note or None, timestamp))
    # Keep the original sign-up note separate from absence notes.
    con.execute(
        "UPDATE signups SET availability='Unavailable' "
        "WHERE raid_id=? AND discord_id=?",
        (raid_id, discord_id),
    )
    if previous is None or (previous[0] or "") != (note or ""):
        activity.enqueue(con, raid_id, discord_id, "absent", note, timestamp)
