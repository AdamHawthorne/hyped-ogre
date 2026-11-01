"""Exact existing raid SQLite schema and legacy character identity upgrades.

Called on the connection created by the original cogs.raid_signup
extension. The caller owns the transaction, database path and lifecycle.
Do not change table names, migration order or existing record semantics.
"""


def ensure_signup_schema_on_connection(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS characters(
        character_id INTEGER PRIMARY KEY,
        discord_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        realm TEXT, class_name TEXT, spec TEXT, role TEXT,
        is_main INTEGER NOT NULL DEFAULT 1,
        last_simc_at INTEGER,
        UNIQUE(discord_id, name, realm)
    );
    CREATE TABLE IF NOT EXISTS raids(
        raid_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        raid_at TEXT NOT NULL,
        custom_title TEXT,
        banner_url TEXT,
        roster_posted_at TEXT,
        roster_updated_at TEXT,
        signup_open INTEGER NOT NULL DEFAULT 0,
        roster_published INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS signups(
        raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
        discord_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL REFERENCES characters(character_id),
        availability TEXT NOT NULL DEFAULT 'Available',
        note TEXT,
        submitted_at TEXT NOT NULL,
        show_on_discord INTEGER,
        PRIMARY KEY(raid_id, discord_id)
    );
    CREATE TABLE IF NOT EXISTS raid_absences(
        raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
        discord_id INTEGER NOT NULL,
        note TEXT,
        signed_off_at TEXT NOT NULL,
        PRIMARY KEY(raid_id, discord_id)
    );
    CREATE TABLE IF NOT EXISTS raid_public_roster(
        raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        assignment TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (raid_id, position)
    );
    CREATE TABLE IF NOT EXISTS gear_exports(
        export_id INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(character_id),
        submitted_at TEXT NOT NULL,
        format TEXT NOT NULL,
        raw_text TEXT NOT NULL,
        parsed_json TEXT
    );
    CREATE TABLE IF NOT EXISTS gear_images(
        image_id INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(character_id),
        submitted_at TEXT NOT NULL,
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        image_data BLOB NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attendance(
        raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
        discord_id INTEGER NOT NULL,
        character_id INTEGER REFERENCES characters(character_id),
        status TEXT NOT NULL DEFAULT 'Pending'
            CHECK(status IN ('Pending', 'Attended', 'Late', 'AFK', 'No-show', 'Excused')),
        late_minutes INTEGER,
        afk_minutes INTEGER,
        leadership_notes TEXT,
        updated_at TEXT,
        PRIMARY KEY(raid_id, discord_id)
    );
    """)

    # Visibility is per raid signup. NULL inherits the raid poster's
    # default until the first successful Sheets sync. Existing data
    # migrates without changing character registration or attendance.
    if "show_on_discord" not in {
        row[1] for row in con.execute("PRAGMA table_info(signups)")
    }:
        con.execute(
            "ALTER TABLE signups ADD COLUMN show_on_discord INTEGER"
        )

    # Race belongs to the shared character, not to a separate LFG copy.
    if "race" not in {row[1] for row in con.execute("PRAGMA table_info(characters)")}:
        con.execute("ALTER TABLE characters ADD COLUMN race TEXT NOT NULL DEFAULT ''")

    # Historic registrations allowed separate realm/casing variants of
    # one character. Consolidate those on startup without losing any
    # sign-ups, attendance references or saved SimC snapshots.
    con.execute("BEGIN IMMEDIATE")
    rows = con.execute(
        "SELECT character_id, discord_id, name, is_main, last_simc_at "
        "FROM characters ORDER BY character_id"
    ).fetchall()
    grouped = {}
    for character_id, member_id, character_name, is_main, simc_at in rows:
        normalized = " ".join((character_name or "").split())
        key = (member_id, normalized.casefold())
        grouped.setdefault(key, []).append(
            (character_id, normalized, is_main, simc_at)
        )
    for matches in grouped.values():
        keep_id, clean_name, _, _ = matches[0]
        for duplicate_id, _, _, _ in matches[1:]:
            for table in ("signups", "attendance", "gear_exports", "gear_images"):
                con.execute(
                    f"UPDATE {table} SET character_id=? WHERE character_id=?",
                    (keep_id, duplicate_id),
                )
            con.execute(
                "DELETE FROM characters WHERE character_id=?", (duplicate_id,)
            )
        con.execute(
            "UPDATE characters SET name=?, is_main=?, last_simc_at=? "
            "WHERE character_id=?",
            (clean_name, max(row[2] for row in matches),
             max((row[3] or 0) for row in matches) or None, keep_id),
        )
    # One identity per Discord user and character name, ignoring case
    # and the legacy realm column. Different characters remain separate.
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_raid_characters_member_name "
        "ON characters(discord_id, name COLLATE NOCASE)"
    )
