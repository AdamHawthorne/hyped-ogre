"""Last and next three scheduled raid sign-up states per Discord member.

The timeline is relative to the date of the current raid, not today's date.
For completed raids, the manually edited Forever Attendance tab is authoritative
when provided by a Sheets sync. Historical signup/absence records are fallbacks
only where no final attendance decision has been recorded. 'Attend' means
signed up, NOT proof of actual attendance.
"""
from datetime import datetime

STATES = {
    "Attended": "Attended", "Late": "Late", "AFK": "AFK",
    "No-show": "No-show", "Absent": "Absent", "Excused": "Excused",
    "Pending": "Pending",
}


def history_for_raid(db_path, current_raid_id, attendance_rows=None):
    """Past 3 | Next 3: official completed outcomes, live future sign-ups.

    attendance_rows is the latest 'Forever Attendance' worksheet read from
    A5:O1000, including its header. None maintains the local-only fallback
    for callers that do not have an authenticated Sheets session.
    """
    from cogs.raid_schedule import database
    with database(db_path) as con:
        raids = list(con.execute(
            "SELECT raid_id, raid_at, archived_at FROM raids "
            "ORDER BY raid_at, raid_id"
        ))
        completed = {row[0] for row in raids if row[2]}
        positions = [r[0] for r in raids]
        if current_raid_id not in positions:
            return {}
        at = positions.index(current_raid_id)
        previous = positions[max(0, at - 3):at]
        future = positions[at + 1:at + 4]
        wanted = set(previous + future)
        statuses = {}
        for raid_id, member_id, availability in con.execute(
            "SELECT raid_id, discord_id, availability FROM signups"
        ):
            if raid_id in wanted:
                statuses[(raid_id, member_id)] = (
                    "Absent" if availability != "Available" else "Attend"
                )
        for raid_id, member_id in con.execute(
            "SELECT raid_id, discord_id FROM raid_absences"
        ):
            if raid_id in wanted:
                statuses[(raid_id, member_id)] = "Absent"
        if attendance_rows is None:
            # Local cache is a fallback only when Google Sheets is not being
            # read. It may lag behind manually changed attendance outcomes.
            for raid_id, member_id, status in con.execute(
                "SELECT raid_id, discord_id, status FROM attendance"
            ):
                if raid_id in previous and status in STATES and status != "Pending":
                    statuses[(raid_id, member_id)] = STATES[status]
        else:
            # The permanent worksheet wins for completed raids. Do not copy
            # an old raid's sign-up status back over an officer's Excused
            # decision, and do not require a character ID for absence-only
            # attendance entries. Match by raid and Discord member ID.
            for raw in attendance_rows[1:]:
                if (len(raw) < 10 or not str(raw[0]).isdigit()
                        or not str(raw[2]).isdigit()):
                    continue
                raid_id, member_id = int(raw[0]), int(raw[2])
                if raid_id not in previous or raid_id not in completed:
                    continue
                status = str(raw[9]).strip()
                if status in STATES:
                    statuses[(raid_id, member_id)] = STATES[status]
        members = set(r[0] for r in con.execute(
            "SELECT DISTINCT discord_id FROM characters"
        ))
        members.update(member_id for _, member_id in statuses)
    # Oldest to newest; where fewer than three raids are scheduled, use '—'
    # to distinguish a non-existent raid from an unregistered guild member.
    prior_slots = [None] * (3 - len(previous)) + previous
    next_slots = future + [None] * (3 - len(future))
    return {
        str(member_id): (
            " · ".join(
                statuses.get((number, member_id), "Missing") if number else "—"
                for number in prior_slots
            )
            + " | " +
            " · ".join(
                statuses.get((number, member_id), "Missing") if number else "—"
                for number in next_slots
            )
        )
        for member_id in members
    }
