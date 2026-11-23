"""Public-facing main character roster, separate from private management tabs.

Uses the exact registered-main, roster-visible and board-hidden conditions of
the Discord Characters embed. Never exports Discord IDs, alts or hidden mains.
"""
from urllib.parse import quote

from cogs import raid_sheets
from cogs.raid_schedule import signup_role_group
from hyped_ogre.features.characters import roster as character_roster

TAB = "Full Roster"
ROLES = ("Tanks", "Healers", "Melee", "Ranged")
HEADERS = ("In-game name", "Class", "Spec", "Level", "iLvl", "Professions")
WIDTH = len(HEADERS)
CLASS_COLOURS = {
    "Warrior": "C79C6E", "Paladin": "F58CBA", "Hunter": "ABD473",
    "Rogue": "FFF569", "Priest": "FFFFFF", "Death Knight": "C41F3B",
    "Shaman": "0070DD", "Mage": "40C7EB", "Warlock": "8787ED",
    "Druid": "FF7D0A", "Monk": "00FF96", "Demon Hunter": "A330C9",
}
CAPACITY = 500


def visible_mains(db_path):
    """Share the Discord Characters board's exact visibility and stat sources.

    roster.rows() already joins board-hidden overrides and enriches characters
    from the verified Blizzard cache and WoWSims exports with fallback values.
    """
    return [
        record for record in character_roster.rows(db_path)
        if record["is_main"] and not record.get("board_hidden")
    ]


def website_rows(records):
    """Fixed four-role order, including zero-count sections."""
    groups = {role: [] for role in ROLES}
    for record in records:
        name = record["name"]  # Canonical in-game name, never board alias.
        klass, spec, role = (
            record["class_name"], record["spec"], record["role"]
        )
        level = record.get("level")
        level = str(level) if type(level) is int and 1 <= level <= 60 else "N/A"
        ilvl = character_roster.display_ilvl(record.get("ilvl"))
        professions = record.get("professions") or "N/A"
        groups[signup_role_group(klass, spec, role)].append(
            (name, klass, spec, level, ilvl, professions)
        )
    for entries in groups.values():
        entries.sort(key=lambda r: (
            0 if r[0].strip().casefold() == "big snore" else 1,
            r[1].casefold(), r[0].casefold()
        ))
    rows = [
        ["<Hyped> Guild Roster"] + [""] * (WIDTH - 1),
        [f"{sum(map(len, groups.values()))} characters"] + [""] * (WIDTH - 1),
        list(HEADERS),
    ]
    sections = []
    for role in ROLES:
        sections.append(len(rows))
        rows.append([f"{role} ({len(groups[role])})"] + [""] * (WIDTH - 1))
        rows.extend(list(entry) for entry in groups[role])
    if len(rows) > CAPACITY:
        raise RuntimeError("Website roster exceeds its 500-row limit.")
    return rows, sections


def _range(a1):
    return quote(f"'{TAB}'!{a1}", safe="'!:")


def _read(session):
    return raid_sheets._json(
        session, "GET",
        f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/{_range('A1:F500')}",
    ).get("values", [])


def _sheet_id(session):
    document = raid_sheets._json(
        session, "GET", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}",
        params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount)))"},
    )
    for sheet in document.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == TAB:
            sheet_id = props["sheetId"]
            # Older website tabs had three columns. Grow in place so
            # published tab identity and public URLs remain unchanged.
            columns = props.get("gridProperties", {}).get("columnCount", WIDTH)
            if columns < WIDTH:
                raid_sheets._json(
                    session, "POST",
                    f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
                    json={"requests": [{"appendDimension": {
                        "sheetId": sheet_id, "dimension": "COLUMNS",
                        "length": WIDTH - columns,
                    }}]},
                )
            return sheet_id
    created = raid_sheets._json(
        session, "POST", f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}:batchUpdate",
        json={"requests": [{"addSheet": {"properties": {
            "title": TAB,
            "gridProperties": {
                "rowCount": CAPACITY, "columnCount": WIDTH,
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
                      "endColumnIndex": WIDTH},
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
    # Apply WoW class colours to the Class cells only. Preserve the
    # restrained page theme and readability of the other five columns.
    for index, row in enumerate(rows):
        if index < 3 or index in sections:
            continue
        hex_colour = CLASS_COLOURS.get(row[1])
        if not hex_colour:
            continue
        rgb = tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4))
        background = dict(zip(("red", "green", "blue"), rgb))
        luminance = sum(c * w for c, w in zip(rgb, (0.299, 0.587, 0.114)))
        foreground = ink if luminance > 0.52 else white
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": index,
                      "endRowIndex": index + 1, "startColumnIndex": 1,
                      "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {
                "backgroundColor": background,
                "textFormat": {"foregroundColor": foreground, "bold": True},
            }},
            "fields": "userEnteredFormat.backgroundColor,"
                      "userEnteredFormat.textFormat.foregroundColor,"
                      "userEnteredFormat.textFormat.bold",
        }})
    for index, width in enumerate((260, 145, 170, 85, 85, 265)):
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
        normalised = [(list(row) + [""] * WIDTH)[:WIDTH] for row in existing]
        if normalised == desired:
            return False
        target = desired + [[""] * WIDTH] * max(0, len(existing) - len(desired))
        raid_sheets._json(
            session, "PUT",
            f"{raid_sheets.API}/{raid_sheets.SPREADSHEET_ID}/values/"
            + _range(f"A1:F{len(target)}"),
            params={"valueInputOption": "RAW"},
            json={"values": target},
        )
        _format(session, sheet_id, desired, sections)
        return True
    finally:
        session.close()
