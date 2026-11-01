"""WoWSims equipment presentation is safe, readable and does not sim."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cogs import raid_signup as raid, raid_schedule as schedule
from cogs import raid_sheets_v3 as sheets, wowsims_gear as gear
from wowsims_helper import example_export


def export(name="Horrible"):
    return json.dumps({
        "name": name, "class": "rogue", "level": 15,
        "version": "v3.2.6", "spec": "combat", "race": "Undead",
        "professions": [{"name": "Alchemy", "level": 34}],
        "gear": {"items": [
            {"id": 10638, "enchant": 15}, None, {"id": 15010}, None,
            {"id": 4928}, None, {"id": 6521}, None, {"id": 2962},
            {"id": 4789}, None, None, None, None,
            {"id": 4569, "random_suffix": 3}, {"id": 2027}, {"id": 3131},
        ]},
    }, separators=(",", ":"))


class GearParserTests(unittest.TestCase):
    def test_export_parses_level_slot_ids_enchants_professions(self):
        data = gear.parse_export(export())
        self.assertEqual(data["level"], 15)
        self.assertEqual(data["equipped"], 9)
        self.assertEqual(data["enchanted"], 1)
        self.assertEqual(data["slots"][0], {
            "slot": "Head", "id": 10638,
            "enchant": 15, "random_suffix": None,
        })
        self.assertEqual(data["slots"][14]["slot"], "Main Hand")
        self.assertEqual(data["slots"][14]["random_suffix"], 3)
        self.assertEqual(data["slots"][15]["slot"], "Off Hand")
        self.assertEqual(data["professions"], ["Alchemy"])
        self.assertEqual(gear.summary(data),
                         "Lv 15 · 9/17 equipped · 1 enchants reported")
        self.assertIsNone(gear.parse_export('{"invalid":true}'))
        self.assertIsNone(gear.parse_export("not-json"))

    def test_sheet_rows_contain_clickable_numeric_urls_not_false_enchant_flags(self):
        rows, headings = gear.sheet_rows([{
            "name": "Horrible", "class_name": "Rogue", "spec": "Combat",
            "submitted_at": "2026-11-01T01:00:00+00:00",
            "raw_text": export(),
        }])
        self.assertEqual(headings, [3])
        self.assertEqual(rows[3][0], "Horrible")
        self.assertEqual(rows[3][1], 15)
        self.assertEqual(rows[4][3], "Head")
        self.assertEqual(rows[4][4], "Item #10638")
        self.assertEqual(rows[4][5], "Enchant #15")
        self.assertEqual(rows[4][6],
                         "https://www.wowhead.com/classic/item=10638")
        self.assertEqual(rows[5][3], "Neck")
        self.assertEqual(rows[5][4], "Empty")
        self.assertEqual(rows[5][5], "—")
        self.assertEqual(rows[6][3], "Shoulders")
        self.assertEqual(rows[6][5], "Not reported")
        self.assertNotIn("item level", gear.summary(gear.parse_export(export())).lower())
        self.assertNotIn(export(), json.dumps(rows))

    def test_bad_ids_are_not_used_in_links_or_enchant_counts(self):
        sample = json.loads(export())
        sample["gear"]["items"][0] = {"id": "-5", "enchant": -2}
        sample["gear"]["items"][2] = {"id": "1.2", "enchant": "999"}
        values = gear.parse_export(json.dumps(sample))
        self.assertIsNone(values["slots"][0]["id"])
        self.assertIsNone(values["slots"][2]["id"])
        self.assertIsNone(gear._positive_id(True))
        self.assertTrue(all(
            slot["id"] is None or slot["id"] > 0
            for slot in values["slots"]
        ))


class GearSheetSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "test.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        raid.save_character(42, "Horrible", "Rogue", "Combat", "DPS",
                            self.path)
        self.cid = raid.member_characters(42, self.path)[0][0]
        raid.save_wowsims_export(42, self.cid, export(), self.path)

    def test_latest_snapshot_is_used_and_deleted_characters_disappear(self):
        items = gear.latest_profiles(self.path)
        self.assertEqual(len(items), 1)
        self.assertIn('"level":15', items[0]["raw_text"])
        newer = json.loads(export())
        newer["level"] = 25
        raid.save_wowsims_export(42, self.cid, json.dumps(newer), self.path)
        data = gear.latest_profiles(self.path)
        self.assertEqual(gear.parse_export(data[0]["raw_text"])["level"], 25)

    def test_sheet_created_once_and_skips_unchanged_updates(self):
        fake = Mock()
        api_replies = []
        def endpoint(_session, method, url, **kwargs):
            api_replies.append((method, url, kwargs))
            if "batchUpdate" in url and "addSheet" in str(kwargs):
                return {"replies": [{"addSheet": {
                    "properties": {"sheetId": 71234}
                }}]}
            return {}
        titles = {}
        with patch.object(sheets.raid_sheets, "_json", side_effect=endpoint), \
             patch.dict(sheets._GEAR_TAB_SNAPSHOT, {}, clear=True):
            sheets._sync_gear_tab(fake, self.path, titles)
            self.assertIn("Forever Gear", titles)
            self.assertEqual(titles["Forever Gear"]["sheetId"], 71234)
            writes = [call for call in api_replies if call[0] == "PUT"]
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0][2]["params"]["valueInputOption"], "RAW")
            self.assertTrue(any(
                "https://www.wowhead.com/classic/item=10638" in json.dumps(call)
                for call in writes
            ))
            first = len(api_replies)
            sheets._sync_gear_tab(fake, self.path, titles)
            self.assertEqual(len(api_replies), first)
            changed = json.loads(export())
            changed["level"] = 30
            raid.save_wowsims_export(42, self.cid, json.dumps(changed), self.path)
            sheets._sync_gear_tab(fake, self.path, titles)
            self.assertEqual(len([call for call in api_replies
                                  if call[0] == "PUT"]), 2)

    def test_raid_tab_gear_column_has_level_equipment_enchants(self):
        event = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00", "Hyjal (20m)"
        )
        raid.save_signup(1, 42, self.cid, self.path)
        body = sheets._rows(self.path, event, {})
        player = next(row for row in body if row[1] == "Horrible")
        self.assertEqual(player[15],
                         "Lv 15 · 9/17 equipped · 1 enchants reported")
