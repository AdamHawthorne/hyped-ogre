"""One opt-in roster announcement per raid, with persistent reaction acknowledgements.

Acknowledgement removes a mention from the outstanding list and adds a Sheet
tick beside the member's Discord name. It never changes sign-ups, roster
selection, attendance or the raid panel.
"""
from datetime import datetime, timezone

from cogs.raid_schedule import database

ACK_EMOJI = "<:check:1566948806535159808>"


def announcement(path, raid_id):
    with database(path) as con:
        return con.execute(
            "SELECT channel_id, message_id FROM raid_ack_announcements "
            "WHERE raid_id=?", (raid_id,)
        ).fetchone()


def members(path, raid_id):
    with database(path) as con:
        return con.execute(
            "SELECT discord_id, character, acknowledged_at "
            "FROM raid_ack_members WHERE raid_id=? ORDER BY position", (raid_id,)
        ).fetchall()


def pending(path, raid_id):
    return [(member_id, character) for member_id, character, acknowledged in
            members(path, raid_id) if acknowledged is None]


def record_announcement(path, raid_id, channel_id, message_id, selected):
    """Store exactly the guild member IDs that were pinged; one post per raid."""
    selected = [(str(discord_id), name) for discord_id, name in selected
                if str(discord_id).isdigit() and int(discord_id) > 0]
    # One signup per Discord member. Deduplicate defensively.
    selected = list({discord_id: (discord_id, name) for discord_id, name in selected}.values())
    if not selected:
        raise ValueError("No selected Discord members to announce.")
    with database(path) as con:
        if con.execute("SELECT 1 FROM raid_ack_announcements WHERE raid_id=?",
                       (raid_id,)).fetchone():
            raise ValueError("Ogre already announced this roster.")
        con.execute(
            "INSERT INTO raid_ack_announcements "
            "(raid_id, channel_id, message_id, created_at) VALUES (?,?,?,?)",
            (raid_id, channel_id, message_id,
             datetime.now(timezone.utc).isoformat()),
        )
        con.executemany(
            "INSERT INTO raid_ack_members "
            "(raid_id, discord_id, character, position) VALUES (?,?,?,?)",
            [(raid_id, member_id, name[:80], i)
             for i, (member_id, name) in enumerate(selected)],
        )


def acknowledge(path, channel_id, message_id, member_id):
    """Return raid_id when this is a NEW acknowledgement; otherwise None."""
    with database(path) as con:
        row = con.execute(
            "SELECT a.raid_id, a.retracted_at, r.archived_at "
            "FROM raid_ack_announcements a "
            "JOIN raids r ON r.raid_id=a.raid_id "
            "WHERE a.channel_id=? AND a.message_id=?",
            (channel_id, message_id),
        ).fetchone()
        if not row or row[1] or row[2]:
            return None
        raid_id = row[0]
        changed = con.execute(
            "UPDATE raid_ack_members SET acknowledged_at=? "
            "WHERE raid_id=? AND discord_id=? AND acknowledged_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), raid_id, str(member_id)),
        )
        if changed.rowcount != 1:
            return None
        con.execute("UPDATE raid_ack_announcements SET needs_refresh=1 "
                    "WHERE raid_id=?", (raid_id,))
        return raid_id


def retract(path, raid_id):
    """Withdraw an earlier one-time announcement, without clearing its history."""
    with database(path) as con:
        result = con.execute(
            "UPDATE raid_ack_announcements SET retracted_at=COALESCE(retracted_at, ?), "
            "needs_refresh=1 WHERE raid_id=? AND retracted_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), raid_id),
        )
        return result.rowcount == 1


def is_retracted(path, raid_id):
    with database(path) as con:
        row = con.execute(
            "SELECT retracted_at FROM raid_ack_announcements WHERE raid_id=?",
            (raid_id,),
        ).fetchone()
    return bool(row and row[0])


def render_retracted(raid_id, panel_url=None):
    """Never include player mentions when withdrawing a published roster."""
    result = (
        f"**<Hyped> Raid #{raid_id} | Roster withdrawn**\n"
        "Ogre fixing raid team. Old roster not current. "
        "Sign-ups stay open. Check raid panel for updates. "
        "Ogre stop collecting nods on this message."
    )
    if panel_url:
        result += f"\n\n[View raid panel]({panel_url})"
    return result


def dirty_announcements(path):
    with database(path) as con:
        return [row[0] for row in con.execute(
            "SELECT raid_id FROM raid_ack_announcements WHERE needs_refresh=1"
        )]


def mark_refreshed(path, raid_id):
    with database(path) as con:
        con.execute("UPDATE raid_ack_announcements SET needs_refresh=0 "
                    "WHERE raid_id=?", (raid_id,))


def mark_deleted(path, raid_id):
    """Record completed-raid Discord cleanup without losing ack history."""
    with database(path) as con:
        con.execute(
            "UPDATE raid_ack_announcements SET deleted_at=COALESCE(deleted_at, ?), "
            "needs_refresh=0 WHERE raid_id=?",
            (datetime.now(timezone.utc).isoformat(), raid_id),
        )


def render(raid_id, awaiting, total, panel_url=None):
    """Compact public text, with no mass mentions and no name removed from roster."""
    # The V1 embed already supplies the raid heading and grouped roster.
    # Keep the actual text message compact while still pinging selected users.
    intro = f"React below to confirm raid spot. Ogre cross name off list. {ACK_EMOJI}\n"
    if awaiting:
        mentions = " ".join(f"<@{int(member_id)}>" for member_id, _ in awaiting)
        body = f"**Still need nod ({len(awaiting)}/{total}):** {mentions}"
    else:
        body = f"**All {total} acknowledged.** Ogre got nod from everyone!"
    footer = f"\n\n[View raid roster]({panel_url})" if panel_url else ""
    result = intro + body + footer
    if len(result) > 2000:
        raise ValueError("Too many selected raiders for one Discord announcement.")
    return result
