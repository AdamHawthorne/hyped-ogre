"""Compatibility and read-only safety for relocated WoWSims equipment."""

import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cogs import wowsims_gear as legacy
from hyped_ogre.features.characters import equipment as canonical
from cogs import raid_signup as raid, raid_schedule as schedule


def sample_export(level=15):
    return json.dumps({
        "name": "Horrible", "class": "rogue", "spec": "combat",
        "level": level, "version": "v3.2.6", "race": "Undead",
        "gear": {"items": [
            {"id": 10638, "enchant": 15}, None, {"id": 15010},
            None, {"id": 4928}, None, {"id": 6521}
        ]},
    })


class WoWSimsEquipmentModuleMigrationTests(unittest.TestCase):
    def test_legacy_and_canonical_are_the_same_python_module(self):
        self.assertIs(legacy, canonical)
        self.assertIs(importlib.import_module("cogs.wowsims_gear"), canonical)
        for name in ("SLOTS", "HEADERS", "SHEET_NAME", "ENCHANT_SLOTS",
                     "parse_export", "latest_profiles", "average_item_level",
                     "enchant_gaps", "summary", "sheet_rows"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_export_parsing_still_uses_fixed_seventeen_slots(self):
        data = canonical.parse_export(sample_export())
        self.assertEqual(len(data["slots"]), 17)
        self.assertEqual(data["slots"][0]["id"], 10638)
        self.assertEqual(data["slots"][0]["enchant"], 15)
        self.assertEqual(data["slots"][2]["id"], 15010)
        self.assertEqual(data["slots"][16]["slot"], "Ranged")
        self.assertEqual(data["level"], 15)
        self.assertEqual(canonical.summary(data), "iLvl ? (0/4 known) · 3 enchant gaps")
        self.assertEqual(legacy.summary(data), canonical.summary(data))

    def test_unknown_item_levels_and_urls_are_not_invented(self):
        profile = canonical.parse_export(sample_export())
        average, known, equipped = canonical.average_item_level(profile, {})
        self.assertIsNone(average)
        self.assertEqual((known, equipped), (0, 4))
        records = [{
            "name": "Horrible", "class_name": "Rogue", "spec": "Combat",
            "submitted_at": "2026-11-01T19:00:00+00:00",
            "raw_text": sample_export(),
        }]
        rows, headings = canonical.sheet_rows(records)
        self.assertEqual(headings, [3])
        self.assertEqual(rows[4][4], "Item #10638")
        self.assertEqual(rows[4][6], "https://www.wowhead.com/forever/item=10638")
        self.assertEqual(rows[4][7], "")
        self.assertEqual(rows, legacy.sheet_rows(records)[0])

    def test_latest_saved_export_is_read_only_and_keeps_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "raid_signup.db")
            raid.create_schema(path)
            schedule.ensure_raid_fields(path)
            raid.save_character(42, "Horrible", "Rogue", "Combat", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            raid.save_wowsims_export(42, char_id, sample_export(15), path)
            raid.save_wowsims_export(42, char_id, sample_export(60), path)
            with sqlite3.connect(path) as db:
                before = db.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0]
            profiles = canonical.latest_profiles(path)
            self.assertEqual(len(profiles), 1)
            self.assertEqual(canonical.parse_export(profiles[0]["raw_text"])["level"], 60)
            self.assertEqual(profiles, legacy.latest_profiles(path))
            with sqlite3.connect(path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM gear_exports").fetchone()[0],
                    before,
                )


if __name__ == "__main__":
    unittest.main()
