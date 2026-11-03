"""Post-launch group plans, separate from the preserved launch-planning rows."""


def initialise(con):
    con.execute("CREATE TABLE IF NOT EXISTS lfg_settings "
                "(channel_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'launch')")
    con.execute("""CREATE TABLE IF NOT EXISTS lfg_live_plans (
        channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
        activity TEXT NOT NULL, availability TEXT NOT NULL, looking_for TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '', avatar_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(channel_id,user_id))""")


def mode(con, channel_id):
    row = con.execute("SELECT mode FROM lfg_settings WHERE channel_id=?", (channel_id,)).fetchone()
    return row[0] if row else "launch"


def set_mode(con, channel_id, value):
    if value not in ("launch", "live"):
        raise ValueError("Choose launch or live.")
    con.execute("INSERT INTO lfg_settings (channel_id,mode) VALUES (?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET mode=excluded.mode", (channel_id, value))


def listings(con, channel_id):
    return [dict(r) for r in con.execute(
        "SELECT * FROM lfg_live_plans WHERE channel_id=? ORDER BY updated_at DESC,user_id", (channel_id,))]


def save(con, channel_id, owner_id, character_id, activity, availability, looking_for, notes, avatar):
    fields = [" ".join(str(value or "").split()) for value in (activity, availability, looking_for, notes)]
    if not fields[0] or not fields[1] or any(len(value) > maximum for value, maximum in zip(fields, (120, 120, 120, 180))):
        raise ValueError("Enter an activity and availability, keeping each field within its limit.")
    con.execute("INSERT INTO lfg_live_plans "
                "(channel_id,user_id,character_id,activity,availability,looking_for,notes,avatar_url) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,user_id) DO UPDATE SET "
                "character_id=excluded.character_id,activity=excluded.activity,availability=excluded.availability,"
                "looking_for=excluded.looking_for,notes=excluded.notes,avatar_url=excluded.avatar_url,updated_at=CURRENT_TIMESTAMP",
                (channel_id, owner_id, character_id, *fields, avatar))
