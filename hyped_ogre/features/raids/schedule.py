"""Raid schedule storage and public-facing Components V2 raid panel.

An event is configured in the private testing channel for now. Sheets-to-bot
synchronisation is separate: the Google Sheet does not authorise a Fly bot.
"""
import sqlite3
import re
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import discord
from cogs.emojis import markup as guild_emoji, combat_role
from hyped_ogre.features.classrooms.config import GUILD_ID

UK = ZoneInfo("Europe/London")
RAID_IMAGE = "https://i.vgy.me/XIszIH.png"  # Poster thumbnail stays unchanged.
RAID_PANEL_IMAGE = "https://i.vgy.me/asI88C.png"
ACCENT = 0xA87535  # Keep the private raid poster setup colour.
RAID_PANEL_ACCENT = 0xB84A3A  # Public raid panel side colour.
DEFAULT_CHECK_CHANNEL_ID = 1563663258047619123
CREATOR_CROWN = "<:ogrecrown:1566947968760946948>"
_UNCHANGED = object()  # Older /raid setup calls must keep poster-specific settings.
RAID_COLUMNS = {
    "instance_1": "TEXT",
    "instance_2": "TEXT",
    "instance_3": "TEXT",
    "roster_announce_at": "TEXT",
    "panel_message_id": "INTEGER",
    "panel_channel_id": "INTEGER",
    "softres_url": "TEXT",
    "custom_title": "TEXT",
    "banner_url": "TEXT",
    "end_at": "TEXT",
    "check_channel_id": "INTEGER",  # Old setting retained for migration.
    "check_message_url": "TEXT",
    "roster_posted_at": "TEXT",
    "roster_updated_at": "TEXT",
    "confirmed_at": "TEXT",
    "archived_at": "TEXT",
    "archive_notes": "TEXT",
    "wcl_url": "TEXT",
    "wcl_sheet_synced": "TEXT",
    "raid_notes_sheet_synced": "TEXT",
    "default_show_discord": "INTEGER NOT NULL DEFAULT 1",
    # 'missing' is the default for new and pre-existing raids.
    "wowsims_policy": "TEXT NOT NULL DEFAULT 'missing'",
    "wowsims_refresh_after": "TEXT",
}


@contextmanager
def database(path):
    """Commit or roll back SQL and always close the connection (Windows-safe)."""
    con = sqlite3.connect(path, timeout=20)
    try:
        with con:
            yield con
    finally:
        con.close()


def parse_uk_datetime(value):
    """Interpret an explicit YYYY-MM-DD HH:MM local UK clock time."""
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError("Use YYYY-MM-DD HH:MM (UK time), e.g. 2026-11-05 20:00.") from exc
    aware = naive.replace(tzinfo=UK)
    if aware.astimezone(timezone.utc).astimezone(UK).replace(tzinfo=None) != naive:
        raise ValueError("That UK time does not exist because of a clock change.")
    return aware


def format_discord_date(value, *, style="F", with_relative=True):
    """Discord full date or relative countdown, not both unless requested."""
    if not value:
        return "To be confirmed"
    if style not in ("F", "R", "D", "t"):
        raise ValueError("Unsupported Discord date style.")
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("Raid times must have a timezone.")
    timestamp = int(instant.timestamp())
    display = f"<t:{timestamp}:{style}>"
    if with_relative and style != "R":
        return display + f" · <t:{timestamp}:R>"
    return display


def validate_check_message_url(value):
    """A shortened Check link must point to a Discord message."""
    if not value or not value.strip():
        return None
    url = value.strip()
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "discord.com"
            or parts.username or parts.password or parts.port
            or not re.fullmatch(r"/channels/\d{16,20}/\d{16,20}/\d{16,20}/?", parts.path)):
        raise ValueError("Use a Discord message permalink, not a channel link.")
    return "https://discord.com" + parts.path.rstrip("/")


def ensure_raid_fields(path):
    """Add new columns without clearing any existing sign-ups or characters."""
    with database(path) as con:
        names = {row[1] for row in con.execute("PRAGMA table_info(raids)")}
        if not names:
            raise RuntimeError("Raid schema must be created before schedule migration.")
        for field, sql_type in RAID_COLUMNS.items():
            if field not in names:
                con.execute(f"ALTER TABLE raids ADD COLUMN {field} {sql_type}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS raid_confirmed_roster (
                raid_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                discord_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                assignment TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (raid_id, position)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS raid_ack_announcements (
                raid_id INTEGER PRIMARY KEY REFERENCES raids(raid_id),
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                needs_refresh INTEGER NOT NULL DEFAULT 0,
                retracted_at TEXT,
                deleted_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS raid_ack_members (
                raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
                discord_id TEXT NOT NULL,
                character TEXT NOT NULL,
                position INTEGER NOT NULL,
                acknowledged_at TEXT,
                PRIMARY KEY(raid_id, discord_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS raid_group_leaders (
                raid_id INTEGER NOT NULL REFERENCES raids(raid_id),
                group_name TEXT NOT NULL,
                discord_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (raid_id, group_name)
            )
        """)
        ack_cols = {row[1] for row in con.execute(
            "PRAGMA table_info(raid_ack_announcements)"
        )}
        if "needs_refresh" not in ack_cols:
            con.execute("ALTER TABLE raid_ack_announcements "
                        "ADD COLUMN needs_refresh INTEGER NOT NULL DEFAULT 0")
        if "retracted_at" not in ack_cols:
            con.execute("ALTER TABLE raid_ack_announcements "
                        "ADD COLUMN retracted_at TEXT")
        if "deleted_at" not in ack_cols:
            con.execute("ALTER TABLE raid_ack_announcements "
                        "ADD COLUMN deleted_at TEXT")
        # Queue cleanup for earlier completed raids as well, including the
        # test raid completed before this change was deployed. A deleted_at
        # marker makes this restart-safe and prevents repeated delete calls.
        con.execute(
            "UPDATE raid_ack_announcements SET needs_refresh=1 "
            "WHERE deleted_at IS NULL AND raid_id IN "
            "(SELECT raid_id FROM raids WHERE archived_at IS NOT NULL)"
        )
        # Older confirmations used to close registration; migrate active raids.
        con.execute("UPDATE raids SET signup_open=1 "
                    "WHERE roster_published=1 AND archived_at IS NULL")
        # Upgrade existing active reaction posts to the V1 grid on restart.
        # Editing uses AllowedMentions.none() and never sends another ping.
        con.execute(
            "UPDATE raid_ack_announcements SET needs_refresh=1 "
            "WHERE deleted_at IS NULL AND retracted_at IS NULL "
            "AND raid_id IN (SELECT raid_id FROM raids "
            "WHERE archived_at IS NULL AND roster_published=1)"
        )
        for table in ("raid_confirmed_roster", "raid_public_roster"):
            existing_columns = {
                row[1] for row in con.execute(f"PRAGMA table_info({table})")
            }
            for column in ("class_name", "role", "spec", "discord_id"):
                if column not in existing_columns:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def validate_softres_url(value):
    """Only display a public HTTPS SoftRes raid URL when one is provided."""
    if not value or not value.strip():
        return None
    url = value.strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid SoftRes HTTPS link.") from exc
    if (len(url) > 1500 or parts.scheme != "https"
            or not (host == "softres.it" or host.endswith(".softres.it"))
            or parts.username is not None or parts.password is not None
            or not parts.path.startswith("/raid/")):
        raise ValueError("Paste a https://softres.it/raid/... link.")
    return url


def validate_wcl_url(value):
    """Accept a real HTTPS Warcraft Logs report URL, including Classic hosts."""
    if not value or not str(value).strip():
        return None
    url = str(value).strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid Warcraft Logs report URL.") from exc
    if (len(url) > 500 or parts.scheme != "https"
            or not (host == "warcraftlogs.com" or host.endswith(".warcraftlogs.com"))
            or not parts.path.startswith("/reports/")
            or not parts.path[len("/reports/"):].strip("/")
            or parts.username is not None or parts.password is not None
            or port is not None
            or any(ch.isspace() or ch in "<>[]()" for ch in url)):
        raise ValueError("Use an HTTPS Warcraft Logs report link (…/reports/…).")
    return url


def set_wcl_url(path, number, value):
    """Update only the raid log link, without reopening or changing the roster."""
    url = validate_wcl_url(value) or ""
    with database(path) as con:
        existing = con.execute(
            "SELECT COALESCE(wcl_url, '') FROM raids WHERE raid_id=?", (number,)
        ).fetchone()
        if existing is None:
            raise ValueError("Raid does not exist.")
        if existing[0] == url:
            return False
        con.execute("UPDATE raids SET wcl_url=? WHERE raid_id=?", (url, number))
    return True


def validate_banner_url(value):
    """Allow an optional public HTTPS direct image link; blank means no image."""
    if not value or not value.strip():
        return None
    url = value.strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTPS image URL.") from exc
    if (len(url) > 512 or parts.scheme != "https" or not host
            or host in {"localhost", "127.0.0.1", "::1"}
            or parts.username is not None or parts.password is not None
            or not parts.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
        raise ValueError("Use a direct HTTPS image URL ending in .png, .jpg, .webp or .gif.")
    return url


def set_softres_url(path, number, url):
    """Change just the SoftRes link; leave the raid event/roster untouched."""
    value = validate_softres_url(url)
    with database(path) as con:
        cursor = con.execute(
            "UPDATE raids SET softres_url=? WHERE raid_id=?", (value, number)
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Raid #{number} not found. Use /raid setup first.")
    return read_raid(path, number)


def save_raid(path, number, raid_date, instance_1, instance_2=None,
              instance_3=None, roster_announce=None, softres_url=None,
              custom_title=None, banner_url=None,
              end_at=_UNCHANGED, check_channel_id=_UNCHANGED,
              check_message_url=_UNCHANGED, wowsims_policy=_UNCHANGED):
    if wowsims_policy is not _UNCHANGED and wowsims_policy not in (
            "missing", "fresh", "optional"):
        raise ValueError("Choose a valid WoWSims requirement.")
    softres = validate_softres_url(softres_url)
    if not 1 <= number <= 100000:
        raise ValueError("Raid number must be between 1 and 100000.")
    start = parse_uk_datetime(raid_date)
    if end_at is not _UNCHANGED and end_at is not None:
        if isinstance(end_at, str):
            end = datetime.fromisoformat(end_at)
        elif isinstance(end_at, datetime):
            end = end_at
        else:
            raise ValueError("Raid end time must be a datetime.")
        if end.tzinfo is None:
            raise ValueError("Raid end time must include a timezone.")
        seconds = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
        if not 0 < seconds <= 12 * 3600:
            raise ValueError("Raid end time must be after the start, within 12 hours.")
        end_at = end.astimezone(UK).isoformat()
    if check_channel_id is not _UNCHANGED:
        if isinstance(check_channel_id, bool) or not str(check_channel_id).isdigit():
            raise ValueError("Choose a valid Discord text channel for Check.")
        check_channel_id = int(check_channel_id)
        if not 0 < check_channel_id < 2**64:
            raise ValueError("Choose a valid Discord text channel for Check.")
    if check_message_url is not _UNCHANGED:
        check_message_url = validate_check_message_url(check_message_url)
    announce = parse_uk_datetime(roster_announce) if roster_announce else None
    instances = [(part or "").strip() for part in (instance_1, instance_2, instance_3)]
    if not instances[0] or any(len(part) > 80 for part in instances):
        raise ValueError("Enter an instance #1 (80 characters max per instance).")
    if instances[2] and not instances[1]:
        raise ValueError("Add instance #2 before instance #3.")
    if announce and announce > start:
        raise ValueError("Roster announcement must not be after the raid starts.")
    # None preserves the title when older /raid setup workflows edit an event.
    # An explicitly blank title from the poster removes the optional suffix.
    with database(path) as con:
        existing_state = con.execute(
            "SELECT roster_published, archived_at FROM raids WHERE raid_id=?",
            (number,)
        ).fetchone()
        if existing_state and existing_state[1]:
            raise ValueError("Archived raids are read-only.")
        if custom_title is None:
            existing = con.execute(
                "SELECT custom_title FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            custom_title = existing[0] if existing and existing[0] else ""
        if banner_url is None:
            existing_banner = con.execute(
                "SELECT banner_url FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            banner_url = existing_banner[0] if existing_banner else None
        banner_url = validate_banner_url(banner_url)
        if end_at is _UNCHANGED:
            current = con.execute(
                "SELECT raid_at, end_at FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            end_at = current[1] if current else None
            if end_at and current[0] != start.isoformat():
                # Legacy start-time edits keep the same custom raid duration,
                # rather than pointing to an end on the old calendar date.
                original_start = datetime.fromisoformat(current[0])
                original_end = datetime.fromisoformat(end_at)
                duration = (original_end.astimezone(timezone.utc) -
                            original_start.astimezone(timezone.utc))
                end_at = (start.astimezone(timezone.utc) +
                          duration).astimezone(UK).isoformat()
        if check_channel_id is _UNCHANGED:
            current = con.execute(
                "SELECT check_channel_id FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            check_channel_id = current[0] if current else None
        if check_message_url is _UNCHANGED:
            previous_check = con.execute(
                "SELECT check_message_url FROM raids WHERE raid_id=?", (number,)
            ).fetchone()
            check_message_url = previous_check[0] if previous_check else None
        if not isinstance(custom_title, str):
            raise ValueError("Optional title must be text.")
        custom_title = " ".join(custom_title.split())
        if len(custom_title) > 70:
            raise ValueError("Optional raid title must be 70 characters or less.")
        full_title = f"<Hyped> Raid #{number}"
        if custom_title:
            full_title += f" - {custom_title}"
        con.execute("""
            INSERT INTO raids(raid_id, title, raid_at, instance_1, instance_2,
                              instance_3, roster_announce_at, softres_url,
                              signup_open, custom_title, banner_url,
                              end_at, check_channel_id, check_message_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(raid_id) DO UPDATE SET
                title=excluded.title, raid_at=excluded.raid_at,
                instance_1=excluded.instance_1, instance_2=excluded.instance_2,
                instance_3=excluded.instance_3,
                roster_announce_at=excluded.roster_announce_at,
                softres_url=COALESCE(excluded.softres_url, raids.softres_url),
                custom_title=excluded.custom_title,
                banner_url=excluded.banner_url,
                end_at=excluded.end_at,
                check_channel_id=excluded.check_channel_id,
                check_message_url=excluded.check_message_url,
                signup_open=CASE WHEN raids.archived_at IS NOT NULL
                       THEN 0 ELSE 1 END
        """, (number, full_title, start.isoformat(),
              instances[0], instances[1] or None, instances[2] or None,
              announce.isoformat() if announce else None,
              softres, custom_title, banner_url, end_at,
              check_channel_id, check_message_url))
        if wowsims_policy is not _UNCHANGED:
            # A fresh export is one submitted AFTER the policy was enabled.
            # Re-saving the poster must not move this deadline.
            current = con.execute(
                "SELECT wowsims_policy, wowsims_refresh_after FROM raids "
                "WHERE raid_id=?", (number,)
            ).fetchone()
            previous_policy, refresh_after = current
            if wowsims_policy == "fresh":
                if previous_policy != "fresh" or not refresh_after:
                    refresh_after = datetime.now(timezone.utc).isoformat()
            else:
                refresh_after = None
            con.execute(
                "UPDATE raids SET wowsims_policy=?, wowsims_refresh_after=? "
                "WHERE raid_id=?",
                (wowsims_policy, refresh_after, number),
            )
    return read_raid(path, number)


def read_raid(path, number):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM raids WHERE raid_id=?", (number,)).fetchone()
        if row is None:
            return None
        event = dict(row)
        # The public confirmation link must be Ogre's actual reaction message,
        # not the independent Check link manually supplied in poster settings.
        if event.get("roster_published") and not event.get("archived_at"):
            ack = con.execute(
                "SELECT channel_id, message_id FROM raid_ack_announcements "
                "WHERE raid_id=? AND retracted_at IS NULL AND deleted_at IS NULL",
                (number,),
            ).fetchone()
            if ack:
                event["confirmation_url"] = (
                    f"https://discord.com/channels/{GUILD_ID}/"
                    f"{ack['channel_id']}/{ack['message_id']}"
                )
        return event


def latest_raid(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM raids ORDER BY raid_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def list_raids(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            "SELECT * FROM raids ORDER BY raid_id DESC LIMIT 24"
        )]


def active_panels(path):
    with database(path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(
            "SELECT * FROM raids WHERE panel_message_id IS NOT NULL"
        )]


def delete_raid(path, number):
    """Delete one raid and its applications/attendance, not character or SimC data.

    Explicit dependent-row deletion makes this safe even on SQLite connections
    where foreign key enforcement is enabled, while retaining all other raids.
    """
    with database(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        event = con.execute(
            "SELECT raid_id FROM raids WHERE raid_id=?", (number,)
        ).fetchone()
        if not event:
            raise ValueError(f"Raid #{number} no longer exists.")
        con.execute("DELETE FROM signups WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_absences WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_public_roster WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_ack_members WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_ack_announcements WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raid_group_leaders WHERE raid_id=?", (number,))
        con.execute("DELETE FROM attendance WHERE raid_id=?", (number,))
        con.execute("DELETE FROM raids WHERE raid_id=?", (number,))
    return True

def raid_status(path, number, mode, *, confirmed_entries=(), notes=None, wcl_url=None):
    """Explicit leadership lifecycle: confirm, reopen, archive; no auto-confirm."""
    if mode not in ("confirm", "reopen", "archive"):
        raise ValueError("Unknown raid status transition.")
    now = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        event = con.execute(
            "SELECT roster_published, archived_at FROM raids WHERE raid_id=?",
            (number,)
        ).fetchone()
        if not event:
            raise ValueError("Raid does not exist.")
        if event[1]:
            raise ValueError("Archived raids are read-only.")
        if mode == "confirm":
            if not confirmed_entries:
                raise ValueError("Select and show at least one player before confirming.")
            con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
            con.executemany(
                "INSERT INTO raid_confirmed_roster "
                "(raid_id, position, discord_id, name, assignment, class_name, role, spec) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(number, index, str(entry[0] or ""), entry[1], entry[2],
                  *tuple(entry[3:6]) + ("",) * (3 - len(entry[3:6])))
                 for index, entry in enumerate(confirmed_entries)],
            )
            con.execute(
                "UPDATE raids SET roster_published=1, signup_open=1, "
                "confirmed_at=COALESCE(confirmed_at, ?), "
                "roster_posted_at=COALESCE(roster_posted_at, ?), roster_updated_at=NULL "
                "WHERE raid_id=?", (now, now, number)
            )
        elif mode == "reopen":
            con.execute(
                "UPDATE raids SET roster_published=0, signup_open=1, "
                "confirmed_at=NULL, roster_posted_at=NULL, roster_updated_at=NULL "
                "WHERE raid_id=?", (number,)
            )
            con.execute("DELETE FROM raid_confirmed_roster WHERE raid_id=?", (number,))
        else:
            if not event[0]:
                raise ValueError("Confirm the raid roster before archiving.")
            con.execute(
                "UPDATE raids SET archived_at=?, "
                "archive_notes=COALESCE(NULLIF(?, ''), archive_notes), "
                "wcl_url=COALESCE(NULLIF(?, ''), wcl_url), "
                "signup_open=0 WHERE raid_id=?",
                (now, notes or "", validate_wcl_url(wcl_url) or "", number),
            )
            # Schedule deletion of only the separate roster acknowledgement
            # announcement. Retain its DB record for audit and retry; the
            # original sign-up V2 panel must never be deleted on completion.
            con.execute("UPDATE raid_ack_announcements SET needs_refresh=1 "
                        "WHERE raid_id=?", (number,))
    return read_raid(path, number)


def confirmed_roster(path, number, unavailable=(), *, include_details=False):
    """Last explicitly approved snapshot for attendance and historical use."""
    unavailable = {str(value) for value in unavailable}
    with database(path) as con:
        rows = con.execute(
            "SELECT discord_id, name, assignment, class_name, role, spec "
            "FROM raid_confirmed_roster WHERE raid_id=? ORDER BY position", (number,)
        ).fetchall()
    return [(name, assignment, klass or "", role or "", spec or "")
            if include_details else (name, assignment)
            for member_id, name, assignment, klass, role, spec in rows
            if not member_id or member_id not in unavailable]


def read_public_roster(path, raid_id, *, include_details=False, include_identity=False):
    """Confirmed team entries; optional class/role metadata for emojis."""
    with database(path) as con:
        columns = ("name, assignment, class_name, role, spec"
                   if include_details or include_identity else "name, assignment")
        if include_identity:
            columns += ", COALESCE(discord_id,'')"
        return con.execute(
            f"SELECT {columns} FROM raid_public_roster "
            "WHERE raid_id=? ORDER BY position", (raid_id,)
        ).fetchall()


def read_signup_roster(path, raid_id, *, include_identity=False):
    """Read live public sign-ups while respecting each player's visibility.

    The Sheet's Show on Discord flag is cached per raid signup after sync.
    Until then, a NULL flag inherits the raid poster's default. Leadership's
    selection and instance assignments stay private before confirmation.
    An available, visible sign-up appears immediately, without waiting for
    roster confirmation. One character per Discord member/night.
    """
    with database(path) as con:
        identity = ", CAST(c.discord_id AS TEXT) " if include_identity else " "
        return con.execute(
            "SELECT c.name, '' AS assignment, "
            "COALESCE(c.class_name, ''), COALESCE(c.role, ''), "
            "COALESCE(c.spec, '')" + identity +
            "FROM signups AS s JOIN characters AS c "
            "ON c.character_id=s.character_id AND c.discord_id=s.discord_id "
            "JOIN raids AS r ON r.raid_id=s.raid_id "
            "WHERE s.raid_id=? AND s.availability='Available' "
            "AND COALESCE(s.show_on_discord, r.default_show_discord, 1)=1 "
            "AND NOT EXISTS (SELECT 1 FROM raid_absences AS a "
            "WHERE a.raid_id=s.raid_id AND a.discord_id=s.discord_id) "
            "ORDER BY s.submitted_at, LOWER(c.name)",
            (raid_id,),
        ).fetchall()


def signup_role_group(klass, spec, role):
    """Classify unassigned sign-ups using the same melee/ranged split as Sheets."""
    role = (role or "").strip().casefold()
    if role == "tank":
        return "Tanks"
    if role in ("healer", "heal"):
        return "Healers"
    klass = (klass or "").strip().casefold()
    spec = (spec or "").strip().casefold()
    if (klass in ("warrior", "rogue")
            or (klass, spec) in {
                ("paladin", "retribution"), ("druid", "feral"),
                ("shaman", "enhancement"), ("hunter", "survival")
            }):
        return "Melee"
    return "Ranged"


def _ordered_roster(public_roster):
    """Match Sheet owner/class/name ordering when member identity is available."""
    entries = list(public_roster)
    if not any(len(entry) > 5 for entry in entries):
        return entries  # Preserve the historical tuple-only helper API.
    owner = os.getenv("OWNER_ID", "0").strip()
    return sorted(entries, key=lambda entry: (
        0 if owner not in ("", "0") and len(entry) > 5 and str(entry[5]) == owner else 1,
        (entry[2] or "").casefold() if len(entry) > 2 else "", entry[0].casefold(),
    ))


def signup_role_groups(public_roster, *, emoji_map=None):
    """Four public sign-up categories; no instance or selection disclosure."""
    groups = {label: [] for label in ("Tanks", "Melee", "Ranged", "Healers")}
    for entry in _ordered_roster(public_roster):
        name = entry[0]
        klass, role, spec = (tuple(entry[2:5]) + ("", "", ""))[:3]
        display = roster_name_with_emojis(
            name, klass, role, spec, emoji_map=emoji_map,
            member_id=entry[5] if len(entry) > 5 else "",
        )
        if display:
            groups[signup_role_group(klass, spec, role)].append(display)
    return [(name, members) for name, members in groups.items() if members]


def record_panel(path, number, channel_id, message_id):
    with database(path) as con:
        con.execute(
            "UPDATE raids SET panel_channel_id=?, panel_message_id=? WHERE raid_id=?",
            (channel_id, message_id, number),
        )


def _split_from_tokens(tokens):
    """New diamonds and historic Group A/B Sheet values map to two Barrows teams."""
    if {"barrows 🔹", "group b", "🔹", "barrows b"} & tokens:
        return "🔹"
    return "🔸"  # Default Barrows group when a specific split is not supplied.


def roster_name_with_emojis(name, klass="", role="", spec="", emoji_map=None, *, member_id=""):
    """Show fixed custom Discord emoji markup; preserve existing API."""
    name = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
    if not name:
        return ""
    klass = (klass or "").strip().title()
    role = combat_role((role or "").strip().title(), klass, spec)
    owner = os.getenv("OWNER_ID", "0").strip()
    crown = CREATOR_CROWN if owner not in ("", "0") and str(member_id) == owner else ""
    return " ".join(part for part in (guild_emoji(klass), name, crown) if part)


def roster_groups(event, public_roster, *, emoji_map=None, by_role=False):
    """Hyjal uses one 20-player roster; Barrows retains two diamond teams.

    Only leadership-approved names are passed in from the Sheet. One raider
    may appear in the Hyjal roster and one Barrows team on the same raid night.
    """
    instances = [
        (event.get(key) or "").casefold()
        for key in ("instance_1", "instance_2", "instance_3")
    ]
    has_hyjal = any("hyjal" in name for name in instances)
    has_barrows = any("barrows" in name for name in instances)
    has_ony = any("ony" in name for name in instances)

    group_titles = []
    if has_hyjal:
        group_titles.append("Hyjal · 20-player")
    if has_barrows:
        group_titles.extend(("Barrows · 10-player 🔸", "Barrows · 10-player 🔹"))
    if has_ony:
        group_titles.append("Ony · 40-player")
    if not group_titles:
        group_titles.append("Selected players")

    groups = {title: [] for title in group_titles}
    pending = []
    for entry in _ordered_roster(public_roster):
        name, assignment = entry[:2]
        klass, role, spec = (tuple(entry[2:5]) + ("", "", ""))[:3]
        clean_name = roster_name_with_emojis(
            name, klass, role, spec, emoji_map=emoji_map,
            member_id=entry[5] if len(entry) > 5 else "",
        )
        if not clean_name:
            continue
        if by_role:
            if by_role == "four":
                # V1 roster announcements use the same four roles as sign-ups;
                # the live V2 raid panel keeps its existing three-role layout.
                role_group = signup_role_group(klass, spec, role)
            else:
                role_group = (role or "").strip().casefold()
                role_group = ("Tank" if role_group == "tank"
                              else "Healer" if role_group in ("healer", "heal")
                              else "DPS")
            clean_name = (role_group, clean_name)
        tokens = {item.strip().casefold() for item in (assignment or "").split(",")}
        split = _split_from_tokens(tokens)
        matched = False
        if has_hyjal and "hyjal" in tokens:
            groups["Hyjal · 20-player"].append(clean_name)
            matched = True
        if has_barrows and (
            {"barrows 🔸", "barrows 🔹", "barrows a", "barrows b",
             "group a", "group b", "🔸", "🔹"} & tokens
        ):
            groups[f"Barrows · 10-player {split}"].append(clean_name)
            matched = True
        if has_ony and "ony" in tokens:
            groups["Ony · 40-player"].append(clean_name)
            matched = True
        if "Selected players" in groups:
            groups["Selected players"].append(clean_name)
            matched = True
        if not matched:
            pending.append(clean_name)
    result = list(groups.items())
    if pending:
        result.append(("Selected · group pending", pending))
    return result


def raid_panel_when(value, end_at=None):
    """Native Discord date and local, hoverable start/end times."""
    start = datetime.fromisoformat(value).astimezone(UK)
    end = (datetime.fromisoformat(end_at).astimezone(UK) if end_at else
           (start.astimezone(timezone.utc) + timedelta(hours=3)).astimezone(UK))
    return (
        f"{format_discord_date(start.isoformat(), style='D', with_relative=False)}"
        f" · {format_discord_date(start.isoformat(), style='t', with_relative=False)}"
        f" - {format_discord_date(end.isoformat(), style='t', with_relative=False)}"
    )

def raid_panel_intro(event):
    """Compact header; raid times are UK, announcement timestamps localise."""
    number = event["raid_id"]
    # Escape only the user-provided suffix; keep the guild prefix stable.
    title = f"<Hyped> Raid #{number}"
    if event.get("custom_title"):
        suffix = discord.utils.escape_mentions(
            discord.utils.escape_markdown(event["custom_title"])
        )
        title += f" - {suffix}"
    instances = [
        discord.utils.escape_mentions(discord.utils.escape_markdown(event[key]))
        for key in ("instance_1", "instance_2", "instance_3")
        if event.get(key)
    ]
    softres = event.get("softres_url")
    softres_line = f"\n[SoftRes sign-up]({softres})" if softres else ""
    wcl = event.get("wcl_url")
    wcl_line = f"\n**Warcraft Logs:** [View report]({wcl})" if wcl else ""
    wse_policy = event.get("wowsims_policy") or "missing"
    wse_label = {
        "missing": "Required if character has no export",
        "fresh": "Fresh export required",
        "optional": "Optional",
    }.get(wse_policy, "Required if character has no export")
    # WoWSims instructions belong to collecting sign-ups, not an announced
    # roster. The roster confirmation link is only Ogre's own reaction post.
    wse_line = (
        (f"\n**WoWSims:** {wse_label} · "
         "[Get addon](https://www.curseforge.com/wow/addons/wowsims-exporter)")
        if not event.get("roster_published") else ""
    )
    confirmation = event.get("confirmation_url") if event.get("roster_published") else None
    return (
        f"## {title}\n"
        f"{raid_panel_when(event['raid_at'], event.get('end_at'))}\n"
        + "**Instances:** " + ", ".join(instances)
        + wse_line
        + (f"\n**Required:** [Confirm raid spot]({confirmation})"
           if confirmation else "")
        + wcl_line + softres_line
    )


def raid_roster_status(event):
    """Only an explicit leadership confirmation is a published roster."""
    if event.get("archived_at"):
        return "**Archived:** " + format_discord_date(
            event["archived_at"], style="F", with_relative=False
        )
    if not event.get("roster_published"):
        return "**Roster confirmation:** " + format_discord_date(
            event.get("roster_announce_at"), style="R", with_relative=False
        )
    stamp = event.get("roster_updated_at")
    if stamp:
        return "**Update:** " + format_discord_date(stamp, style="F", with_relative=False)
    stamp = event.get("roster_posted_at")
    if stamp:
        return "**Roster confirmed:** " + format_discord_date(
            stamp, style="F", with_relative=False
        )
    return "**Roster confirmed:** To be confirmed"


def roster_group_heading(title):
    """Use the same instance/diamond headings in previews and public rosters."""
    if title.startswith("Barrows · 10-player "):
        return f"**Barrows** {title[-1]}"
    if title == "Hyjal · 20-player":
        return "**Hyjal** 🔹🔸"
    if title == "Ony · 40-player":
        return "**Ony**"
    return f"**{title}**"


def _roster_group_text(title, players):
    """Always show muted Tanks / Healers / DPS placeholders per raid roster."""
    heading = roster_group_heading(title)
    for instance in ("Hyjal", "Barrows", "Ony"):
        heading = heading.replace(f"**{instance}**", f"**{instance} ({len(players)})**")
    lines = [heading]
    for label, category in (("Tanks", "Tank"), ("Healers", "Healer"), ("DPS", "DPS")):
        matching = ([name for player_role, name in players if player_role == category]
                    if players and all(isinstance(item, tuple) for item in players)
                    else players if category == "DPS" else [])
        lines.append(f"-# **{label} ({len(matching)})**")
        lines.extend("-# " + name for name in matching)
    return "\n".join(lines)


def raid_panel_text(event, public_roster=(), *, emoji_map=None):
    """Plain-text preview for each roster lifecycle stage."""
    if not public_roster:
        return raid_panel_intro(event) + "\n\n" + raid_roster_status(event)
    if not event.get("roster_published"):
        groups = signup_role_groups(public_roster, emoji_map=emoji_map)
        lines = ["**Sign-ups (" + str(len(public_roster)) + ")**"]
        for role, players in groups:
            lines.append("-# **" + role + " (" + str(len(players)) + ")**\n" +
                         "\n".join("-# " + player for player in players))
        return raid_panel_intro(event) + "\n\n" + "\n\n".join(lines) + (
            "\n\n" + raid_roster_status(event))
    groups = roster_groups(event, public_roster, emoji_map=emoji_map, by_role=True)
    return raid_panel_intro(event) + "\n### Roster" + "".join(
        "\n\n" + _roster_group_text(title, players)
        for title, players in groups
    ) + "\n\n" + raid_roster_status(event)


def roster_components(event, public_roster, *, emoji_map=None):
    """Role-grouped live sign-ups before confirmation; team rosters afterwards."""
    if not public_roster:
        return []
    if not event.get("roster_published"):
        return [
            discord.ui.TextDisplay(
                "-# **" + role + " (" + str(len(players)) + ")**\n" +
                "\n".join("-# " + player for player in players))
            for role, players in signup_role_groups(
                public_roster, emoji_map=emoji_map)
        ]
    return [
        discord.ui.TextDisplay(_roster_group_text(title, players))
        for title, players in roster_groups(
            event, public_roster, emoji_map=emoji_map, by_role=True)
    ]


class RaidEventPanel(discord.ui.LayoutView):
    """Real Discord V2 message, persistent across bot restarts."""
    def __init__(self, event, *, on_register=None, on_characters=None, on_signup=None,
                 on_gear=None, on_update=None, on_absent=None,
                 public_roster=(), emoji_map=None, on_remind=None):
        super().__init__(timeout=None)
        event_id = event["raid_id"]
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(raid_panel_intro(event)),
                accessory=discord.ui.Thumbnail(
                    RAID_PANEL_IMAGE, description="Hyped raid image"
                ),
            ),
            *( [discord.ui.TextDisplay(
                   "### Roster" if event.get("roster_published") else
                   f"**Sign-ups ({len(public_roster)})**"
               )] + roster_components(event, public_roster, emoji_map=emoji_map)
               if public_roster else [] ),
            discord.ui.TextDisplay(raid_roster_status(event)),
            *(
                [discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        media=event["banner_url"],
                        description="Hyped raid bottom banner",
                    ),
                )]
                if event.get("banner_url") else []
            ),
            accent_color=RAID_PANEL_ACCENT,
        ))
        controls = discord.ui.ActionRow()
        sign_up = discord.ui.Button(
            label="Attend", custom_id=f"hyped_raid_{event_id}_signup",
            style=discord.ButtonStyle.success,
            disabled=not bool(event.get("signup_open")) or bool(event.get("archived_at")),
        )
        update = discord.ui.Button(
            label="Characters", custom_id=f"hyped_raid_{event_id}_update",
            style=discord.ButtonStyle.secondary,
            disabled=on_update is None,
        )
        gear = discord.ui.Button(
            label="WoWSims", custom_id=f"hyped_raid_{event_id}_gear",
            style=discord.ButtonStyle.secondary,
            disabled=on_gear is None or bool(event.get("archived_at")),
        )
        absent = discord.ui.Button(
            label="Absent", custom_id=f"hyped_raid_{event_id}_absent",
            style=discord.ButtonStyle.danger,
            disabled=on_absent is None or bool(event.get("archived_at")),
        )
        if on_signup is not None:
            sign_up.callback = on_signup
        else:
            sign_up.disabled = True
        if on_update is not None:
            update.callback = on_update
        if on_gear is not None:
            gear.callback = on_gear
        if on_absent is not None:
            absent.callback = on_absent
        reminder = discord.ui.Button(
            emoji="🔔",
            custom_id=f"hyped_raid_{event_id}_reminder",
            style=discord.ButtonStyle.secondary,
            disabled=on_remind is None or bool(event.get("archived_at")),
        )
        if on_remind is not None:
            reminder.callback = on_remind
        for control in (sign_up, reminder, update, gear, absent):
            controls.add_item(control)
        self.add_item(controls)
