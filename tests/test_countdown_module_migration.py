"""Offline guards for the Countdown clock module migration.

Never start Discord, send reminders or access a persistent database here.
"""
import importlib
from datetime import datetime, timezone
import unittest

from cogs import countdown_core as legacy
from hyped_ogre.features.countdowns import core


class CountdownModuleMigrationTests(unittest.TestCase):
    def test_legacy_import_is_same_module_and_public_functions(self):
        self.assertIs(legacy, core)
        self.assertIs(importlib.import_module("cogs.countdown_core"), core)
        for name in (
            "UK", "parse_uk_datetime", "upcoming_string",
            "remaining_hours_string", "next_cycle", "pre_reminder_due",
            "due_reminder_due", "render_key", "validate_image_url",
            "uk_display",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(core, name))

    def test_uk_time_and_dst_behaviour_are_preserved(self):
        self.assertEqual(core.parse_uk_datetime("2026-11-04 23:00"),
                         int(datetime(2026, 11, 4, 23, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(core.parse_uk_datetime("2026-07-01 19:00"),
                         int(datetime(2026, 7, 1, 18, tzinfo=timezone.utc).timestamp()))
        for bad in ("2026-03-29 01:30", "2026-10-25 01:30", "invalid"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                legacy.parse_uk_datetime(bad)

    def test_repeat_and_reminder_idempotence(self):
        self.assertEqual(core.next_cycle(100, 3600, 7400), 10900)
        self.assertTrue(core.pre_reminder_due(3600, 1800, 30, 0))
        self.assertFalse(core.pre_reminder_due(3600, 1800, 30, 3600))
        self.assertTrue(core.due_reminder_due(3600, 3600, 0))
        self.assertFalse(core.due_reminder_due(3600, 3600, 3600))
        self.assertEqual(core.render_key(3650, 10, 1),
                         legacy.render_key(3650, 10, 1))

    def test_image_validation_and_countdown_text(self):
        self.assertEqual(core.upcoming_string(90061, 0), "1d 01h 02m")
        self.assertEqual(core.upcoming_string(100, 100), "00m · Ready")
        self.assertEqual(core.validate_image_url("https://i.vgy.me/example.png"),
                         "https://i.vgy.me/example.png")
        for unsafe in (
            "http://example.com/test.png",
            "file:///etc/passwd",
            "https://user:secret@example.com/icon.png",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                legacy.validate_image_url(unsafe)


if __name__ == "__main__":
    unittest.main()
