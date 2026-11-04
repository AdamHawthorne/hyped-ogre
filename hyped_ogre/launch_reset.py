"""One-off 2026 Forever launch reset of raid/character/LFG test data.

This module is deliberately limited to raid_signup.db and lfg_board.db.
It never opens bot.db, which contains Ogre's rolls and coins.
Dry-run is the default; the destructive path requires count-matching guards,
a one-time confirmation string, verified SQLite backups and an intact schema.

Usage on the existing Fly machine (after deployment):
    python -m hyped_ogre.launch_reset --audit
    python -m hyped_ogre.launch_reset --execute --confirm FOREVER-2026-LAUNCH \\
      --expect-characters N --expect-raids N --expect-lfg N
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

EXPECTED_NAMES = ("raid_signup.db", "lfg_board.db")
CONFIRMATION = "FOREVER-2026-LAUNCH"
MARKER = ".hyped_forever_launch_reset_2026.json"
RAID_DATA = (
    "raid_ack_members", "raid_ack_announcements", "raid_group_leaders",
    "raid_invite_posts", "raid_confirmed_roster", "raid_public_roster",
    "raid_absences", "signups", "attendance", "gear_exports", "gear_images",
    "raid_activity_events", "character_activity_events", "character_absences",
    "raids", "characters",
)
LFG_DATA = ("lfg_live_plans", "lfg_dungeon_requests", "lfg_listings")
REQUIRED_RAID = {"raids", "characters", "signups", "gear_exports", "attendance"}
REQUIRED_LFG = {"lfg_boards", "lfg_listings", "lfg_settings"}
PRESERVED_RAID = {"character_panels", "character_panel_parts"}
PRESERVED_LFG = {"lfg_boards", "lfg_board_parts", "lfg_board_overflow", "lfg_settings"}


def _paths(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise RuntimeError(f"Database directory does not exist: {root}")
    raid, lfg = (root / name for name in EXPECTED_NAMES)
    for path in (raid, lfg):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Expected a real, existing SQLite file: {path}")
        if path.resolve().parent != root:
            raise RuntimeError("Refusing any database path outside the selected directory")
    # No reference or connection to bot.db. These filenames are fixed.
    return raid, lfg


def _connect(path, *, readonly=False):
    uri = path.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    db = sqlite3.connect(uri, uri=True, timeout=20, isolation_level=None)
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _tables(db, schema="main"):
    return {
        row[0] for row in db.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _counts(db, data_tables, schema="main"):
    exists = _tables(db, schema)
    return {
        name: db.execute(f'SELECT COUNT(*) FROM {schema}."{name}"').fetchone()[0]
        if name in exists else 0
        for name in data_tables
    }


def _rows(db, query):
    return [tuple(row) for row in db.execute(query)]


def audit(root=Path("/data")):
    raid, lfg = _paths(root)
    with closing(_connect(raid, readonly=True)) as db:
        if not REQUIRED_RAID.issubset(_tables(db)):
            raise RuntimeError("Unexpected raid database schema; no changes made")
        raid_counts = _counts(db, RAID_DATA)
        raid_posts = _rows(db,
            "SELECT raid_id, panel_channel_id, panel_message_id FROM raids "
            "WHERE panel_message_id IS NOT NULL ORDER BY raid_id")
        confirmations = _rows(db,
            "SELECT raid_id, channel_id, message_id FROM raid_ack_announcements "
            "ORDER BY raid_id") if "raid_ack_announcements" in _tables(db) else []
        if _rows(db, "PRAGMA foreign_key_check"):
            raise RuntimeError("Existing raid foreign key violations; no changes made")
    with closing(_connect(lfg, readonly=True)) as db:
        if not REQUIRED_LFG.issubset(_tables(db)):
            raise RuntimeError("Unexpected LFG database schema; no changes made")
        lfg_counts = _counts(db, LFG_DATA)
        boards = _rows(db, "SELECT channel_id, message_id FROM lfg_boards ORDER BY channel_id")
        if _rows(db, "PRAGMA foreign_key_check"):
            raise RuntimeError("Existing LFG foreign key violations; no changes made")
    summary = {
        "characters": raid_counts["characters"],
        "raids": raid_counts["raids"],
        "lfg": sum(lfg_counts.values()),
        "raid_rows": raid_counts,
        "lfg_rows": lfg_counts,
        "raid_discord_posts_to_remove": raid_posts,
        "raid_confirmations_to_remove": confirmations,
        "lfg_boards_to_keep": boards,
        "character_panels": "preserved",
        "lfg_boards": "preserved and switched to live",
        "roll_and_coin_database": "never opened, modified or deleted",
        "google_sheets": "separate cleanup required; NOT touched by this command",
        "discord_messages": "old raid messages NOT deleted by this command",
    }
    return summary


def _backup_sqlite(source, destination):
    # sqlite3.backup gives a consistent image even when the bot is online
    # and the live database uses WAL; file-copying a live .db would not.
    with closing(_connect(source, readonly=True)) as source_db:
        with closing(sqlite3.connect(destination)) as backup_db:
            source_db.backup(backup_db)
            if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
            if _rows(backup_db, "PRAGMA foreign_key_check"):
                raise RuntimeError("Backup has foreign key violations")


def _make_backups(root, raid, lfg):
    needed = 2 * (raid.stat().st_size + lfg.stat().st_size) + 32 * 1024 * 1024
    if shutil.disk_usage(root).free < needed:
        raise RuntimeError("Not enough free volume space for two safe SQLite backups")
    folder = Path(tempfile.mkdtemp(prefix="forever-launch-", dir=root))
    try:
        os.chmod(folder, 0o700)
        for original in (raid, lfg):
            _backup_sqlite(original, folder / original.name)
        return folder
    except Exception:
        shutil.rmtree(folder)
        raise


def execute(root, *, confirm, expected_characters, expected_raids, expected_lfg):
    if confirm != CONFIRMATION:
        raise ValueError("Launch reset confirmation phrase is required")
    expected = (expected_characters, expected_raids, expected_lfg)
    if any(not isinstance(n, int) or n < 0 for n in expected):
        raise ValueError("All three nonnegative audit counts must be supplied")
    root = Path(root).resolve()
    marker = root / MARKER
    if marker.exists():
        raise RuntimeError("The Forever launch reset has already been performed")
    raid, lfg = _paths(root)
    before = audit(root)
    if (before["characters"], before["raids"], before["lfg"]) != expected:
        raise RuntimeError("Live data counts differ from audit: reset aborted")
    backup_folder = _make_backups(root, raid, lfg)
    # Attach the LFG database and use one SQLite write transaction rather
    # than committing the two files separately.
    db = _connect(raid)
    try:
        db.execute("ATTACH DATABASE ? AS lfg", (str(lfg),))
        db.execute("BEGIN IMMEDIATE")
        live_raid = _counts(db, RAID_DATA)
        live_lfg = _counts(db, LFG_DATA, "lfg")
        if live_raid != before["raid_rows"] or live_lfg != before["lfg_rows"]:
            raise RuntimeError("Data changed during backup; reset aborted")
        for table in LFG_DATA:
            if table in _tables(db, "lfg"):
                db.execute(f'DELETE FROM lfg."{table}"')
        # Retain public LFG board IDs, persistent controls, and overflow
        # message IDs so /postlfglfm can refresh the existing Discord posts.
        db.execute(
            "INSERT INTO lfg.lfg_settings(channel_id,mode) "
            "SELECT channel_id,'live' FROM lfg.lfg_boards WHERE 1 "
            "ON CONFLICT(channel_id) DO UPDATE SET mode='live'"
        )
        for table in RAID_DATA:
            if table in _tables(db):
                db.execute(f'DELETE FROM main."{table}"')
        if _rows(db, "PRAGMA main.foreign_key_check"):
            raise RuntimeError("Raid relational integrity check failed")
        if _rows(db, "PRAGMA lfg.foreign_key_check"):
            raise RuntimeError("LFG relational integrity check failed")
        if sum(_counts(db, RAID_DATA).values()) or sum(_counts(db, LFG_DATA, "lfg").values()):
            raise RuntimeError("Reset verification found leftover data")
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "backup_directory": str(backup_folder),
        "before": before,
        "after": audit(root),
        "status": "database reset complete; Discord and Sheets need separate cleanup",
    }
    # A one-off marker blocks accidental repeat wipes after live signups.
    with marker.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--audit", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--expect-characters", type=int)
    parser.add_argument("--expect-raids", type=int)
    parser.add_argument("--expect-lfg", type=int)
    args = parser.parse_args(argv)
    root = Path(os.environ.get("HYPED_RESET_DB_DIRECTORY", "/data"))
    if args.audit:
        print(json.dumps(audit(root), indent=2))
    else:
        result = execute(
            root, confirm=args.confirm, expected_characters=args.expect_characters,
            expected_raids=args.expect_raids, expected_lfg=args.expect_lfg,
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
