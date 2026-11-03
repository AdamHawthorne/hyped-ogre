"""Mirror valid, manually editable Sheets attendance into the local register."""

from datetime import datetime, timezone


def mirror_rows(path, rows):
    from cogs.raid_schedule import database
    statuses = {"Pending": "Pending", "Attended": "Attended",
                "Absent": "No-show", "Excused": "Excused"}
    changed = 0
    with database(path) as con:
        for raw in rows:
            if len(raw) < 10 or any(not str(raw[i]).isdigit() for i in (0, 2, 4)):
                continue
            raid_id, member_id, character_id = (int(raw[i]) for i in (0, 2, 4))
            status = statuses.get(raw[9])
            if status is None:
                continue
            exists = con.execute(
                "SELECT 1 FROM characters c JOIN raids r ON r.raid_id=? "
                "WHERE c.character_id=? AND c.discord_id=?",
                (raid_id, character_id, member_id)).fetchone()
            if not exists:
                continue
            def minutes(index):
                try:
                    value = int(raw[index]) if len(raw) > index and raw[index] else 0
                    return value if 0 <= value <= 1440 else 0
                except (TypeError, ValueError):
                    return 0
            late, afk = minutes(10), minutes(11)
            if status == "Attended" and late:
                status = "Late"
            notes = str(raw[13]) if len(raw) > 13 else ""
            old = con.execute("SELECT character_id,status,late_minutes,afk_minutes,leadership_notes "
                              "FROM attendance WHERE raid_id=? AND discord_id=?",
                              (raid_id, member_id)).fetchone()
            desired = (character_id, status, late, afk, notes)
            if old == desired:
                continue
            updated = str(raw[14]) if len(raw) > 14 and raw[14] else datetime.now(timezone.utc).isoformat()
            con.execute("INSERT INTO attendance "
                        "(raid_id,discord_id,character_id,status,late_minutes,afk_minutes,leadership_notes,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(raid_id,discord_id) DO UPDATE SET "
                        "character_id=excluded.character_id,status=excluded.status,"
                        "late_minutes=excluded.late_minutes,afk_minutes=excluded.afk_minutes,"
                        "leadership_notes=excluded.leadership_notes,updated_at=excluded.updated_at",
                        (raid_id, member_id, *desired, updated))
            changed += 1
    return changed
