"""Last and next three scheduled raid sign-up states per Discord member.

The timeline is relative to the date of the current raid, not today's date.
Past statuses prefer actual attendance records when available. Without one,
'Attend' means signed up, NOT proof that the player physically attended.
"""
from datetime import datetime

STATES = {
    "Attended": "Attended", "Late": "Late", "AFK": "AFK",
    "No-show": "No-show", "Excused": "Excused",
}


def history_for_raid(db_path, current_raid_id):
    from cogs.raid_schedule import database
    with database(db_path) as con:
        raids = list(con.execute(
            "SELECT raid_id, raid_at FROM raids ORDER BY raid_at, raid_id"
        ))
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
        # Attendance is authoritative for completed raids, where recorded.
        for raid_id, member_id, status in con.execute(
            "SELECT raid_id, discord_id, status FROM attendance"
        ):
            if raid_id in previous and status in STATES:
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
