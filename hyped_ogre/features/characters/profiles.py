"""Validated, same-class off-spec profiles on the caller's SQLite connection."""

from hyped_ogre.features.raids.signup_reference import CLASS_SPECS


def offspec(con, owner_id, character_id):
    row = con.execute("SELECT class_name,off_spec,off_role FROM characters "
                      "WHERE discord_id=? AND character_id=?",
                      (owner_id, character_id)).fetchone()
    if row is None:
        raise ValueError("That character isn't yours.")
    return row


def set_offspec(con, owner_id, character_id, class_name, spec, role):
    if not getattr(con, "in_transaction", False):
        con.execute("BEGIN IMMEDIATE")
    current = offspec(con, owner_id, character_id)
    if current[0] != class_name:
        raise ValueError("The class changed. Reopen Characters before choosing an off-spec.")
    if (spec or role) and role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid off-spec and role for this class.")
    con.execute("UPDATE characters SET off_spec=?,off_role=? "
                "WHERE discord_id=? AND character_id=?",
                (spec, role, owner_id, character_id))
