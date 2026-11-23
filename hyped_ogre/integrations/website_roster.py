"""Public-facing main character roster, separate from private management tabs.

Uses the exact registered-main, roster-visible and board-hidden conditions of
the Discord Characters embed. Never exports Discord IDs, alts or hidden mains.
"""
from urllib.parse import quote

from cogs import raid_sheets
from cogs.raid_schedule import database, signup_role_group

TAB = "Hyped Website Roster"
ROLES = ("Tanks", "Healers", "Melee", "Ranged")
HEADERS = ("In-game name", "Class", "Spec")
CAPACITY = 500


def visible_mains(db_path):
    """Use canonical in-game character names, not manually edited display aliases."""
    with database(db_path) as con:
        records = con.execute(
            "SELECT c.name,c.class_name,c.spec,c.role FROM characters c "
            "LEFT JOIN character_board_overrides b ON b.character_id=c.character_id "
            "WHERE c.is_main=1 AND c.roster_visible=1 AND COALESCE(b.hidden,0)=0 "
            "ORDER BY LOWER(c.class_name),LOWER(c.name),c.character_id"
        ).fetchall()
    return [tuple(record) for record in records]


def website_rows(records):
    """Fixed four-role order, including zero-count sections."""
    groups = {role: [] for role in ROLES}
    for name, klass, spec, role in records:
        groups[signup_role_group(klass, spec, role)].append((name, klass, spec))
    for entries in groups.values():
        entries.sort(key=lambda r: (
            0 if r[0].strip().casefold() == "big snore" else 1,
            r[1].casefold(), r[0].casefold()
        ))
    rows = [
        ["<Hyped> Guild Roster", "", ""],
        [f"{sum(map(len, groups.values()))} main characters", "", ""],
        list(HEADERS),
    ]
    sections = []
    for role in ROLES:
        sections.append(len(rows))
        rows.append([f"{role} ({len(groups[role])})", "", ""])
        rows.extend(list(entry) for entry in groups[role])
    if len(rows) > CAPACITY:
        raise RuntimeError("Website roster exceeds its 500-row limit.")
    return rows, sections


def _range(a1):
    return quote(f"'{TAB}'!{a1}", safe="'!:")


def _read(session):
    return raid_sheets._json(
        session, "GET",
        f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/{_range('A1:C500')}",
    ).get("values", [])


def _sheet_id(session):
    document = raid_sheets._json(
        session, "GET", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}",
        params={"fields": "sheets(properties(sheetId,title))"},
    )
    for sheet in document.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == TAB:
            return props["sheetId"]
    created = raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": [{"addSheet": {"properties": {
            "title": TAB,
            "gridProperties": {
                "rowCount": CAPACITY, "columnCount": 3,
                "frozenRowCount": 3, "hideGridlines": True,
            },
        }}}]},
    )
    return created["replies"][0]["addSheet"]["properties"]["sheetId"]


def _format(session, sheet_id, rows, sections):
    black = {"red": 0.075, "green": 0.075, "blue": 0.09}
    gold = {"red": 0.83, "green": 0.61, "blue": 0.29}
    white = {"red": 1, "green": 1, "blue": 1}
    pale = {"red": 0.955, "green": 0.95, "blue": 0.94}
    ink = {"red": 0.13, "green": 0.13, "blue": 0.15}
    def paint(start, stop, bg, fg, *, bold=False, size=11):
        return {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": start,
                      "endRowIndex": stop, "startColumnIndex": 0,
                      "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg, "textFormat": {
                    "foregroundColor": fg, "fontFamily": "Roboto",
                    "fontSize": size, "bold": bold},
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat",
        }}
    requests = [
        paint(0, CAPACITY, pale, ink),
        paint(0, 1, black, gold, bold=True, size=18),
        paint(1, 2, black, white, bold=True),
        paint(2, 3, gold, black, bold=True),
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 3,
                                              "hideGridlines": True}},
            "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines",
        }},
    ]
    requests.extend(paint(idx, idx + 1, black, white, bold=True, size=12)
                    for idx in sections)
    for index, width in enumerate((270, 160, 200)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": index, "endIndex": index + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize",
        }})
    raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": requests},
    )


def sync_once(db_path):
    """Create/update the website tab; no destructive changes to other worksheets."""
    if not raid_sheets.configured():
        return False
    records = visible_mains(db_path)
    desired, sections = website_rows(records)
    session = raid_sheets._session()
    try:
        sheet_id = _sheet_id(session)
        existing = _read(session)
        # Google may omit trailing blank cells: compare normalised visible rows.
        normalised = [(list(row) + [""] * 3)[:3] for row in existing]
        if normalised == desired:
            return False
        target = desired + [["", "", ""]] * max(0, len(existing) - len(desired))
        raid_sheets._json(
            session, "PUT",
            f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/"
            + _range(f"A1:C{len(target)}"),
            params={"valueInputOption": "RAW"},
            json={"values": target},
        )
        _format(session, sheet_id, desired, sections)
        return True
    finally:
        session.close()
