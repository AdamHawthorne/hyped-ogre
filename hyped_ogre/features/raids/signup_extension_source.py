"""Private raid management: local database and optional Google Sheets sync."""
import os
import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import closing, contextmanager

import discord
from discord import app_commands
from discord.ext import commands

from cogs import raid_sheets, raid_sheets_v3, raid_announcer
from cogs.emojis import markup as guild_emoji, option_emoji, combat_role, EMOJI_IDS
from cogs.forever_races import RACE_CLASSES, STARTING_ZONES
from cogs.countdown import toggle_raid_dm
from cogs.raid_poster import RaidPosterView
from hyped_ogre.features.raids.signup_schema import ensure_signup_schema_on_connection
from hyped_ogre.features.characters import queries as character_queries
from hyped_ogre.features.characters import mutations as character_mutations
from hyped_ogre.features.raids import signup_queries
from hyped_ogre.features.raids import signup_mutations
from hyped_ogre.features.raids.leaders import set_group_leader, GROUPS
from hyped_ogre.features.raids import invite_reminders
from hyped_ogre.features.raids.signup_reference import (
    SHEET_TABS, ATTENDANCE_OPTIONS, CLASS_SPECS,
)
from cogs.raid_schedule import (
    RaidEventPanel, active_panels, ensure_raid_fields, latest_raid, list_raids,
    read_raid, read_public_roster, read_signup_roster, record_panel, save_raid, set_softres_url, delete_raid,
    raid_status, validate_wcl_url, format_discord_date,
)

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "0"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TEST_CHANNEL_ID = 1564594901746716713
RAID_CHANNEL_ID = 1563663151323553802
RAID_DB = os.getenv("RAID_DB_PATH", "/data/raid_signup.db" if os.path.isdir("/data") else "raid_signup.db")
ACCENT = 0xA87535
LOGGER = logging.getLogger(__name__)
WOWSIMS_ADDON_URL = "https://www.curseforge.com/wow/addons/wowsims-exporter"

@contextmanager
def database(path=RAID_DB):
    con = sqlite3.connect(path, timeout=20)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        with con:
            yield con
    finally:
        con.close()


def create_schema(path=RAID_DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with database(path) as con:
        ensure_signup_schema_on_connection(con)


def is_test_owner(interaction):
    return (interaction.user.id == OWNER_ID and interaction.guild_id == GUILD_ID
            and interaction.channel_id == TEST_CHANNEL_ID)

def is_test_member(interaction):
    """Allow guild members to test player-facing raid actions in any guild channel.

    Officer management still uses the separate owner-only allowed() guard.
    """
    return interaction.guild_id == GUILD_ID


def notify_classrooms(interaction, owner_id):
    """Queue access reconciliation after a committed shared character edit.

    Dispatch is non-blocking: the Discord response and saved profile do not
    depend on the availability of classroom permissions. The classroom worker
    also scans the register every minute to recover missed events or restarts.
    """
    dispatch = getattr(getattr(interaction, "client", None), "dispatch", None)
    if callable(dispatch):
        dispatch("classroom_character_changed", interaction.guild_id, owner_id)


async def refresh_player_signup_panel(interaction, raid_id):
    """Refresh a public sign-up list after a player edits their attendance.

    The Discord interaction is acknowledged first; a missing panel, temporary
    permission error or unavailable bot client never discards the saved signup.
    """
    client = getattr(interaction, "client", None)
    dispatch = getattr(client, "dispatch", None)
    if callable(dispatch):
        dispatch("raid_activity_changed", interaction.guild_id)
    getter = getattr(client, "get_cog", None)
    cog = getter("RaidPrototype") if callable(getter) else None
    if cog is None:
        return
    try:
        await cog.refresh_saved_panel(raid_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException,
            sqlite3.Error) as exc:
        LOGGER.warning("Unable to refresh sign-ups for Raid #%s: %s", raid_id, exc)


async def refresh_character_signups(interaction, owner_id, character_id):
    """Class/role edits immediately re-sort the collecting roster, if signed."""
    try:
        with database(RAID_DB) as con:
            raids = con.execute(
                "SELECT s.raid_id FROM signups s JOIN raids r ON r.raid_id=s.raid_id "
                "WHERE s.discord_id=? AND s.character_id=? "
                "AND s.availability='Available' AND r.archived_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM raid_absences a "
                "WHERE a.raid_id=s.raid_id AND a.discord_id=s.discord_id)",
                (owner_id, character_id),
            ).fetchall()
    except sqlite3.Error as exc:
        LOGGER.warning("Could not find active sign-ups to refresh: %s", exc)
        return
    for (raid_id,) in raids:
        await refresh_player_signup_panel(interaction, raid_id)


def save_character(discord_id, name, class_name, spec, role, path=RAID_DB, *,
                   roster_visible=True):
    """Update by player + character name, including records from the old realm form.

    Keep the legacy realm column in SQLite for migration compatibility, but never
    request or display it. Never modify a profile belonging to another Discord ID.
    """
    name = " ".join(name.split())
    if not name or len(name) > 40:
        raise ValueError("Enter a character name (40 characters maximum).")
    if role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid class, spec and raid role.")
    with database(path) as con:
        return character_mutations.register_character(
            con, discord_id, name, class_name, spec, role,
            roster_visible=roster_visible,
        )


def promote_character_to_roster(discord_id, character_id, path=RAID_DB):
    """Make an existing LFG character roster-visible without creating a copy."""
    with database(path) as con:
        return character_mutations.promote_to_roster(con, discord_id, character_id)


def list_characters(discord_id, path=RAID_DB):
    with database(path) as con:
        return character_queries.list_characters(con, discord_id)

def character_list_text(discord_id):
    rows = list_characters(discord_id)
    lines = [
        f"**{discord.utils.escape_markdown(name)}** · {class_name} / {spec} · **{role}**"
        for name, class_name, spec, role in rows
    ]
    return (
        "## My characters\n"
        + ("\n".join(lines) if lines else "Ogre not know any characters yet.")
        + "\n\n-# Update an existing character using /raid register and the same name."
    )


def member_characters(discord_id, path=RAID_DB):
    with database(path) as con:
        return character_queries.member_characters(con, discord_id)

def character_race(discord_id, character_id, path=RAID_DB):
    with database(path) as con:
        return character_queries.character_race(con, discord_id, character_id)

def set_character_race(discord_id, character_id, race, path=RAID_DB):
    with database(path) as con:
        return character_mutations.set_character_race(
            con, discord_id, character_id, race, RACE_CLASSES
        )


def character_offspec(discord_id, character_id, path=RAID_DB):
    try:
        with database(path) as con:
            row = con.execute("SELECT off_spec,off_role FROM characters "
                              "WHERE discord_id=? AND character_id=?",
                              (discord_id, character_id)).fetchone()
        return row if row else ("", "")
    except sqlite3.OperationalError:
        return "", ""

async def refresh_lfg_for_member(interaction, owner_id):
    """Keep LFG entries current when the shared character profile changes."""
    bot = getattr(interaction, "client", None)
    getter = getattr(bot, "get_cog", None)
    cog = getter("LFGPreview") if callable(getter) else None
    if cog is not None:
        try:
            await cog.refresh_member_boards(owner_id)
        except (sqlite3.Error, discord.HTTPException) as exc:
            LOGGER.warning("Couldn't refresh LFG characters for %s: %s",
                           owner_id, exc)


def set_main_character(discord_id, character_id, path=RAID_DB):
    """Mark exactly one owned character Main; leave all raid/SimC records intact."""
    with database(path) as con:
        return character_mutations.set_main_character(
            con, discord_id, character_id
        )

def delete_character(discord_id, character_id, path=RAID_DB):
    """Delete an unused character without erasing raid/attendance history."""
    with database(path) as con:
        con.execute("BEGIN IMMEDIATE")
        character = con.execute(
            "SELECT name,is_main FROM characters WHERE discord_id=? AND character_id=?",
            (discord_id, character_id),
        ).fetchone()
        if not character:
            raise ValueError("That character is no longer yours.")
        for table in ("signups", "attendance"):
            if con.execute(
                f"SELECT 1 FROM {table} WHERE character_id=? LIMIT 1",
                (character_id,),
            ).fetchone():
                raise ValueError(
                    "This character has raid sign-up or attendance history. "
                    "Ogre won't delete that history. Ask leadership to archive it."
                )
        # The LFG board stores only this canonical character ID. Refuse to
        # orphan public dungeon or launch requests when someone deletes it.
        lfg_path = os.getenv(
            "LFG_DB_PATH",
            "/data/lfg_board.db" if os.path.isdir("/data") else "lfg_board.db")
        if path == RAID_DB and os.path.isfile(lfg_path):
            with closing(sqlite3.connect(lfg_path, timeout=10)) as lfg_db:
                for table in ("lfg_dungeon_requests", "lfg_listings", "lfg_live_plans"):
                    exists = lfg_db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone()
                    if exists and lfg_db.execute(
                            f"SELECT 1 FROM {table} WHERE user_id=? AND character_id=? "
                            "LIMIT 1", (discord_id, character_id)).fetchone():
                        raise ValueError(
                            "This character is used on the LFG board. "
                            "Remove its LFG request or update the plan first.")
        # Gear belongs exclusively to this character, unlike raid history.
        for table in ("gear_exports", "gear_images"):
            con.execute(f"DELETE FROM {table} WHERE character_id=?", (character_id,))
        con.execute(
            "DELETE FROM characters WHERE discord_id=? AND character_id=?",
            (discord_id, character_id),
        )
        if character[1]:
            next_character = con.execute(
                "SELECT character_id FROM characters WHERE discord_id=? "
                "ORDER BY roster_visible DESC, character_id LIMIT 1",
                (discord_id,),
            ).fetchone()
            if next_character:
                con.execute(
                    "UPDATE characters SET is_main=1 WHERE discord_id=? AND character_id=?",
                    (discord_id, next_character[0]),
                )
        from hyped_ogre.features.characters.availability import enqueue
        enqueue(con, discord_id, "character_removed", character[0])
        return character[0]



def main_character_ids(discord_id, path=RAID_DB):
    try:
        with database(path) as con:
            return character_queries.main_character_ids(con, discord_id)
    except sqlite3.OperationalError:
        return set()

def signup_note_details(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return signup_queries.signup_note_details(con, raid_id, discord_id)

def save_gear(discord_id, character_id, text, path=RAID_DB):
    """Store a reusable raw SimC export independent of any raid application."""
    text = text.strip()
    if len(text) < 50 or len(text) > 200000:
        raise ValueError("Upload a complete SimC export (50–200,000 characters).")
    if not ("player=" in text.lower() or "level=" in text.lower()
            or "simulationcraft" in text.lower()):
        raise ValueError("Ogre need a SimulationCraft character export, not a description.")
    now = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        character = con.execute(
            "SELECT character_id FROM characters WHERE character_id=? AND discord_id=?",
            (character_id, discord_id),
        ).fetchone()
        if not character:
            raise ValueError("Ogre not know that character belongs to you.")
        con.execute(
            "INSERT INTO gear_exports (character_id, submitted_at, format, raw_text) "
            "VALUES (?, ?, 'simc_raw', ?)",
            (character_id, now, text),
        )
        con.execute(
            "UPDATE characters SET last_simc_at=? WHERE character_id=?",
            (int(time.time()), character_id),
        )



def has_wowsims_export(discord_id, character_id, path=RAID_DB):
    """A WoWSims export belongs to a character and can be reused for every raid."""
    with database(path) as con:
        return character_queries.has_wowsims_export(con, discord_id, character_id)

def wowsims_requirement_for_signup(raid_id, discord_id, character_id, path=RAID_DB):
    """Return 'missing', 'fresh' or None for the selected raid and character.

    The raid's poster controls the requirement. The WoWSims collection button
    itself remains available until the raid is completed.
    """
    event = read_raid(path, raid_id)
    policy = (event or {}).get("wowsims_policy") or "missing"
    if policy == "optional":
        return None
    if not has_wowsims_export(discord_id, character_id, path):
        return "missing"
    if policy != "fresh":
        return None
    cutoff = (event or {}).get("wowsims_refresh_after")
    if not cutoff:
        return "fresh"
    with database(path) as con:
        row = con.execute(
            "SELECT MAX(g.submitted_at) FROM gear_exports g "
            "JOIN characters c ON c.character_id=g.character_id "
            "WHERE c.discord_id=? AND c.character_id=? "
            "AND g.format='wowsims_json'",
            (discord_id, character_id),
        ).fetchone()
    if not row or not row[0]:
        return "missing"
    try:
        updated = datetime.fromisoformat(row[0])
        enabled = datetime.fromisoformat(cutoff)
        if updated.tzinfo is None or enabled.tzinfo is None:
            return "fresh"
    except (TypeError, ValueError):
        return "fresh"
    return None if updated > enabled else "fresh"


def save_wowsims_export(discord_id, character_id, raw, path=RAID_DB, *,
                        entered_name=None):
    """Verify the registered full name, then match only its first word to /wse.

    Forever WSE exports the first name in "name" (e.g. Big for Big Snore).
    Retain the complete registered name and the original export JSON.
    """
    raw = (raw or "").strip()
    if not 50 <= len(raw) <= 4000:
        raise ValueError("Ogre need complete WoWSims JSON (50-4,000 characters).")
    try:
        export = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("That not WoWSims JSON. In game: /wse export, Ctrl+C, then paste.") from exc
    if (not isinstance(export, dict)
            or not isinstance(export.get("name"), str)
            or not export["name"].strip()
            or not isinstance(export.get("class"), str)
            or not export["class"].strip()
            or not isinstance(export.get("gear"), dict)
            or not isinstance(export["gear"].get("items"), list)
            or not isinstance(export.get("version"), str)):
        raise ValueError("Ogre need full WoWSims Exporter data, not a partial JSON.")
    with database(path) as con:
        row = con.execute(
            "SELECT name FROM characters WHERE character_id=? AND discord_id=?",
            (character_id, discord_id)
        ).fetchone()
        if not row:
            raise ValueError("Ogre not know that character belongs to you.")
        registered_name = " ".join(row[0].split())
        if entered_name is not None:
            if (not isinstance(entered_name, str)
                    or " ".join(entered_name.split()).casefold()
                    != registered_name.casefold()):
                raise ValueError(
                    "Confirmed name does not match the selected character (" +
                    registered_name + "). Choose the correct character or "
                    "enter its full registered name.")
        # WSE's "realm" may contain the surname. Do not compare it or
        # replace the stored name. WSE first-name matching is intentionally
        # case-insensitive and never identifies two same-first-name alts.
        exported_first = export["name"].split()[0].casefold()
        registered_first = registered_name.split()[0].casefold()
        if exported_first != registered_first:
            raise ValueError("WoWSims export says " + export["name"][:40] +
                             ", but selected character is " + registered_name +
                             ". Export the correct character (first name must match).")
        con.execute(
            "INSERT INTO gear_exports (character_id, submitted_at, format, raw_text) "
            "VALUES (?, ?, 'wowsims_json', ?)",
            (character_id, datetime.now(timezone.utc).isoformat(), raw)
        )
        # Existing DB column name retained for backward-compatible migrations.
        con.execute("UPDATE characters SET last_simc_at=? WHERE character_id=?",
                    (int(time.time()), character_id))


def update_character_profile(discord_id, character_id, class_name, spec, role, path=RAID_DB):
    """Edit class/spec/role in place, retaining gear exports and all raid signups."""
    if role not in CLASS_SPECS.get(class_name, {}).get(spec, ()):
        raise ValueError("Choose a valid class, spec and raid role.")
    with database(path) as con:
        return character_mutations.update_character_profile(
            con, discord_id, character_id, class_name, spec, role, RACE_CLASSES
        )

def save_signup(raid_id, discord_id, character_id, path=RAID_DB, *, note=None):
    """Accept one signed-up character per member/night with optional note."""
    if note is not None:
        note = note.strip()
        if len(note) > 500:
            raise ValueError("Sign-up note must be 500 characters or less.")
    with database(path) as con:
        return signup_mutations.record_signup(
            con, raid_id, discord_id, character_id, path, note,
            wowsims_requirement_for_signup,
            lambda: datetime.now(timezone.utc).isoformat(),
        )

def my_signup(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return signup_queries.my_signup(con, raid_id, discord_id)

def save_absence(raid_id, discord_id, note="", path=RAID_DB):
    """Record the time someone marked themselves absent, with an optional note.

    Absence never requires a character; keep prior signed-up characters linked
    as Unavailable so leadership can still see the original application.
    """
    note = (note or "").strip()
    if len(note) > 500:
        raise ValueError("Note must be 500 characters or less.")
    timestamp = datetime.now(timezone.utc).isoformat()
    with database(path) as con:
        signup_mutations.record_absence(
            con, raid_id, discord_id, note, timestamp
        )
    return timestamp

def my_absence(raid_id, discord_id, path=RAID_DB):
    with database(path) as con:
        return signup_queries.my_absence(con, raid_id, discord_id)

class AbsenceNoteModal(discord.ui.Modal):
    """Note optional; signed-off time comes from the server, not user input."""
    def __init__(self, owner_id, raid_id):
        super().__init__(title=f"Absence · Raid #{raid_id}")
        self.owner_id = owner_id
        self.raid_id = raid_id
        self.note = discord.ui.TextInput(
            label="Note (optional)", placeholder="Anything Ogre should know?",
            required=False, style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This absence form belongs to another player.", ephemeral=True
            )
            return
        try:
            signed_off = save_absence(
                self.raid_id, self.owner_id, self.note.value
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't record absence: {exc}", ephemeral=True
            )
            return
        unix = int(datetime.fromisoformat(signed_off).timestamp())
        await interaction.response.send_message(
            f"Ogre mark you absent for Raid #{self.raid_id}. "
            f"Signed off <t:{unix}:F>.", ephemeral=True,
        )
        await refresh_player_signup_panel(interaction, self.raid_id)


class SignupNoteModal(discord.ui.Modal):
    """One sign-up for the whole raid night, including an optional note."""

    def __init__(self, member_id, raid_id, char_id, name, initial_note=""):
        super().__init__(title=f"Sign up · Raid #{raid_id}")
        self.member_id = member_id
        self.raid_id = raid_id
        self.char_id = char_id
        self.name = name
        self.note = discord.ui.TextInput(
            label="Note (optional)", placeholder="Any plans Ogre should know?",
            required=False, style=discord.TextStyle.paragraph, max_length=500,
            default=initial_note or None,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "That sign-up isn't yours.", ephemeral=True
            )
            return
        try:
            existing = my_signup(self.raid_id, self.member_id)
            save_signup(self.raid_id, self.member_id, self.char_id,
                        note=self.note.value)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't sign you up: {exc}", ephemeral=True
            )
            return
        already = existing and existing[0].casefold() == self.name.casefold() \
            and existing[1] == "Available"
        await interaction.response.send_message(
            f"{guild_emoji('MoveTo')} **Attendance {'updated' if already else 'registered'}** for Raid #{self.raid_id}.\n"
            f"**{discord.utils.escape_markdown(self.name)}** is marked Available.\n"
            "Your character now appears on the public sign-up list. "
            "If plans change, hit **Absent** as soon as you know.",
            ephemeral=True,
        )
        await refresh_player_signup_panel(interaction, self.raid_id)



class ExistingAttendanceView(discord.ui.LayoutView):
    """Keep repeat Attend clicks informational, with an explicit note edit."""

    def __init__(self, member_id, raid_id, existing):
        super().__init__(timeout=300)
        name, _, note = existing
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {guild_emoji('MoveTo')} Already attending Raid #{raid_id}\n"
                f"**{discord.utils.escape_markdown(name)}** · Available\n"
                + (f"**Note:** {discord.utils.escape_markdown(note)}\n" if note else "")
                + "Ogre has you down. If plans change, press **Absent** ASAP."
            ), accent_color=ACCENT,
        ))
        edit = discord.ui.Button(label="Update note", style=discord.ButtonStyle.secondary)

        async def on_edit(interaction):
            if not is_test_member(interaction) or interaction.user.id != member_id:
                await interaction.response.send_message("Not your sign-up.", ephemeral=True)
                return
            rows = member_characters(member_id)
            current = next((r for r in rows if r[1].casefold() == name.casefold()), None)
            if not current:
                await interaction.response.send_message(
                    "Character no longer found. Contact leadership.", ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                SignupNoteModal(member_id, raid_id, current[0], name, initial_note=note)
            )

        edit.callback = on_edit
        self.add_item(discord.ui.ActionRow(edit))


class WoWSimsExportModal(discord.ui.Modal):
    """Only paste /wse export JSON; optionally finish the pending raid sign-up."""

    def __init__(self, member_id, char_id, char_name, raid_id=None, note=None):
        super().__init__(title="WoWSims · " + char_name[:30])
        self.member_id, self.char_id, self.char_name = member_id, char_id, char_name
        self.raid_id, self.pending_note = raid_id, note
        self.confirm_name = discord.ui.TextInput(
            label="Confirm full character name", default=char_name,
            required=True, max_length=40,
            placeholder="First Last (e.g. Big Snore)",
        )
        self.add_item(self.confirm_name)
        self.export_text = discord.ui.TextInput(
            label="Paste /wse export here", required=True,
            style=discord.TextStyle.paragraph,
            placeholder='{"name":"Character","class":"warrior",...}',
            min_length=50, max_length=4000,
        )
        self.add_item(self.export_text)
        self.note = None
        if raid_id is not None and note is None:
            self.note = discord.ui.TextInput(
                label="Raid note (optional)", required=False,
                style=discord.TextStyle.paragraph, max_length=500,
                placeholder="Any plans Ogre should know?",
            )
            self.add_item(self.note)

    async def on_submit(self, interaction):
        if not is_test_member(interaction) or interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "That character not yours.", ephemeral=True)
            return
        try:
            save_wowsims_export(
                self.member_id, self.char_id, self.export_text.value,
                entered_name=self.confirm_name.value,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't save WoWSims: {exc}", ephemeral=True)
            return
        name = discord.utils.escape_markdown(self.char_name)
        if self.raid_id is not None:
            note = self.note.value if self.note is not None else self.pending_note
            try:
                save_signup(self.raid_id, self.member_id, self.char_id, note=note)
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Ogre saved **{name}** WoWSims, but couldn't sign Raid "
                    f"#{self.raid_id}: {exc}", ephemeral=True)
                return
            await interaction.response.send_message(
                f"Ogre got **{name}** WoWSims! Signed for Raid #{self.raid_id}. "
                "Ogre keep export for next raids too. "
                "Ogre keep WoWSims open until raid completed. Update any time.",
                ephemeral=True)
            await refresh_player_signup_panel(interaction, self.raid_id)
            return
        await interaction.response.send_message(
            f"Ogre got **{name}** WoWSims! Ogre keep this export. "
            "Use **WoWSims** again whenever gear changes, especially "
            "before roster confirmation. Ogre take updates until raid completed.", ephemeral=True)


class WoWSimsInstructionsView(discord.ui.LayoutView):
    """Show addon instructions and a single paste action before opening modal."""

    def __init__(self, owner_id, char_id, char_name, *, raid_id=None, note=None):
        super().__init__(timeout=300)
        event = read_raid(RAID_DB, raid_id) if raid_id is not None else None
        deadline = (format_discord_date(event["roster_announce_at"], style="F",
                                        with_relative=False)
                    if event and event.get("roster_announce_at") else None)
        hint = (f"by roster confirmation ({deadline})"
                if deadline else "before Ogre confirms roster")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## WoWSims · {discord.utils.escape_markdown(char_name)}\n"
                + ("Ogre need WoWSims export before signing you up.\n\n"
                   if raid_id is not None else
                   "Ogre keep WoWSims export with your character.\n\n")
                + f"1. [Get WoWSims Exporter addon]({WOWSIMS_ADDON_URL}).\n"
                "2. In game, type `/wse export`. Export window opens, "
                "your data already selected. Press **Ctrl+C**.\n"
                "3. Come back, press **Paste export**, confirm your full "
                "character name and paste with **Ctrl+V**. Only the first "
                "name is checked against the WSE export.\n\n"
                f"Ogre prefer latest export {hint}. "
                "Ogre take updates until raid completed. "
                "One export per character, reused for future raids."
            ), accent_color=ACCENT,
        ))
        paste = discord.ui.Button(label="Paste export",
                                  style=discord.ButtonStyle.success)

        async def open_paste(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message(
                    "That export not yours.", ephemeral=True)
                return
            await interaction.response.send_modal(
                WoWSimsExportModal(owner_id, char_id, char_name, raid_id, note)
            )

        paste.callback = open_paste
        self.add_item(discord.ui.ActionRow(paste))


class CharacterActionView(discord.ui.LayoutView):
    """One dropdown for signup, character management and WoWSims exports."""

    def __init__(self, owner_id, *, raid_id=None, purpose="signup", current_signup=None):
        super().__init__(timeout=300)
        self.owner_id, self.raid_id, self.purpose = owner_id, raid_id, purpose
        rows = member_characters(owner_id)
        main_ids = main_character_ids(owner_id)
        title = {"signup": "Attend", "gear": "WoWSims", "update": "Characters",
                 "lfg": "LFG Characters"}[purpose]
        hint = {
            "signup": "Choose the character you will raid on. Add an optional note next.",
            "gear": "Choose a character to paste /wse export. [Get addon](" + WOWSIMS_ADDON_URL + ").",
            "update": "Choose a character to manage, or add an alt.",
            "lfg": "Use a saved character or register one for LFG. LFG-only characters are not added to the raid roster.",
        }[purpose]
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## {title}\n{hint}"),
            accent_color=ACCENT,
        ))
        options = [
            discord.SelectOption(
                label=name[:100], value=str(char_id),
                description=f"{'Main' if char_id in main_ids else 'Alt'} · {class_name} / {spec} · {role}"[:100],
                emoji=option_emoji(class_name),
            )
            for char_id, name, class_name, spec, role, _ in rows[:24]
        ]
        if purpose in ("update", "lfg") and len(rows) < 25:
            options.append(discord.SelectOption(
                label="Add character", value="add", emoji="➕",
                description="Register a new main or alt",
            ))
        if not options:
            return
        selection = discord.ui.Select(
            placeholder="Choose character or add an alt" if purpose in ("update", "lfg") else "Choose your character",
            options=options,
        )

        async def select_character(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("That character isn't yours.", ephemeral=True)
                return
            if selection.values[0] == "add":
                await interaction.response.edit_message(
                    view=ClassPickerView(self.owner_id, roster_visible=self.purpose != "lfg"))
                return
            char_id = int(selection.values[0])
            match = next((r for r in rows if r[0] == char_id), None)
            if not match:
                await interaction.response.send_message(
                    "Character list expired. Reopen from the raid panel.", ephemeral=True,
                )
                return
            if self.purpose == "gear":
                if has_wowsims_export(self.owner_id, char_id):
                    await interaction.response.send_modal(
                        WoWSimsExportModal(self.owner_id, char_id, match[1])
                    )
                else:
                    await interaction.response.edit_message(
                        view=WoWSimsInstructionsView(self.owner_id, char_id, match[1])
                    )
            elif self.purpose in ("update", "lfg"):
                await interaction.response.edit_message(
                    view=CharacterManageView(self.owner_id, char_id, match)
                )
            else:
                required = wowsims_requirement_for_signup(
                    self.raid_id, self.owner_id, char_id
                )
                if required == "missing":
                    await interaction.response.edit_message(
                        view=WoWSimsInstructionsView(
                            self.owner_id, char_id, match[1], raid_id=self.raid_id
                        )
                    )
                elif required == "fresh":
                    await interaction.response.send_modal(
                        WoWSimsExportModal(
                            self.owner_id, char_id, match[1], raid_id=self.raid_id
                        )
                    )
                else:
                    await interaction.response.send_modal(
                        SignupNoteModal(self.owner_id, self.raid_id, char_id, match[1])
                    )

        selection.callback = select_character
        self.add_item(discord.ui.ActionRow(selection))


class CharacterRaceView(discord.ui.LayoutView):
    """Shared race edit used by both LFG and raid character management."""

    def __init__(self, owner_id, character_id, character):
        super().__init__(timeout=300)
        self.owner_id, self.character_id = owner_id, character_id
        klass = character[2]
        eligible = [race for race, classes in RACE_CLASSES.items()
                    if klass in classes]
        selected = character_race(owner_id, character_id)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Choose race · {discord.utils.escape_markdown(character[1])}\n"
                "Your raid sign-ups and LFG profile use the same character."
            ), accent_color=ACCENT,
        ))
        picker = discord.ui.Select(
            placeholder="Choose your Forever race",
            options=[discord.SelectOption(
                label=race, value=race, default=race == selected,
                description=STARTING_ZONES[race]) for race in eligible],
        )

        async def chosen(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            try:
                set_character_race(owner_id, character_id, picker.values[0])
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Ogre couldn't update race: {exc}", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterManageView(owner_id, character_id, character))
            await refresh_lfg_for_member(interaction, owner_id)

        picker.callback = chosen
        self.add_item(discord.ui.ActionRow(picker))


class CharacterProfileEditView(discord.ui.LayoutView):
    """One character-profile menu for class, spec and compatible race."""

    def __init__(self, owner_id, character_id, character):
        super().__init__(timeout=300)
        self.owner_id, self.character_id = owner_id, character_id
        _, name, class_name, spec, role, _ = character
        race = character_race(owner_id, character_id)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Class / Spec / Race · {discord.utils.escape_markdown(name)}\n"
                f"**Class / Spec:** {class_name} / {spec} · {role}\n"
                f"**Race:** {race or 'Not set'}\n"
                "Ogre keep raid sign-ups and WoWSims with this character. "
                "Change class or spec first if needed, then choose compatible race."
            ), accent_color=ACCENT,
        ))
        klass = discord.ui.Button(label="Change class / spec",
                                  style=discord.ButtonStyle.secondary)
        choose_race = discord.ui.Button(label="Change race",
                                        style=discord.ButtonStyle.secondary)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)

        async def change_class(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=ClassPickerView(owner_id, editing_character_id=character_id)
            )

        async def change_race(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            latest = next((row for row in member_characters(owner_id)
                           if row[0] == character_id), None)
            if latest is None:
                await interaction.response.send_message(
                    "Ogre cannot find that character.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterRaceView(owner_id, character_id, latest)
            )

        async def go_back(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            latest = next((row for row in member_characters(owner_id)
                           if row[0] == character_id), None)
            if latest is None:
                await interaction.response.send_message(
                    "Ogre cannot find that character.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterManageView(owner_id, character_id, latest)
            )

        klass.callback = change_class
        choose_race.callback = change_race
        back.callback = go_back
        self.add_item(discord.ui.ActionRow(klass, choose_race))
        self.add_item(discord.ui.ActionRow(back))


class CharacterManageView(discord.ui.LayoutView):
    """Manage class/spec/race, WoWSims, main status and deletion."""

    def __init__(self, owner_id, character_id, character):
        super().__init__(timeout=300)
        self.owner_id, self.character_id = owner_id, character_id
        _, name, class_name, spec, role, _ = character
        is_main = character_id in main_character_ids(owner_id)
        race = character_race(owner_id, character_id)
        off_spec, off_role = character_offspec(owner_id, character_id)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {guild_emoji(class_name)} {discord.utils.escape_markdown(name)}\n"
                f"{class_name} / {spec} · {guild_emoji(combat_role(role, class_name, spec))} {role}\n"
                f"**Race:** {race or 'Not set'} · **{'Main' if is_main else 'Alt'}**\n"
                f"**Off-spec:** {off_spec + ' · ' + off_role if off_spec else 'Not set'}"
            ), accent_color=ACCENT,
        ))
        buttons = discord.ui.ActionRow()
        edit = discord.ui.Button(label="Class / Spec / Race",
                                 style=discord.ButtonStyle.secondary)
        wowsims = discord.ui.Button(label="Update WoWSims",
                                    style=discord.ButtonStyle.secondary)
        main = discord.ui.Button(label="Set Main",
                                 style=discord.ButtonStyle.success, disabled=is_main)
        delete = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)
        off_spec_button = discord.ui.Button(label="Off-spec", style=discord.ButtonStyle.secondary)

        async def off_spec_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("That character isn't yours.", ephemeral=True)
                return
            from hyped_ogre.features.characters.offspec_ui import OffspecView
            await interaction.response.send_message(
                f"Choose an off-spec for **{discord.utils.escape_markdown(name)}**. "
                "Your main spec stays the one used for raid sign-ups.",
                view=OffspecView(self.owner_id, self.character_id, class_name), ephemeral=True)

        off_spec_button.callback = off_spec_callback

        async def edit_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterProfileEditView(
                    self.owner_id, self.character_id, character
                )
            )

        async def wowsims_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            if has_wowsims_export(self.owner_id, self.character_id):
                await interaction.response.send_modal(
                    WoWSimsExportModal(self.owner_id, self.character_id, name)
                )
            else:
                await interaction.response.edit_message(
                    view=WoWSimsInstructionsView(
                        self.owner_id, self.character_id, name
                    )
                )

        async def main_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            try:
                set_main_character(self.owner_id, self.character_id)
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Ogre couldn't set Main: {exc}", ephemeral=True)
                return
            notify_classrooms(interaction, self.owner_id)
            await interaction.response.edit_message(
                view=CharacterManageView(self.owner_id, self.character_id, character)
            )

        async def delete_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "That character isn't yours.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=ConfirmCharacterDeleteView(
                    self.owner_id, self.character_id, name
                )
            )

        edit.callback = edit_callback
        wowsims.callback = wowsims_callback
        main.callback = main_callback
        delete.callback = delete_callback
        buttons.add_item(edit)
        buttons.add_item(off_spec_button)
        buttons.add_item(wowsims)
        buttons.add_item(main)
        buttons.add_item(delete)
        self.add_item(buttons)
        back = discord.ui.Button(label="Back",
                                 style=discord.ButtonStyle.secondary)

        async def back_callback(interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Not your characters.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterActionView(self.owner_id, purpose="update")
            )

        back.callback = back_callback
        self.add_item(discord.ui.ActionRow(back))

class ConfirmCharacterDeleteView(discord.ui.LayoutView):
    """Require an explicit second click before permanently removing a character."""

    def __init__(self, owner_id, character_id, character_name):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Delete {discord.utils.escape_markdown(character_name)}?\n"
                "This permanently removes the character, WoWSims exports and screenshots. "
                "Characters with raid sign-up or attendance history cannot be deleted."
            ), accent_color=ACCENT,
        ))
        controls = discord.ui.ActionRow()
        confirm = discord.ui.Button(label="Confirm delete", style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def confirm_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message("That character isn't yours.", ephemeral=True)
                return
            try:
                removed = delete_character(owner_id, character_id)
            except (ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(f"Ogre can't delete: {exc}", ephemeral=True)
                return
            notify_classrooms(interaction, owner_id)
            await interaction.response.edit_message(
                view=CharacterActionView(owner_id, purpose="update"),
            )

        async def cancel_callback(interaction):
            if not is_test_member(interaction) or interaction.user.id != owner_id:
                await interaction.response.send_message("That character isn't yours.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=CharacterActionView(owner_id, purpose="update"),
            )

        confirm.callback = confirm_callback
        cancel.callback = cancel_callback
        controls.add_item(confirm)
        controls.add_item(cancel)
        self.add_item(controls)



class CharacterNameModal(discord.ui.Modal):
    def __init__(self, owner_id, class_name, spec, role, signup_raid_id=None,
                 *, roster_visible=True):
        super().__init__(title="Save character")
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.role = role
        self.signup_raid_id = signup_raid_id
        self.roster_visible = roster_visible
        self.name = discord.ui.TextInput(
            label="Full character name", placeholder="First Last (e.g. Big Snore)", max_length=40
        )
        self.add_item(self.name)
        if signup_raid_id is not None:
            self.note = discord.ui.TextInput(
                label="Raid note (optional)", required=False,
                style=discord.TextStyle.paragraph, max_length=500,
                placeholder="Any plans Ogre should know?",
            )
            self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_test_member(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        # A new-character form must not silently edit or duplicate an existing
        # entry, regardless of case or whether it came from LFG.
        existing = next(
            (r for r in member_characters(self.owner_id)
             if r[1].casefold() == " ".join(self.name.value.split()).casefold()),
            None,
        )
        if existing is not None:
            await interaction.response.send_message(
                "That character is already registered. Select it from your saved "
                "characters instead; Ogre won't create a duplicate.",
                ephemeral=True,
            )
            return
        try:
            outcome = save_character(
                interaction.user.id, self.name.value,
                self.class_name, self.spec, self.role,
                roster_visible=self.roster_visible or self.signup_raid_id is not None,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't save character: {exc}", ephemeral=True
            )
            return
        notify_classrooms(interaction, self.owner_id)
        name = discord.utils.escape_markdown(self.name.value.strip())
        if self.signup_raid_id is not None:
            try:
                character = next(
                    row for row in member_characters(interaction.user.id)
                    if row[1].casefold() == self.name.value.strip().casefold()
                )
                required = wowsims_requirement_for_signup(
                    self.signup_raid_id, interaction.user.id, character[0]
                )
                if required:
                    if required == "missing":
                        await interaction.response.send_message(
                            view=WoWSimsInstructionsView(
                                interaction.user.id, character[0], character[1],
                                raid_id=self.signup_raid_id, note=self.note.value
                            ), ephemeral=True
                        )
                    else:
                        await interaction.response.send_message(
                            "Ogre need fresh WoWSims export for this raid. "
                            "Click **WoWSims** to paste new one, then **Attend**.",
                            ephemeral=True
                        )
                    return
                save_signup(
                    self.signup_raid_id, interaction.user.id, character[0],
                    note=self.note.value,
                )
            except (StopIteration, ValueError, sqlite3.Error) as exc:
                await interaction.response.send_message(
                    f"Saved **{name}**, but couldn't sign up for Raid "
                    f"#{self.signup_raid_id}: {exc}. Press Sign up to retry.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"**{name}** registered and signed up for Raid "
                f"#{self.signup_raid_id}! {guild_emoji('MoveTo')}\n"
                "Your character appears on the public sign-up list. "
                "If plans change, hit **Absent** as soon as you know.",
                ephemeral=True,
            )
            await refresh_player_signup_panel(interaction, self.signup_raid_id)
            return
        character = next(
            (row for row in member_characters(interaction.user.id)
             if row[1].casefold() == self.name.value.strip().casefold()), None
        )
        if character:
            await interaction.response.send_message(
                view=CharacterManageView(interaction.user.id, character[0], character),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Ogre saved **{name}**. Open Characters to edit.", ephemeral=True
            )


class ClassPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, signup_raid_id=None, editing_character_id=None,
                 *, roster_visible=True):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        self.roster_visible = roster_visible
        title = (f"## Sign up · Raid #{signup_raid_id}\n"
                 if signup_raid_id is not None else
                 "## Update character\n" if editing_character_id is not None
                 else "## Register character\n")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                title + "Choose class, then spec and role."
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose your class",
            options=[discord.SelectOption(label=name, value=name, emoji=option_emoji(name))
                     for name in CLASS_SPECS],
        )

        async def select_class(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
                return
            await interaction.response.edit_message(
                view=SpecPickerView(
                    self.owner_id, select.values[0],
                    signup_raid_id=self.signup_raid_id,
                    editing_character_id=self.editing_character_id,
                    roster_visible=self.roster_visible,
                )
            )

        select.callback = select_class
        self.add_item(discord.ui.ActionRow(select))


class SpecPickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name, signup_raid_id=None,
                 editing_character_id=None, *, roster_visible=True):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        self.roster_visible = roster_visible
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {class_name}\n"
                "Choose spec. Ogre pick raid role automatically where possible."
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose your specialisation",
            options=[discord.SelectOption(label=name, value=name)
                     for name in CLASS_SPECS[class_name]],
        )

        async def select_spec(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
                return
            spec = select.values[0]
            roles = CLASS_SPECS[self.class_name][spec]
            if len(roles) > 1:
                await interaction.response.edit_message(
                    view=RolePickerView(
                        self.owner_id, self.class_name, spec, roles,
                        signup_raid_id=self.signup_raid_id,
                        editing_character_id=self.editing_character_id,
                        roster_visible=self.roster_visible,
                    )
                )
            elif self.editing_character_id is not None:
                await apply_profile_update(
                    interaction, self.owner_id, self.editing_character_id,
                    self.class_name, spec, roles[0], return_to_editor=True,
                )
            else:
                await interaction.response.send_modal(
                    CharacterNameModal(self.owner_id, self.class_name, spec, roles[0],
                                       signup_raid_id=self.signup_raid_id,
                                       roster_visible=self.roster_visible)
                )

        select.callback = select_spec
        self.add_item(discord.ui.ActionRow(select))


class RolePickerView(discord.ui.LayoutView):
    def __init__(self, owner_id, class_name, spec, roles, signup_raid_id=None,
                 editing_character_id=None, *, roster_visible=True):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.class_name = class_name
        self.spec = spec
        self.signup_raid_id = signup_raid_id
        self.editing_character_id = editing_character_id
        self.roster_visible = roster_visible
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## {class_name} · {spec}\n"
                "That spec can cover more than one raid role. Which do you plan to play?"
            ), accent_color=ACCENT,
        ))
        select = discord.ui.Select(
            placeholder="Choose raid role",
            options=[discord.SelectOption(label=role, value=role, emoji=option_emoji(combat_role(role, class_name, spec))) for role in roles],
        )

        async def select_role(interaction):
            if not is_test_member(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
                return
            if self.editing_character_id is not None:
                await apply_profile_update(
                    interaction, self.owner_id, self.editing_character_id,
                    self.class_name, self.spec, select.values[0],
                    return_to_editor=True,
                )
            else:
                await interaction.response.send_modal(
                    CharacterNameModal(
                        self.owner_id, self.class_name, self.spec, select.values[0],
                        signup_raid_id=self.signup_raid_id,
                        roster_visible=self.roster_visible,
                    )
                )

        select.callback = select_role
        self.add_item(discord.ui.ActionRow(select))


class ProfileUpdatedView(discord.ui.LayoutView):
    def __init__(self, name, class_name, spec, role):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Ogre updated {discord.utils.escape_markdown(name)}\n"
                f"{class_name} / {spec} · **{role}**"
            ), accent_color=ACCENT,
        ))


async def apply_profile_update(interaction, owner_id, character_id,
                               class_name, spec, role, *, return_to_editor=False):
    if not is_test_member(interaction) or interaction.user.id != owner_id:
        await interaction.response.send_message(
            "This character isn't yours.", ephemeral=True
        )
        return
    try:
        name = update_character_profile(
            owner_id, character_id, class_name, spec, role
        )
    except (ValueError, sqlite3.Error) as exc:
        await interaction.response.send_message(
            f"Ogre couldn't update character: {exc}", ephemeral=True
        )
        return
    notify_classrooms(interaction, owner_id)
    view = ProfileUpdatedView(name, class_name, spec, role)
    if return_to_editor:
        latest = next((row for row in member_characters(owner_id)
                       if row[0] == character_id), None)
        if latest is not None:
            view = CharacterProfileEditView(owner_id, character_id, latest)
    await interaction.response.edit_message(view=view)
    await refresh_character_signups(interaction, owner_id, character_id)
    await refresh_lfg_for_member(interaction, owner_id)



class RaidAttendanceSelect(discord.ui.View):
    """Private player picker for late, no-show and excused results."""
    def __init__(self, cog, raid_id, outcome, candidates):
        super().__init__(timeout=300)
        self.cog, self.raid_id, self.outcome = cog, raid_id, outcome
        choices = [discord.SelectOption(label=r[1][:100], value=r[16],
                   description=(r[9] + " · " + r[0])[:100])
                   for r in candidates[:25]]
        picker = discord.ui.Select(placeholder="Select player", options=choices)
        async def picked(interaction):
            if not cog.allowed(interaction):
                await interaction.response.send_message("Officer-only.", ephemeral=True)
                return
            member_id = picker.values[0]
            if outcome == "Late":
                await interaction.response.send_modal(RaidLateModal(cog, raid_id, member_id))
                return
            await interaction.response.defer(ephemeral=True)
            try:
                name = await asyncio.to_thread(raid_sheets_v3.attendance_set,
                    RAID_DB, raid_id, member_id,
                    "Absent" if outcome == "No Show" else "Excused")
            except Exception as exc:
                await interaction.followup.send(f"Attendance update failed: {exc}", ephemeral=True)
                return
            await interaction.followup.send(f"{name}: {outcome} saved.", ephemeral=True)
        picker.callback = picked
        self.add_item(picker)


class RaidLateModal(discord.ui.Modal, title="Mark player late"):
    minutes = discord.ui.TextInput(label="Minutes late", placeholder="e.g. 15", max_length=3)
    def __init__(self, cog, raid_id, member_id):
        super().__init__()
        self.cog, self.raid_id, self.member_id = cog, raid_id, member_id

    async def on_submit(self, interaction):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        try:
            minutes = int(str(self.minutes))
            if not 1 <= minutes <= 999:
                raise ValueError("Enter 1–999 minutes.")
        except ValueError:
            await interaction.response.send_message("Enter a valid number of minutes.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            name = await asyncio.to_thread(raid_sheets_v3.attendance_set,
                RAID_DB, self.raid_id, self.member_id, "Attended", minutes)
            await interaction.followup.send(f"{name}: attended, {minutes} minutes late.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Attendance update failed: {exc}", ephemeral=True)


async def refresh_management_poster(cog, owner_id, message, draft):
    """Reflect completed/unpublished state on the original private poster."""
    if owner_id is None or message is None or draft is None:
        return
    try:
        await message.edit(view=RaidPosterView(
            cog, owner_id, draft, page="schedule"
        ))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        LOGGER.warning("Could not refresh private raid poster: %s", exc)


class RaidCompleteModal(discord.ui.Modal, title="Complete raid"):
    wcl = discord.ui.TextInput(label="Warcraft Logs URL (optional)", required=False,
                               max_length=500)
    loot = discord.ui.TextInput(label="Loot log / link (optional)", required=False,
                                max_length=800, style=discord.TextStyle.paragraph)
    notes = discord.ui.TextInput(label="Raid notes (optional)", required=False,
                                 max_length=600, style=discord.TextStyle.paragraph)
    def __init__(self, cog, raid_id, *,
                 poster_owner_id=None, poster_message=None, poster_draft=None):
        super().__init__()
        self.cog, self.raid_id = cog, raid_id
        self.poster_owner_id, self.poster_message = poster_owner_id, poster_message
        self.poster_draft = poster_draft
        current = read_raid(RAID_DB, raid_id)
        if current:
            # If leadership pasted the report or notes in Sheets, do not
            # require a duplicate entry during completion.
            self.wcl.default = current.get("wcl_url") or ""
            self.notes.default = current.get("archive_notes") or ""

    async def on_submit(self, interaction):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        try:
            url = validate_wcl_url(str(self.wcl)) or ""
        except ValueError:
            await interaction.response.send_message("Invalid Warcraft Logs URL.", ephemeral=True)
            return
        event = read_raid(RAID_DB, self.raid_id)
        if not event or not event.get("roster_published") or event.get("archived_at"):
            await interaction.response.send_message("Raid must be confirmed and not archived.", ephemeral=True)
            return
        notes = str(self.notes).strip()
        loot = str(self.loot).strip()
        combined = notes + ("\nLoot: " + loot if loot else "")
        try:
            candidates = await asyncio.to_thread(raid_sheets_v3.attendance_candidates,
                                                 RAID_DB, self.raid_id)
        except Exception as exc:
            await interaction.response.send_message(f"Roster check failed: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**Complete Raid #{self.raid_id}?**\n"
            f"Selected and bench players: **{len(candidates)}**. "
            "Pending attendance will become Attended; existing no-shows, late "
            "and excused results will be preserved. The raid will be archived.\n"
            f"WCL: {url or 'Not provided'}\n"
            f"Loot: {'Provided' if loot else 'Not provided'}",
            view=RaidCompletionConfirm(
                self.cog, self.raid_id, url, combined,
                poster_owner_id=self.poster_owner_id,
                poster_message=self.poster_message,
                poster_draft=self.poster_draft,
            ),
            ephemeral=True)


class RaidCompletionConfirm(discord.ui.View):
    def __init__(self, cog, raid_id, url, notes, *,
                 poster_owner_id=None, poster_message=None, poster_draft=None):
        super().__init__(timeout=180)
        self.cog, self.raid_id, self.url, self.notes = cog, raid_id, url, notes
        self.poster_owner_id, self.poster_message = poster_owner_id, poster_message
        self.poster_draft = poster_draft

    @discord.ui.button(label="Confirm completion", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            # Capture newly declared absences before completing and hiding
            # the raid tab, including members omitted from confirmed teams.
            try:
                await self.cog.sync_raid_sheets()
            except Exception as exc:
                # Preserve the established completion behaviour during a
                # temporary Sheets sync outage. Attendance completion itself
                # still validates the Sheet before archiving.
                LOGGER.warning("Raid #%s pre-completion absence sync delayed: %s",
                               self.raid_id, exc)
            count = await asyncio.to_thread(raid_sheets_v3.attendance_complete,
                                            RAID_DB, self.raid_id)
            raid_status(RAID_DB, self.raid_id, "archive",
                        notes=self.notes, wcl_url=self.url)
            await self.cog.cleanup_completed_announcement(self.raid_id)
            # The stored raid is closed regardless of Sheets availability.
            # Always edit the public panel so Attend greys out on completion.
            try:
                await self.cog.sync_raid_sheets()
            finally:
                await self.cog.refresh_saved_panel(self.raid_id)
        except Exception as exc:
            await interaction.followup.send(f"Completion failed: {exc}", ephemeral=True)
            return
        await refresh_management_poster(
            self.cog, self.poster_owner_id, self.poster_message,
            self.poster_draft,
        )
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            f"Raid #{self.raid_id} completed. {count} selected/bench players recorded. "
            "Attendance and WCL saved.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Raid completion cancelled.", view=None)



class RaidUnpublishConfirmation(discord.ui.View):
    """Two-step, owner-only unpublish: no attendance or sign-ups lost."""

    def __init__(self, cog, raid_id, *,
                 poster_owner_id=None, poster_message=None, poster_draft=None):
        super().__init__(timeout=180)
        self.cog, self.raid_id = cog, raid_id
        self.poster_owner_id, self.poster_message = poster_owner_id, poster_message
        self.poster_draft = poster_draft

    @discord.ui.button(label="Confirm unpublish", style=discord.ButtonStyle.danger)
    async def confirm_unpublish(self, interaction, button):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.unpublish_roster(self.raid_id)
        except (ValueError, RuntimeError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"Ogre couldn't unpublish Raid #{self.raid_id}: {exc}",
                ephemeral=True,
            )
            return
        await refresh_management_poster(
            self.cog, self.poster_owner_id, self.poster_message,
            self.poster_draft,
        )
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            f"Raid #{self.raid_id} roster unpublished. Ogre show sign-ups again. "
            "Attendance, characters and Sheet selections still saved. "
            "Attend stay open; old reaction post deleted. "
            "Ogre can ping again after confirmation.",
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Ogre keep roster published.", view=None
        )


class RaidManageView(discord.ui.View):
    def __init__(self, cog, raid_id, *,
                 poster_owner_id=None, poster_message=None, poster_draft=None):
        super().__init__(timeout=300)
        self.cog, self.raid_id = cog, raid_id
        self.poster_owner_id, self.poster_message = poster_owner_id, poster_message
        self.poster_draft = poster_draft

    async def _choose(self, interaction, outcome):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        try:
            candidates = await asyncio.to_thread(raid_sheets_v3.attendance_candidates,
                                                 RAID_DB, self.raid_id)
        except Exception as exc:
            await interaction.response.send_message(f"Cannot load roster: {exc}", ephemeral=True)
            return
        if not candidates:
            await interaction.response.send_message("No confirmed selected/bench players.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Raid #{self.raid_id}: {outcome}", ephemeral=True,
            view=RaidAttendanceSelect(self.cog, self.raid_id, outcome, candidates))

    @discord.ui.button(label="Mark Late", style=discord.ButtonStyle.secondary)
    async def late(self, interaction, button):
        await self._choose(interaction, "Late")

    @discord.ui.button(label="No Show", style=discord.ButtonStyle.danger)
    async def no_show(self, interaction, button):
        await self._choose(interaction, "No Show")

    @discord.ui.button(label="Excused", style=discord.ButtonStyle.secondary)
    async def excused(self, interaction, button):
        await self._choose(interaction, "Excused")

    @discord.ui.button(label="Unpublish Roster", style=discord.ButtonStyle.secondary)
    async def unpublish(self, interaction, button):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        event = read_raid(RAID_DB, self.raid_id)
        if not event or event.get("archived_at") or not event.get("roster_published"):
            await interaction.response.send_message(
                "Ogre can only unpublish a currently published active raid.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"**Unpublish Raid #{self.raid_id}?**\n"
            "Ogre return public panel to sign-up list. "
            "Sign-ups and Sheet selections stay saved. "
            "Ogre will delete the old reaction message. "
            "Confirm the corrected roster and choose Announce again "
            "to send a fresh ping.",
            view=RaidUnpublishConfirmation(
                self.cog, self.raid_id,
                poster_owner_id=self.poster_owner_id,
                poster_message=self.poster_message,
                poster_draft=self.poster_draft,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Complete Raid", style=discord.ButtonStyle.success)
    async def complete(self, interaction, button):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Officer-only.", ephemeral=True)
            return
        await interaction.response.send_modal(RaidCompleteModal(
            self.cog, self.raid_id,
            poster_owner_id=self.poster_owner_id,
            poster_message=self.poster_message,
            poster_draft=self.poster_draft,
        ))


@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class RaidPrototype(commands.GroupCog, group_name="raid", group_description="Hyped raid sign-up management"):
    def __init__(self, bot):
        self.bot = bot
        self._sheet_task = None
        self._invite_task = None
        self._discord_name_cache = {}
        self._last_sheet_error = None
        self._last_sheet_sync = None
        self._sync_lock = asyncio.Lock()
        self._ack_lock = asyncio.Lock()

    def allowed(self, interaction):
        # During testing, live raid panels are ONLY permitted in private #ogre.
        return is_test_owner(interaction)

    def allowed_sync(self, interaction):
        """Only the owner may sync; permit the public raid sign-up channel."""
        return self.allowed(interaction) or (
            interaction.user.id == OWNER_ID
            and interaction.guild_id == GUILD_ID
            and interaction.channel_id == RAID_CHANNEL_ID
        )

    def raid_db_path(self):
        return RAID_DB

    async def announce_roster(self, raid_id, selected=None):
        async with self._ack_lock:
            link = await raid_announcer.announce(
                self.bot, RAID_DB, raid_id, GUILD_ID, selected=selected
            )
        # The reaction message ID exists only after posting it. Refresh the
        # V2 sign-up panel so "Confirm raid spot" points to that actual post.
        # A temporary Discord edit failure must not cause a duplicate ping.
        try:
            await self.refresh_saved_panel(raid_id)
        except (discord.HTTPException, sqlite3.Error, ValueError) as exc:
            LOGGER.warning("Raid #%s confirmation link refresh delayed: %s",
                           raid_id, exc)
        return link

    async def cleanup_completed_announcement(self, raid_id):
        """Remove only the roster acknowledgement ping after archiving.

        Failure must not roll back a completed raid. The durable
        needs_refresh flag lets the existing maintenance task retry.
        """
        async with self._ack_lock:
            try:
                await raid_announcer.refresh(self.bot, RAID_DB, raid_id, GUILD_ID)
            except (discord.HTTPException, ValueError, sqlite3.Error) as exc:
                LOGGER.warning("Raid #%s acknowledgement cleanup delayed: %s",
                               raid_id, exc)

    async def unpublish_roster(self, raid_id):
        """Restore V2 signups and delete the withdrawn V1 reaction message."""
        from cogs import raid_acknowledgements as ack
        async with self._ack_lock:
            event = read_raid(RAID_DB, raid_id)
            if not event or event.get("archived_at") or not event.get("roster_published"):
                raise ValueError("Raid roster is not currently published.")
            raid_status(RAID_DB, raid_id, "reopen")
            if ack.retract(RAID_DB, raid_id):
                try:
                    await raid_announcer.refresh(self.bot, RAID_DB, raid_id, GUILD_ID)
                except (discord.HTTPException, ValueError) as exc:
                    # Keep the old ID until deletion is actually confirmed;
                    # maintenance retries it. Never send a duplicate ping.
                    LOGGER.warning("Raid #%s announcement deletion delayed: %s",
                                   raid_id, exc)
        try:
            await self.sync_raid_sheets()
        except Exception as exc:
            LOGGER.warning("Raid #%s unpublish sheet sync delayed: %s",
                           raid_id, exc)
        await self.refresh_saved_panel(raid_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        async with self._ack_lock:
            await raid_announcer.reaction(self.bot, RAID_DB, payload, GUILD_ID)

    async def delete_raid_from_poster(self, interaction, raid_number):
        """Delete the raid card, then raid-scoped rows; never delete characters."""
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can delete raids in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            event = read_raid(RAID_DB, raid_number)
        except sqlite3.Error:
            await interaction.response.send_message(
                "Ogre couldn't read this raid; nothing was deleted.",
                ephemeral=True,
            )
            return
        if event is None:
            await interaction.response.send_message(
                "That raid no longer exists.", ephemeral=True
            )
            return
        channel_id = event.get("panel_channel_id")
        message_id = event.get("panel_message_id")
        await interaction.response.defer(ephemeral=True)
        # Verify Discord authorship before touching Sheets or raid records.
        # Sheets deletion happens first; failed API calls leave the raid
        # available for retry rather than orphaning a private tab.
        panel_message = None
        if message_id and channel_id:
            try:
                # Only remove the exact bot-authored panel recorded for this raid.
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
                    raise ValueError("The saved raid panel channel is not in this server.")
                panel_message = await channel.fetch_message(message_id)
                if panel_message.author.id != self.bot.user.id:
                    raise ValueError("The saved panel message is not authored by Ogre.")
            except discord.NotFound:
                # Already deleted from Discord; still allow database cleanup.
                panel_message = None
            except (discord.Forbidden, discord.HTTPException, ValueError) as exc:
                await interaction.followup.send(
                    f"Ogre couldn't remove the saved Discord raid panel: {exc}. "
                    "Raid data was preserved; please retry.",
                    ephemeral=True,
                )
                return
        # Hold the same lock as automatic Sheets sync until BOTH the
        # Sheets tab and raid database record are removed. Otherwise the
        # background job can recreate the deleted tab mid-deletion.
        async with self._sync_lock:
            deleted_sheet = False
            if raid_sheets.configured():
                try:
                    deleted_sheet = await asyncio.to_thread(
                        raid_sheets_v3.delete_raid_tab, raid_number
                    )
                except (ValueError, RuntimeError, sqlite3.Error) as exc:
                    await interaction.followup.send(
                        f"Ogre couldn't delete Raid #{raid_number} from Google Sheets: "
                        f"{exc}. Raid and public panel preserved; retry deletion.",
                        ephemeral=True,
                    )
                    return
            if panel_message is not None:
                try:
                    await panel_message.delete()
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await interaction.followup.send(
                        f"Ogre removed the Sheets tab but couldn't remove the "
                        f"Discord panel: {exc}. Raid database preserved. "
                        "Fix channel permissions and retry; the Sheets tab may "
                        "reappear during automatic sync.",
                        ephemeral=True,
                    )
                    return
            try:
                delete_raid(RAID_DB, raid_number)
            except (ValueError, sqlite3.Error):
                await interaction.followup.send(
                    "Ogre couldn't delete the saved raid records. "
                    "No other raids or characters were touched.",
                    ephemeral=True,
                )
                return
        try:
            await interaction.message.edit(
                view=RaidPosterView(self, interaction.user.id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(
            f"Ogre deleted Raid #{raid_number} from Discord and local raid "
            "records. "
            + ("Its Google Sheets raid tab was deleted. " if deleted_sheet else
               "No corresponding Google Sheets raid tab was found or Sheets "
               "is not connected. ")
            + "Saved characters and WoWSims exports remain untouched.",
            ephemeral=True,
        )

    async def cog_load(self):
        # Reattach controls in every destination, including after restarts.
        for event in active_panels(RAID_DB):
            if event.get("panel_channel_id") and event.get("panel_message_id"):
                self.bot.add_view(self.make_panel(event), message_id=event["panel_message_id"])
        if raid_sheets.configured():
            self._sheet_task = asyncio.create_task(self.sheet_sync_loop())
        # Invite posts are Discord-only and must continue when Sheets is down.
        self._invite_task = asyncio.create_task(self.invite_reminder_loop())

    def cog_unload(self):
        if self._sheet_task:
            self._sheet_task.cancel()
        if self._invite_task:
            self._invite_task.cancel()

    async def invite_reminder_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await invite_reminders.send_due_invites(
                    self.bot, RAID_DB, GUILD_ID
                )
            except (discord.HTTPException, sqlite3.Error, ValueError) as exc:
                LOGGER.warning("Raid invite scheduler error: %s", exc)
            await asyncio.sleep(30)

    async def sheet_sync_loop(self):
        await self.bot.wait_until_ready()
        # One silent startup edit repairs older V2 panels that still point to
        # the manual Check message. No new announcements or member pings.
        for event in active_panels(RAID_DB):
            if event.get("roster_published") and not event.get("archived_at"):
                try:
                    await self.refresh_saved_panel(event["raid_id"])
                except (discord.HTTPException, sqlite3.Error, ValueError) as exc:
                    LOGGER.warning("Raid #%s confirmation link update delayed: %s",
                                   event["raid_id"], exc)
        while True:
            try:
                await self.sync_raid_sheets()
            except Exception as exc:
                # A Sheets outage may not interrupt Discord raid registrations.
                self._last_sheet_error = str(exc)
                LOGGER.warning("Raid Sheets sync failed: %s", exc)
            async with self._ack_lock:
                await raid_announcer.retry_dirty(self.bot, RAID_DB, GUILD_ID)
            await asyncio.sleep(60)

    async def raid_discord_names(self):
        """Resolve guild nicknames on the Discord event loop, not in the Sheets thread."""
        member_ids = await asyncio.to_thread(
            raid_sheets.character_discord_ids, RAID_DB
        )
        guild = self.bot.get_guild(GUILD_ID)
        names = {}
        for member_id in member_ids:
            member = guild.get_member(member_id) if guild is not None else None
            # fetch_member works even with an empty member cache. Only fetch
            # names not resolved in a previous sync, to avoid hourly API churn.
            if member is None and guild is not None and member_id not in self._discord_name_cache:
                try:
                    member = await guild.fetch_member(member_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            if member is not None:
                names[member_id] = member.display_name
            elif member_id in self._discord_name_cache:
                names[member_id] = self._discord_name_cache[member_id]
            else:
                user = self.bot.get_user(member_id)
                if user is not None:
                    names[member_id] = user.name
        self._discord_name_cache.update(names)
        return names

    async def sync_raid_sheets(self):
        async with self._sync_lock:
            names = await self.raid_discord_names()
            counts, changed = await asyncio.to_thread(
                raid_sheets_v3.sync_once, RAID_DB, names
            )
            self._last_sheet_error = None
            self._last_sheet_sync = datetime.now(timezone.utc)
            for raid_id in changed:
                event = read_raid(RAID_DB, raid_id)
                if not event or not event.get("panel_message_id"):
                    continue
                channel_id = event.get("panel_channel_id")
                if not channel_id:
                    continue
                try:
                    channel = self.bot.get_channel(channel_id)
                    if channel is None:
                        channel = await self.bot.fetch_channel(channel_id)
                    message = await channel.fetch_message(event["panel_message_id"])
                    await message.edit(view=self.make_panel(event))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Unable to refresh revealed roster for Raid #%s in channel %s",
                                   raid_id, channel_id)
            if changed:
                # Keep Ogre's separate V1 announcement aligned with later
                # leadership Sheet changes, without publishing a new ping.
                # In old test/minimal databases the optional acknowledgement
                # table may not exist; never interrupt the V2 panel sync.
                try:
                    with database(RAID_DB) as con:
                        con.executemany(
                            "UPDATE raid_ack_announcements SET needs_refresh=1 "
                            "WHERE raid_id=? AND retracted_at IS NULL "
                            "AND deleted_at IS NULL",
                            [(raid_id,) for raid_id in changed],
                        )
                except sqlite3.Error as exc:
                    LOGGER.warning("Roster acknowledgement refresh delayed: %s", exc)
            return counts, changed

    async def on_register(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        await interaction.response.send_message(
            view=ClassPickerView(interaction.user.id), ephemeral=True
        )

    async def on_characters(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        await interaction.response.send_message(
            character_list_text(interaction.user.id), ephemeral=True
        )

    async def on_signup(self, interaction, raid_id):
        if not is_test_member(interaction):
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        # Acknowledge immediately to avoid Discord's three-second interaction deadline.
        await interaction.response.defer(ephemeral=True)
        try:
            event = read_raid(RAID_DB, raid_id)
            if not event or not event["signup_open"] or event.get("archived_at"):
                await interaction.followup.send(
                    "Sign-ups are closed for this raid.", ephemeral=True
                )
                return
            if not member_characters(interaction.user.id):
                # First-time users register a character and sign up in one flow.
                await interaction.followup.send(
                    view=ClassPickerView(interaction.user.id, signup_raid_id=raid_id),
                    ephemeral=True,
                )
                return
            existing = signup_note_details(raid_id, interaction.user.id)
            if existing and existing[1] == "Available":
                await interaction.followup.send(
                    view=ExistingAttendanceView(
                        interaction.user.id, raid_id, existing
                    ), ephemeral=True,
                )
                return
            await interaction.followup.send(
                view=CharacterActionView(interaction.user.id, raid_id=raid_id),
                ephemeral=True,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.followup.send(
                f"Ogre couldn't open sign-ups: {exc}", ephemeral=True
            )

    async def on_gear(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Gear is available to members of this server.", ephemeral=True
            )
            return
        rows = member_characters(interaction.user.id)
        if not rows:
            await interaction.response.send_message(
                "Ogre needs a character first. Click **Characters** to add one.",
                ephemeral=True,
            )
        elif len(rows) == 1:
            character_id, character_name = rows[0][:2]
            if has_wowsims_export(interaction.user.id, character_id):
                await interaction.response.send_modal(
                    WoWSimsExportModal(
                        interaction.user.id, character_id, character_name
                    )
                )
            else:
                await interaction.response.send_message(
                    view=WoWSimsInstructionsView(
                        interaction.user.id, character_id, character_name
                    ), ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                view=CharacterActionView(interaction.user.id, purpose="gear"),
                ephemeral=True,
            )

    async def on_update(self, interaction):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Characters are available to members of this server.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            view=CharacterActionView(interaction.user.id, purpose="update"),
            ephemeral=True,
        )

    async def on_absent(self, interaction, raid_id):
        if not is_test_member(interaction):
            await interaction.response.send_message(
                "Absence is available to members of this server.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            AbsenceNoteModal(interaction.user.id, raid_id)
        )

    def raid_emoji_map(self):
        """Stable custom markup without guild-cache lookup."""
        return {name.casefold(): guild_emoji(name) for name in EMOJI_IDS}

    def make_panel(self, event):
        # Some callers pass a raw Sheets snapshot without the computed link.
        # Reload the authoritative saved announcement before building V2.
        event = read_raid(RAID_DB, event["raid_id"]) or event

        async def signup(interaction):
            await self.on_signup(interaction, event["raid_id"])

        async def absent(interaction):
            await self.on_absent(interaction, event["raid_id"])

        async def remind(interaction):
            start = datetime.fromisoformat(event["raid_at"])
            target = int(start.timestamp())
            enabled = toggle_raid_dm(event["raid_id"], interaction.user.id, target)
            if enabled is None:
                message = "The 30-minute reminder window has already passed."
            elif enabled:
                message = f"Ogre shout you 30 minutes before Raid #{event['raid_id']}."
            else:
                message = "Ogre stop raid reminder."
            await interaction.response.send_message(message, ephemeral=True)

        return RaidEventPanel(
            event, on_signup=signup, on_update=self.on_update,
            on_remind=remind,
            on_gear=self.on_gear, on_absent=absent,
            public_roster=read_signup_roster(
                RAID_DB, event["raid_id"], include_identity=True
            ),
            emoji_map=self.raid_emoji_map(),
        )

    async def post_panel(self, interaction, event, destination_id=None, ping_role_ids=None):
        """Publish to the poster-selected text channel; edit existing panel in place."""
        channel_id = destination_id or event.get("panel_channel_id") or interaction.channel_id
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(
                "Choose a text channel in this server that Ogre can access.", ephemeral=True)
            return
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                f"Ogre needs View Channel and Send Messages in {channel.mention}.", ephemeral=True)
            return

        old_channel_id = event.get("panel_channel_id")
        old_message_id = event.get("panel_message_id")
        if old_message_id and old_channel_id == channel.id:
            try:
                message = await channel.fetch_message(old_message_id)
                await message.edit(view=self.make_panel(event))
                await interaction.response.send_message(
                    f"Ogre updated raid panel: {message.jump_url}", ephemeral=True)
                if raid_sheets.configured():
                    try:
                        await self.sync_raid_sheets()
                    except Exception as exc:
                        LOGGER.warning("Raid tab sync delayed: %s", exc)
                return
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message(
                    "Ogre couldn't update the existing raid panel.", ephemeral=True)
                return

        # Only ping on a newly published panel, never when editing or refreshing.
        role_ids = set(ping_role_ids or [])
        roles = [role for role in channel.guild.roles
                 if role.id in role_ids and role.id != channel.guild.id]
        if roles and not perms.mention_everyone:
            roles = [role for role in roles if role.mentionable]
        mentions = " ".join(role.mention for role in roles)
        await interaction.response.defer(ephemeral=True)
        ping_message = None
        if mentions:
            try:
                ping_message = await channel.send(
                    content=(f"{mentions} Ogre got raid sign-ups ready!"
                             + (f" Required: {event['check_message_url']}"
                                if event.get("check_message_url") else "")),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=roles),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                await interaction.followup.send(
                    f"Ogre couldn't ping the selected roles in {channel.mention}: {exc}",
                    ephemeral=True)
                return
        try:
            message = await channel.send(
                view=self.make_panel(event),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            if ping_message is not None:
                try:
                    await ping_message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
            await interaction.followup.send(
                f"Ogre couldn't post in {channel.mention}: {exc}", ephemeral=True)
            return
        record_panel(
            RAID_DB, event["raid_id"], channel.id, message.id,
            creator_id=getattr(getattr(interaction, "user", None), "id", None),
        )
        if old_message_id and old_channel_id and old_channel_id != channel.id:
            try:
                old_channel = self.bot.get_channel(old_channel_id) or await self.bot.fetch_channel(old_channel_id)
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Old raid panel could not be removed after moving Raid #%s",
                               event["raid_id"])
        await interaction.followup.send(
            f"Ogre posted raid panel: {message.jump_url}", ephemeral=True)
        if raid_sheets.configured():
            try:
                await self.sync_raid_sheets()
            except Exception as exc:
                LOGGER.warning("Raid tab sync delayed: %s", exc)

    async def refresh_saved_panel(self, number):
        event = read_raid(RAID_DB, number)
        if not event or not event.get("panel_channel_id") or not event.get("panel_message_id"):
            return
        channel_id = event["panel_channel_id"]
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        message = await channel.fetch_message(event["panel_message_id"])
        await message.edit(view=self.make_panel(event))

    @app_commands.command(
        name="confirm", description="Publish the roster while keeping sign-ups open"
    )
    @app_commands.describe(number="Raid number to confirm")
    async def confirm(self, interaction: discord.Interaction, number: int):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.sync_raid_sheets()
            approved = await asyncio.to_thread(raid_sheets_v3.read_selected, RAID_DB, number)
            raid_status(RAID_DB, number, "confirm", confirmed_entries=approved)
            await self.sync_raid_sheets()
            await self.refresh_saved_panel(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid confirm: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} roster published. Sign-ups remain open and "
            "Sheet assignments stay editable. Announce selected players from /raid poster.",
            ephemeral=True,
        )

    @app_commands.command(
        name="reopen", description="Reopen a confirmed raid roster for corrections"
    )
    @app_commands.describe(number="Confirmed raid number")
    async def reopen(self, interaction: discord.Interaction, number: int):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.unpublish_roster(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid reopen: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} roster unpublished. Sign-ups remain open. "
            "Update the Sheet and publish again from /raid poster. "
            "Choose Announce after reconfirming to ping selected players again.",
            ephemeral=True,
        )

    @app_commands.command(
        name="leader", description="Choose a leader for a raid instance or split"
    )
    @app_commands.describe(
        number="Raid number",
        group="Instance group to lead",
        member="Selected Discord member to lead, or leave blank to unassign",
    )
    @app_commands.choices(group=[
        app_commands.Choice(name="Hyjal", value="Hyjal"),
        app_commands.Choice(name="Barrows 🔸", value="Barrows 🔸"),
        app_commands.Choice(name="Barrows 🔹", value="Barrows 🔹"),
        app_commands.Choice(name="Ony", value="Ony"),
    ])
    async def leader(self, interaction: discord.Interaction, number: int,
                     group: str, member: discord.Member | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Owner-only in private #ogre.", ephemeral=True,
            )
            return
        try:
            selected = set_group_leader(
                RAID_DB, number, group, member.id if member else None
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't set leader: {exc}", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        # Update the existing V1 message without repinging anyone. The
        # background maintenance job also retries if Discord is unavailable.
        async with self._ack_lock:
            try:
                await raid_announcer.refresh(self.bot, RAID_DB, number, GUILD_ID)
            except (discord.HTTPException, ValueError, sqlite3.Error) as exc:
                LOGGER.warning("Raid #%s leader refresh delayed: %s", number, exc)
        await interaction.followup.send(
            f"Raid #{number} {group} leader: " +
            (f"<@{selected}>" if selected else "Unassigned") +
            ". V2 sign-up stays untouched.",
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True,
        )

    @app_commands.command(
        name="archive", description="Archive completed raid with notes and Warcraft Logs"
    )
    @app_commands.describe(number="Completed raid number",
                           wcl_url="Optional Warcraft Logs report URL",
                           notes="Optional raid review notes")
    async def archive(self, interaction: discord.Interaction, number: int,
                      wcl_url: str | None = None, notes: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("Owner-only in private #ogre.", ephemeral=True)
            return
        notes = (notes or "").strip()
        if len(notes) > 1500:
            await interaction.response.send_message("Keep raid notes under 1500 characters.",
                                                    ephemeral=True)
            return
        try:
            url = validate_wcl_url(wcl_url) or ""
        except ValueError:
            await interaction.response.send_message(
                "Use an HTTPS Warcraft Logs report link (…/reports/…).",
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            raid_status(RAID_DB, number, "archive", notes=notes, wcl_url=url)
            await self.cleanup_completed_announcement(number)
            # Close the Discord Attend control even when Sheets is unavailable.
            try:
                await self.sync_raid_sheets()
            finally:
                await self.refresh_saved_panel(number)
        except (ValueError, RuntimeError, sqlite3.Error,
                discord.HTTPException) as exc:
            await interaction.followup.send(f"Raid archive: {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Raid #{number} archived with review notes and Warcraft Logs. "
            "Its private tab is hidden, not deleted.",
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="Synchronise raid SQLite data and visible roster with Google Sheets")
    async def sync(self, interaction: discord.Interaction):
        if not self.allowed_sync(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can sync raids in #ogre or the raid channel.", ephemeral=True
            )
            return
        if not raid_sheets.configured():
            await interaction.response.send_message(
                "Google Sheets not connected to Ogre yet. "
                "Set RAID_GOOGLE_SERVICE_ACCOUNT_B64 and RAID_SHEETS_ID "
                "as Fly secrets, then share the private sheet with "
                "the service account email.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            counts, changed = await self.sync_raid_sheets()
        except Exception as exc:
            self._last_sheet_error = str(exc)
            await interaction.followup.send(
                f"Ogre couldn't sync Google Sheets: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            "Ogre synced raid data to Google Sheets. "
            f"Updated/added: {sum(counts.values())} rows. "
            f"Refreshed {len(changed)} raid roster(s).",
            ephemeral=True,
        )

    @app_commands.command(
        name="poster", description="Open the V2 raid setup menu in private #ogre"
    )
    async def poster(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can open raid poster in private #ogre.",
                ephemeral=True,
            )
            return
        # The poster itself is a normal visible bot message, not an ephemeral preview.
        await interaction.response.defer(ephemeral=True)
        try:
            message = await interaction.channel.send(
                view=RaidPosterView(self, interaction.user.id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, discord.Forbidden, ValueError) as exc:
            await interaction.followup.send(
                f"Ogre couldn't open raid poster: {exc}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Raid poster ready: {message.jump_url}\n"
            "Choose date, time, instances and roster announcement, then post.",
            ephemeral=True,
        )

    @app_commands.command(name="setup", description="Post or update a real raid panel in private #ogre")
    @app_commands.describe(
        number="Raid number, e.g. 1",
        date_time="Raid start in UK local time: YYYY-MM-DD HH:MM",
        instance_1="First raid instance",
        instance_2="Optional second instance",
        instance_3="Optional third instance",
        roster_announce="Roster announcement in UK time: YYYY-MM-DD HH:MM",
        softres_url="Optional https://softres.it/raid/... link",
    )
    async def raid_setup(self, interaction: discord.Interaction,
                         number: int, date_time: str, instance_1: str,
                         instance_2: str | None = None,
                         instance_3: str | None = None,
                         roster_announce: str | None = None,
                         softres_url: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message(
                "Raid panels can only be set up by Ogre's owner in private #ogre.",
                ephemeral=True,
            )
            return
        try:
            event = save_raid(
                RAID_DB, number, date_time, instance_1, instance_2,
                instance_3, roster_announce, softres_url
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn't set up raid: {exc}", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(name="softres", description="Add, change or remove a raid SoftRes link")
    @app_commands.describe(
        number="Raid number",
        url="Optional public SoftRes link; leave empty to remove it",
    )
    async def softres(self, interaction: discord.Interaction,
                      number: int, url: str | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        try:
            event = set_softres_url(RAID_DB, number, url)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(
                f"Ogre couldn\u0027t update SoftRes: {exc}", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(name="panel", description="Refresh the existing real raid panel")
    @app_commands.describe(number="Raid number (leave blank for latest)")
    async def panel(self, interaction: discord.Interaction, number: int | None = None):
        if not self.allowed(interaction):
            await interaction.response.send_message("This action is available to members of the Hyped Discord server.", ephemeral=True)
            return
        event = read_raid(RAID_DB, number) if number is not None else latest_raid(RAID_DB)
        if not event:
            await interaction.response.send_message(
                "No raid configured yet. Use /raid setup first.", ephemeral=True
            )
            return
        await self.post_panel(interaction, event)

    @app_commands.command(name="register", description="Register or update a character")
    async def register(self, interaction: discord.Interaction):
        await self.on_register(interaction)

    @app_commands.command(name="characters", description="Show your saved characters (private)")
    async def characters(self, interaction: discord.Interaction):
        await self.on_characters(interaction)


async def setup(bot):
    create_schema()
    ensure_raid_fields(RAID_DB)
    await bot.add_cog(RaidPrototype(bot))
