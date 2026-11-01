"""Safe, read-only human equipment view of a character's latest /wse export.

WoWSims JSON carries item/enchant IDs, but not dependable item names,
icons or item-level metadata. Don't invent these or score missing enchants.
"""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

SLOTS = (
    "Head", "Neck", "Shoulders", "Back", "Chest", "Wrists",
    "Hands", "Waist", "Legs", "Feet", "Finger 1", "Finger 2",
    "Trinket 1", "Trinket 2", "Main Hand", "Off Hand", "Ranged",
)
SHEET_NAME = "Forever Gear"
HEADERS = (
    "Character", "Level", "Class / Spec", "Slot",
    "Item", "Enchant", "Wowhead item link", "Exported (UK)",
)
UK = ZoneInfo("Europe/London")
MAX_CHARACTERS = 75  # 75 * 19 = 1425 detail rows; keep the sheet bounded.


def _positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if 0 < number < 1_000_000_000 else None


def parse_export(raw):
    """The export's fixed 17-slot index is authoritative, not item list order."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    gear = value.get("gear")
    if not isinstance(gear, dict) or not isinstance(gear.get("items"), list):
        return None
    items = gear["items"]
    slots = []
    for index, name in enumerate(SLOTS):
        spec = items[index] if index < len(items) else None
        spec = spec if isinstance(spec, dict) else {}
        slots.append({
            "slot": name,
            "id": _positive_id(spec.get("id")),
            "enchant": _positive_id(spec.get("enchant")),
            "random_suffix": _positive_id(spec.get("random_suffix")),
        })
    professions = [
        str(p.get("name", "")) for p in (value.get("professions") or [])
        if isinstance(p, dict) and p.get("name")
    ]
    return {
        "name": str(value.get("name") or ""),
        "level": _positive_id(value.get("level")),
        "class": str(value.get("class") or ""),
        "race": str(value.get("race") or ""),
        "spec": str(value.get("spec") or ""),
        "version": str(value.get("version") or ""),
        "professions": professions[:2],
        "slots": slots,
        "equipped": sum(bool(x["id"]) for x in slots),
        "enchanted": sum(bool(x["id"] and x["enchant"]) for x in slots),
    }


def latest_profiles(db_path):
    """Fetch only latest saved WoWSims export for each registered character."""
    from cogs.raid_schedule import database
    with database(db_path) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("""
            SELECT c.character_id, c.discord_id, c.name, c.class_name,
                   c.spec, g.submitted_at, g.raw_text
            FROM characters c
            JOIN gear_exports g ON g.export_id = (
                SELECT g2.export_id FROM gear_exports g2
                WHERE g2.character_id=c.character_id
                  AND g2.format='wowsims_json'
                ORDER BY g2.export_id DESC LIMIT 1
            )
            ORDER BY LOWER(c.name), c.character_id
        """)]


def summary(profile):
    """Never call a character level 'item level' or assume enchants are mandatory."""
    if not profile:
        return "WoWSims received"
    level = f"Lv {profile['level']} · " if profile["level"] else ""
    enchant_count = profile["enchanted"]
    return (f"{level}{profile['equipped']}/17 equipped · "
            f"{enchant_count} enchant{'s' if enchant_count != 1 else ''} reported")


def _exported_uk(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UK).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return ""


def sheet_rows(profiles):
    """One clean, bounded tab: character banners plus numbered equipment slots."""
    rows = [
        ["<Hyped> · WoWSims Equipment"],
        ["Latest saved /wse export per character. Item IDs link to Wowhead; "
         "the export does not contain item names, icons or item level. "
         "An empty enchant field does not prove an enchant is required."],
        list(HEADERS),
    ]
    headings = []
    for record in profiles[:MAX_CHARACTERS]:
        profile = parse_export(record["raw_text"])
        if profile is None:
            continue
        exported = _exported_uk(record["submitted_at"])
        label = str(record["name"])[:80]
        klass = str(record["class_name"])[:50]
        spec = str(record["spec"])[:50]
        headings.append(len(rows))  # zero-indexed heading for styling
        rows.append([
            label, profile["level"] or "Unknown",
            f"{klass} / {spec}", summary(profile),
            ("Professions: " + ", ".join(profile["professions"]))
            if profile["professions"] else "", "", "", exported,
        ])
        for slot in profile["slots"]:
            item_id = slot["id"]
            enchant_id = slot["enchant"] if item_id else None
            rows.append([
                label, "", "", slot["slot"],
                f"Item #{item_id}" if item_id else "Empty",
                f"Enchant #{enchant_id}" if enchant_id else
                ("Not reported" if item_id else "—"),
                f"https://www.wowhead.com/classic/item={item_id}" if item_id else "",
                "",
            ])
        rows.append([""] * len(HEADERS))
    return rows, headings
