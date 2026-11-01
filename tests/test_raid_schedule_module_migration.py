"""Raid scheduling migration guards for saved data and persistent panels.

All database operations are restricted to freshly-created temporary SQLite
files. Discord panels are instantiated offline; no API or token is used.
"""

import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discord

from cogs import raid_schedule as legacy
from cogs import raid_signup
from hyped_ogre.features.raids import schedule as canonical


class RaidScheduleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid_signup.db")
        raid_signup.create_schema(self.path)
        canonical.ensure_raid_fields(self.path)

    def create_raid(self, number=7):
        return canonical.save_raid(
            self.path, number, "2026-12-09 21:00",
            "Hyjal (20m)", "Barrows (10m)",
            custom_title="Guild raid", wowsims_policy="optional",
        )

    def test_both_import_paths_refer_to_exact_same_module_and_views(self):
        self.assertIs(legacy, canonical)
        self.assertIs(importlib.import_module("cogs.raid_schedule"), canonical)
        for name in (
            "_UNCHANGED", "RAID_IMAGE", "RAID_PANEL_IMAGE",
            "database", "ensure_raid_fields", "save_raid", "read_raid",
            "raid_status", "confirmed_roster", "read_signup_roster",
            "read_public_roster", "record_panel", "raid_panel_intro",
            "raid_panel_text", "roster_groups", "signup_role_group",
            "RaidEventPanel", "list_raids", "delete_raid",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_existing_database_and_roster_lifecycle_are_unchanged(self):
        event = self.create_raid()
        self.assertEqual(event["raid_id"], 7)
        self.assertEqual(event["instance_1"], "Hyjal (20m)")
        self.assertEqual(event["instance_2"], "Barrows (10m)")
        self.assertEqual(event["wowsims_policy"], "optional")
        self.assertEqual(event["signup_open"], 1)
        self.assertFalse(event["roster_published"])
        self.assertEqual(legacy.read_raid(self.path, 7), event)

        approved = [("42", "Snore", "Hyjal", "Warrior", "Tank", "Protection")]
        confirmed = canonical.raid_status(
            self.path, 7, "confirm", confirmed_entries=approved,
        )
        self.assertEqual(confirmed["signup_open"], 1)
        self.assertEqual(confirmed["roster_published"], 1)
        self.assertEqual(legacy.confirmed_roster(self.path, 7), [("Snore", "Hyjal")])

        reopened = canonical.raid_status(self.path, 7, "reopen")
        self.assertEqual(reopened["signup_open"], 1)
        self.assertFalse(reopened["roster_published"])
        self.assertEqual(canonical.confirmed_roster(self.path, 7), [])

        canonical.raid_status(self.path, 7, "confirm", confirmed_entries=approved)
        archived = canonical.raid_status(
            self.path, 7, "archive", notes="Clear complete",
        )
        self.assertEqual(archived["signup_open"], 0)
        self.assertEqual(archived["archive_notes"], "Clear complete")
        with self.assertRaisesRegex(ValueError, "read-only"):
            canonical.save_raid(self.path, 7, "2026-12-10 21:00", "Hyjal (20m)")
        self.assertEqual(len(legacy.list_raids(self.path)), 1)

    def test_raid_panel_has_original_persistent_discord_ids(self):
        event = self.create_raid()
        async def handle(_interaction):
            return None

        panel = legacy.RaidEventPanel(
            event, on_signup=handle, on_update=handle,
            on_gear=handle, on_absent=handle, on_remind=handle,
        )
        self.assertIsInstance(panel, discord.ui.LayoutView)
        self.assertIsInstance(panel, canonical.RaidEventPanel)
        self.assertIsNone(panel.timeout)
        self.assertTrue(panel.is_persistent())
        controls = {
            child.label: child.custom_id for child in panel.walk_children()
            if isinstance(child, discord.ui.Button)
        }
        self.assertEqual(
            set(controls.values()),
            {
                "hyped_raid_7_signup", "hyped_raid_7_reminder",
                "hyped_raid_7_update", "hyped_raid_7_gear",
                "hyped_raid_7_absent",
            },
        )
        self.assertIn("<Hyped> Raid #7", canonical.raid_panel_intro(event))
        self.assertIn("WoWSims:", legacy.raid_panel_intro(event))
        self.assertNotIn("### Roster", legacy.raid_panel_text(event))

    def test_no_implicit_public_selection_and_survival_hunter_remains_melee(self):
        self.create_raid()
        self.assertEqual(canonical.read_signup_roster(self.path, 7), [])
        self.assertEqual(canonical.read_public_roster(self.path, 7), [])
        self.assertEqual(canonical.signup_role_group("Hunter", "Survival", "DPS"), "Melee")
        self.assertEqual(canonical.signup_role_group("Hunter", "Marksmanship", "DPS"), "Ranged")
        self.assertEqual(canonical.signup_role_group("Warrior", "Protection", "Tank"), "Tanks")
        self.assertEqual(canonical.signup_role_group("Druid", "Restoration", "Healer"), "Healers")

    def test_old_patches_affect_canonical_database_access(self):
        self.create_raid()
        with patch.object(legacy, "read_raid", return_value={"raid_id": 999}):
            self.assertEqual(canonical.read_raid(self.path, 7), {"raid_id": 999})
        self.assertEqual(canonical.read_raid(self.path, 7)["raid_id"], 7)


if __name__ == "__main__":
    unittest.main()
