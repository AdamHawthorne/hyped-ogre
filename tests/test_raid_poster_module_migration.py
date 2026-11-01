"""Regression guards for the leadership raid poster module migration.

Use temporary raid databases only. Views are rendered locally without
Discord API calls, Sheets access or production settings.
"""

import importlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import discord

from cogs import raid_poster as legacy
from cogs import raid_schedule as schedule
from cogs import raid_signup as raid
from hyped_ogre.features.raids import poster


class RaidPosterModuleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "raid_signup.db")
        raid.create_schema(self.db)
        schedule.ensure_raid_fields(self.db)
        self.cog = SimpleNamespace(
            raid_db_path=lambda: self.db,
            allowed=lambda interaction: True,
            post_panel=AsyncMock(),
            sync_raid_sheets=AsyncMock(),
            refresh_saved_panel=AsyncMock(),
        )

    def test_old_and_new_import_paths_are_identical(self):
        self.assertIs(legacy, poster)
        self.assertIs(importlib.import_module("cogs.raid_poster"), poster)
        for name in (
            "DEFAULT_RAID_CHANNEL_ID", "INSTANCES", "TIMES",
            "WOWSIMS_POLICIES", "ANNOUNCEMENTS", "next_sunday_date",
            "draft_from_event", "poster_schedule", "poster_end_at",
            "RaidPosterView", "RaidPostConfirmation", "RaidRosterPreview",
            "RaidDeleteConfirmation", "PosterTextModal",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(poster, name))

    def test_fixed_sunday_calculation_and_new_raid_defaults(self):
        now = datetime(2026, 11, 1, 10, tzinfo=ZoneInfo("Europe/London"))
        self.assertEqual(poster.next_sunday_date(now=now), "2026-11-08")
        self.assertEqual(
            poster.next_sunday_date(
                [{"raid_at": "2026-12-09T21:00:00+00:00"}], now=now
            ), "2026-12-13",
        )
        default = legacy.draft_from_event(None)
        self.assertEqual(default["instance_1"], "Hyjal (20m)")
        self.assertEqual(default["instance_2"], "Barrows (10m)")
        self.assertEqual(default["time"], "20:00")
        self.assertEqual(default["wowsims_policy"], "missing")
        self.assertTrue(default["default_show_discord"])
        self.assertEqual(default["ping_role_ids"], [])

    def test_saved_raid_draft_preserves_optional_fields_and_wowsims_policy(self):
        event = schedule.save_raid(
            self.db, 9, "2026-12-09 21:00", "Hyjal (20m)",
            "Barrows (10m)", custom_title="Clear practice",
            wowsims_policy="optional",
        )
        view = poster.RaidPosterView(
            self.cog, 42, poster.draft_from_event(event)
        )
        self.assertIsInstance(view, legacy.RaidPosterView)
        self.assertEqual(view.draft["number"], 9)
        self.assertEqual(view.draft["instance_2"], "Barrows (10m)")
        self.assertEqual(view.draft["wowsims_policy"], "optional")
        self.assertEqual(view.draft["custom_title"], "Clear practice")
        self.assertIn("Not posted", view.description())
        self.assertEqual(schedule.read_raid(self.db, 9)["roster_published"], 0)

    def test_private_schedule_advanced_navigation_still_fits_discord(self):
        basic = legacy.RaidPosterView(self.cog, 42)
        self.assertIsInstance(basic, discord.ui.LayoutView)
        self.assertEqual(basic.timeout, 900)
        self.assertLessEqual(basic.total_children_count, 40)
        self.assertIn("Advanced settings", str(basic.to_components()))
        self.assertIn("Raid instance #2", str(basic.to_components()))
        self.assertNotIn("Choose end time", str(basic.to_components()))
        advanced = poster.RaidPosterView(
            self.cog, 42, basic.draft, page="advanced"
        )
        self.assertLessEqual(advanced.total_children_count, 40)
        self.assertIn("Choose end time", str(advanced.to_components()))
        self.assertIn("WoWSims requirement", str(advanced.to_components()))
        self.assertIn("Back to schedule", str(advanced.to_components()))
        self.assertIn("Roles to ping", str(advanced.to_components()))
        self.assertFalse(advanced.authorised(
            SimpleNamespace(user=SimpleNamespace(id=99))
        ))

    def test_legacy_patch_targets_same_canonical_module(self):
        event = schedule.save_raid(
            self.db, 1, "2026-12-09 21:00", "Hyjal (20m)"
        )
        with patch.object(legacy, "read_raid", return_value={"raid_id": 99}):
            self.assertEqual(poster.read_raid(self.db, 1)["raid_id"], 99)
        self.assertEqual(poster.read_raid(self.db, 1)["raid_id"], 1)
        self.assertIsNone(schedule.read_raid(self.db, 1)["panel_message_id"])
        self.cog.post_panel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
