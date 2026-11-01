"""Backward compatibility checks for WoW: Forever reference modules.

Character race restrictions and raid buff calculations must be identical
before and after moving them to the independent Hyped Ogre package.
"""
import unittest

from cogs import forever_races as legacy_races
from cogs import forever_raid_buffs as legacy_buffs
from hyped_ogre.features.characters import races
from hyped_ogre.features.raids import buffs


class WoWReferenceMigrationTests(unittest.TestCase):
    def test_race_data_uses_same_objects_from_old_imports(self):
        for name in ("RACES", "STARTING_ZONES", "RACE_CLASSES"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy_races, name), getattr(races, name))

    def test_character_restrictions_and_starting_zones_preserved(self):
        self.assertEqual(races.RACES, (
            "Orc", "Troll", "Tauren", "Undead", "Skyborne (Windshaper)",
        ))
        self.assertEqual(races.STARTING_ZONES["Skyborne (Windshaper)"],
                         "Zephras Isle")
        self.assertIn("Paladin", races.RACE_CLASSES["Undead"])
        self.assertNotIn("Paladin", races.RACE_CLASSES["Orc"])
        self.assertEqual(set(races.RACES), set(races.RACE_CLASSES))
        self.assertEqual(set(races.RACES), set(races.STARTING_ZONES))

    def test_buff_exports_have_identical_implementation(self):
        for name in ("CLASS_COVERAGE", "SPEC_COVERAGE", "_selected",
                     "missing_entries", "missing_coverage",
                     "instance_summaries"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy_buffs, name), getattr(buffs, name))

    def test_missing_coverage_still_has_nine_conservative_checks(self):
        self.assertEqual(len(buffs.CLASS_COVERAGE), 6)
        self.assertEqual(len(buffs.SPEC_COVERAGE), 3)
        self.assertEqual(len(buffs.missing_entries([])), 9)
        self.assertEqual(buffs.missing_coverage([]), "No team selected")
        self.assertEqual(len(buffs.missing_entries([
            ("mage", "fire"), ("priest", "holy"),
        ])), 7)

    def test_raid_composition_summary_keeps_four_dashboard_rows(self):
        event = {"instance_1": "Barrows", "instance_2": "Hyjal",
                 "instance_3": "Ony"}
        output = buffs.instance_summaries([], event)
        self.assertEqual(len(output), 4)
        self.assertEqual(output[0], ("Barrows 🔸 (9)", "No team selected"))
        self.assertEqual(output[1], ("Barrows 🔹 (9)", "No team selected"))
        self.assertEqual(output[2], ("Hyjal (9)", "No team selected"))
        self.assertEqual(output[3], ("Ony (9)", "No team selected"))


if __name__ == "__main__":
    unittest.main()
