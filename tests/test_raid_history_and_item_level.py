"""Relative raid signup timeline, owner-first ordering and Forever-only iLvl."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import raid_history, forever_item_data as items
from cogs import raid_schedule as schedule, raid_signup as raid
from cogs import raid_sheets_v3 as sheets, wowsims_gear as gear
from wowsims_helper import signup_with_export


class RaidHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.db)
        schedule.ensure_raid_fields(self.db)
        for idx, day in enumerate((1, 4, 7, 10, 13, 16, 19), 1):
            schedule.save_raid(self.db, idx,
                               f"2026-12-{day:02d} 20:00", "Hyjal (20m)")
        raid.save_character(42, "Snore Mon", "Warrior", "Protection", "Tank",
                            self.db)
        raid.save_character(43, "Alpha Tank", "Warrior", "Protection", "Tank",
                            self.db)
        self.owner_char = raid.member_characters(42, self.db)[0][0]
        self.other_char = raid.member_characters(43, self.db)[0][0]

    def test_previous_three_and_next_three_by_member_and_time(self):
        for number in (3, 5, 6):
            signup_with_export(number, 42, self.owner_char, self.db)
        raid.save_absence(6, 42, "Travel", self.db)
        raid.save_absence(7, 42, "Away", self.db)
        timeline = raid_history.history_for_raid(self.db, 4)
        self.assertEqual(
            timeline["42"],
            "Missing · Missing · Attend | Attend · Absent · Absent"
        )
        self.assertEqual(
            timeline["43"],
            "Missing · Missing · Missing | Missing · Missing · Missing"
        )
        event = schedule.read_raid(self.db, 4)
        signup_with_export(4, 42, self.owner_char, self.db)
        body = sheets._rows(self.db, event, {})
        owner = next(row for row in body if row[16] == "42")
        self.assertEqual(owner[19], timeline["42"])
        # A different character on another raid still belongs to member 42.
        raid.save_character(42, "Snore Two", "Mage", "Frost", "DPS", self.db)
        alt = next(c[0] for c in raid.member_characters(42, self.db)
                   if c[1] == "Snore Two")
        signup_with_export(5, 42, alt, self.db)
        self.assertEqual(
            raid_history.history_for_raid(self.db, 4)["42"], timeline["42"]
        )

    def test_no_scheduled_raid_is_dash_not_missing(self):
        self.assertEqual(
            raid_history.history_for_raid(self.db, 1)["43"],
            "— · — · — | Missing · Missing · Missing"
        )
        self.assertEqual(
            raid_history.history_for_raid(self.db, 7)["43"],
            "Missing · Missing · Missing | — · — · —"
        )

    def test_actual_attendance_overrides_historical_signup(self):
        signup_with_export(3, 42, self.owner_char, self.db)
        with schedule.database(self.db) as con:
            con.execute(
                "INSERT INTO attendance (raid_id, discord_id, character_id, "
                "status) VALUES (3, 42, ?, 'No-show')", (self.owner_char,)
            )
        self.assertIn("No-show", raid_history.history_for_raid(self.db, 4)["42"])
        # Pending is not proof of attendance, so signed-up remains Attend.
        with schedule.database(self.db) as con:
            con.execute(
                "UPDATE attendance SET status='Pending' "
                "WHERE raid_id=3 AND discord_id=42"
            )
        self.assertIn("Attend", raid_history.history_for_raid(self.db, 4)["42"])

    def test_owner_first_in_tanks_without_changing_character_ids(self):
        signup_with_export(4, 43, self.other_char, self.db)
        signup_with_export(4, 42, self.owner_char, self.db)
        event = schedule.read_raid(self.db, 4)
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            body = sheets._rows(self.db, event, {})
        tanks = [row for row in body if row[16] in ("42", "43")]
        self.assertEqual([row[16] for row in tanks], ["42", "43"])
        self.assertEqual([row[1] for row in tanks],
                         ["Snore Mon", "Alpha Tank"])


class ItemEnrichmentTests(unittest.TestCase):
    def test_forever_item_level_is_parsed_or_omitted(self):
        self.assertEqual(items.item_level("<span>Item Level 67</span>"), 67)
        self.assertEqual(items.item_level("I t e m L e v e l   84"), 84)
        self.assertIsNone(items.item_level("Requires Level 60"))
        self.assertIsNone(items.item_level("unknown"))

    def test_partial_average_enchants_and_forever_url(self):
        raw = json.dumps({
            "level": 60, "name": "Snore",
            "gear": {"items": [
                {"id": 100, "enchant": 1},
                None, {"id": 101}, None, {"id": 102},
            ]},
        })
        p = gear.parse_export(raw)
        catalog = {
            "100": {"name": "Helm of Testing", "level": 70},
            "101": {"name": "Shoulders of Testing", "level": 60},
            "102": {"name": "Chest of Testing", "level": 80},
        }
        self.assertEqual(gear.average_item_level(p, catalog), (70.0, 3, 3))
        self.assertEqual(gear.enchant_gaps(p), 2)
        self.assertEqual(gear.summary(p, catalog),
                         "iLvl 70.0 · 2 enchant gaps")
        r, _ = gear.sheet_rows([{
            "name": "Snore", "class_name": "Warrior", "spec": "Protection",
            "submitted_at": "2026-11-01T00:00:00+00:00",
            "raw_text": raw,
        }], catalog)
        self.assertEqual(r[4][4], "Helm of Testing")
        self.assertEqual(r[4][7], 70)
        self.assertEqual(r[4][6], "https://www.wowhead.com/forever/item=100")
        self.assertEqual(r[5][7], "")
        self.assertIn("iLvl 70.0", r[3][3])
        # An unresolvable item should never gain an invented level.
        catalog.pop("102")
        self.assertEqual(gear.average_item_level(p, catalog),
                         (65.0, 2, 3))
        self.assertIn("(2/3 known)", gear.summary(p, catalog))

    def test_cache_load_from_offline_roster_then_still_attempts_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            cache = items._cache_path(db)
            with patch.dict(items._MEMO, {}, clear=True), \
                 patch.object(items, "_FETCH_ATTEMPTED", set()), \
                 patch.object(items, "_download",
                              return_value={"100": {"name": "Helm", "level": 70}}) as get:
                self.assertEqual(items.cached_catalog(db), {})
                self.assertEqual(items.catalog(db)["100"]["level"], 70)
                get.assert_called_once()
                self.assertTrue(cache.exists())
                self.assertEqual(items.catalog(db)["100"]["level"], 70)
                get.assert_called_once()
