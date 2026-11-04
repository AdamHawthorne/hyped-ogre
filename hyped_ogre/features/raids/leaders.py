"""Leadership-owned instance/split leaders for the separate V1 raid post.

A person may lead one group per raid. The guild owner defaults to the first
group in which they actually appear, not every group on the same raid night.
Explicit assignments and unassignments are stored independently of sign-ups.
"""
import os

from cogs.raid_schedule import database, read_raid, read_public_roster

GROUPS = ("Hyjal", "Barrows 🔸", "Barrows 🔹", "Ony")


def group_label(group):
    if group == "Hyjal · 20-player":
        return "Hyjal"
    if group == "Barrows · 10-player 🔸":
        return "Barrows 🔸"
    if group == "Barrows · 10-player 🔹":
        return "Barrows 🔹"
    if group == "Ony · 40-player":
        return "Ony"
    return group


def member_groups(assignment):
    """Consistent with the roster's default 🔸 Barrows split."""
    tokens = {part.strip().casefold() for part in (assignment or "").split(",")}
    result = set()
    if "hyjal" in tokens:
        result.add("Hyjal")
    if "ony" in tokens:
        result.add("Ony")
    if tokens & {"barrows 🔸", "barrows 🔹", "barrows a", "barrows b",
                 "group a", "group b", "🔸", "🔹", "barrows"}:
        if tokens & {"barrows 🔹", "barrows b", "group b", "🔹"}:
            result.add("Barrows 🔹")
        else:
            result.add("Barrows 🔸")
    return result


def read_leaders(path, raid_id):
    with database(path) as con:
        return dict(con.execute(
            "SELECT group_name, discord_id FROM raid_group_leaders "
            "WHERE raid_id=?", (raid_id,),
        ))


def effective_leaders(path, event, selected):
    """Return only valid, non-duplicated leaders for groups on the roster."""
    from cogs.raid_schedule import roster_groups
    present = {group_label(label) for label, members in roster_groups(
        event, [(row[1], row[2], row[3], row[4], row[5], row[0])
                for row in selected if len(row) >= 6], by_role="four"
    ) if members}
    members = {}
    for row in selected:
        if len(row) >= 6 and str(row[0]).isdigit() and int(row[0]) > 0:
            for group in member_groups(str(row[2])):
                members.setdefault(group, set()).add(str(row[0]))
    saved = read_leaders(path, event["raid_id"])
    result, used = {}, set()
    for group in GROUPS:
        chosen = saved.get(group)
        if group not in present or group not in saved:
            continue
        if chosen and chosen in members.get(group, set()) and chosen not in used:
            result[group] = chosen
            used.add(chosen)
        else:
            result[group] = None
    owner = str(os.getenv("OWNER_ID", "0"))
    if owner not in ("", "0") and owner not in used:
        for group in GROUPS:
            if group in present and group not in result and owner in members.get(group, set()):
                result[group] = owner
                break
    return result


def set_group_leader(path, raid_id, group, member_id=None):
    """Officer-controlled assignment; member must be selected for that group."""
    if group not in GROUPS:
        raise ValueError("Choose Hyjal, Barrows 🔸, Barrows 🔹 or Ony.")
    event = read_raid(path, raid_id)
    if not event or not event.get("roster_published") or event.get("archived_at"):
        raise ValueError("Publish an active roster before assigning leaders.")
    roster = read_public_roster(path, raid_id, include_identity=True)
    selected = [
        (str(member), name, assignment, klass, role, spec)
        for name, assignment, klass, role, spec, member in roster
    ]
    if not any(group in member_groups(entry[2]) for entry in selected):
        raise ValueError("That instance group has no selected players.")
    member_id = str(member_id) if member_id is not None else ""
    if member_id and not any(
        row[0] == member_id and group in member_groups(row[2])
        for row in selected
    ):
        raise ValueError("Choose a Discord member assigned to this instance group.")
    with database(path) as con:
        if member_id:
            overlap = con.execute(
                "SELECT group_name FROM raid_group_leaders "
                "WHERE raid_id=? AND discord_id=? AND group_name!=?",
                (raid_id, member_id, group),
            ).fetchone()
            if overlap:
                raise ValueError(f"That player already leads {overlap[0]}.")
        con.execute(
            "INSERT INTO raid_group_leaders (raid_id,group_name,discord_id) "
            "VALUES (?,?,?) ON CONFLICT(raid_id,group_name) DO UPDATE SET "
            "discord_id=excluded.discord_id",
            (raid_id, group, member_id),
        )
        con.execute(
            "UPDATE raid_ack_announcements SET needs_refresh=1 "
            "WHERE raid_id=? AND retracted_at IS NULL AND deleted_at IS NULL",
            (raid_id,),
        )
    return effective_leaders(path, event, selected).get(group)
