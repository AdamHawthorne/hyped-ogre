"""Safe, read-only human equipment view of a character's latest /wse export.

WoWSims JSON carries item/enchant IDs, but not dependable item names,
icons or item-level metadata. Don't invent these or score missing enchants.
"""
import json
import sqlite3
from cogs import forever_item_data
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
    "Item", "Enchant", "Wowhead item link", "iLvl", "Exported (UK)",
)
UK = ZoneInfo("Europe/London")
MAX_CHARACTERS = 75  # 75 * 19 = 1425 detail rows; keep the sheet bounded.

# Conservative classic baseline: excludes jewellery/trinkets, off-hand
# frills, and ranged scopes. Head/shoulder enchants may depend on faction or
# progression; this is a slot-check indicator, not proof an enchant is required.
ENCHANT_SLOTS = frozenset((
    "Head", "Shoulders", "Back", "Chest", "Wrists", "Hands",
    "Legs", "Feet", "Main Hand",
))


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


def average_item_level(profile, item_catalog=None):
    """Only average item levels actually resolved for equipped items."""
    if not profile:
        return None, 0, 0
    known = []
    equipped = 0
    for slot in profile["slots"]:
        if slot["id"]:
            equipped += 1
            data = forever_item_data.lookup(item_catalog or {}, slot["id"])
            level = data.get("level")
            if isinstance(level, int) and level > 0:
                known.append(level)
    return (round(sum(known) / len(known), 1) if known else None,
            len(known), equipped)


def enchant_gaps(profile):
    """Unenchanted equipment in commonly enchantable slots, not every slot."""
    if not profile:
        return 0
    return sum(bool(slot["id"] and not slot["enchant"])
               for slot in profile["slots"] if slot["slot"] in ENCHANT_SLOTS)


def summary(profile, item_catalog=None):
    """Gear-level estimate and conservative enchant gaps; never character Lv."""
    if not profile:
        return "WoWSims not submitted"
    average, known, equipped = average_item_level(profile, item_catalog)
    level = f"{average:.1f}" if average is not None else "?"
    completeness = f" ({known}/{equipped} known)" if known != equipped else ""
    if not equipped:
        completeness = " (no gear)"
    gaps = enchant_gaps(profile)
    return f"iLvl {level}{completeness} · {gaps} enchant gap{'s' if gaps != 1 else ''}"


def _exported_uk(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UK).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return ""


def sheet_rows(profiles, item_catalog=None):
    """One clean, bounded tab with named Forever items and resolved iLvl."""
    item_catalog = item_catalog or {}
    rows = [
        ["<Hyped> · WoWSims Equipment"],
        ["Forever-only cached item data: item names and levels are estimates "
         "from a beta snapshot. Blank iLvl = unresolved. Enchant gaps are "
         "a conservative standard-slot check, not a full enchant audit."],
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
            f"{klass} / {spec}", summary(profile, item_catalog),
            ("Professions: " + ", ".join(profile["professions"]))
            if profile["professions"] else "", "", "", "", exported,
        ])
        for slot in profile["slots"]:
            item_id = slot["id"]
            enchant_id = slot["enchant"] if item_id else None
            metadata = forever_item_data.lookup(item_catalog, item_id)
            rows.append([
                label, "", "", slot["slot"],
                metadata.get("name") or (f"Item #{item_id}" if item_id else "Empty"),
                f"Enchant #{enchant_id}" if enchant_id else
                ("Not reported" if item_id else "—"),
                f"{forever_item_data.WOWHEAD_BASE}{item_id}" if item_id else "",
                metadata.get("level") or "", "",
            ])
        rows.append([""] * len(HEADERS))
    return rows, headings
