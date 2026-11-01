"""Per-raid WoWSims poster policies and Attend enforcement."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs import raid_poster, raid_schedule as schedule, raid_signup as raid
from wowsims_helper import example_export


class WoWSimsPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.raid = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00", "Hyjal (20m)"
        )
        raid.save_character(42, "Snore", "Warrior", "Protection", "Tank",
                            self.path)
        self.character = raid.member_characters(42, self.path)[0][0]

    def save_policy(self, policy, raid_id=1):
        return schedule.save_raid(
            self.path, raid_id, "2026-12-09 21:00", "Hyjal (20m)",
            wowsims_policy=policy,
        )

    async def test_new_and_migrated_raids_default_to_required_if_missing(self):
        self.assertEqual(self.raid["wowsims_policy"], "missing")
        self.assertIsNone(self.raid["wowsims_refresh_after"])
        self.assertEqual(raid_poster.draft_from_event(None)["wowsims_policy"],
                         "missing")
        self.assertEqual(raid_poster.draft_from_event(self.raid)["wowsims_policy"],
                         "missing")
        self.assertEqual(raid.wowsims_requirement_for_signup(
            1, 42, self.character, self.path), "missing")
        with self.assertRaisesRegex(ValueError, "WoWSims export first"):
            raid.save_signup(1, 42, self.character, self.path)
        raid.save_wowsims_export(42, self.character,
                                example_export("Snore"), self.path)
        self.assertIsNone(raid.wowsims_requirement_for_signup(
            1, 42, self.character, self.path))
        raid.save_signup(1, 42, self.character, self.path)
        self.assertEqual(raid.my_signup(1, 42, self.path),
                         ("Snore", "Available"))

    async def test_optional_mode_allows_new_characters_without_export(self):
        self.save_policy("optional")
        self.assertEqual(schedule.read_raid(self.path, 1)["wowsims_policy"],
                         "optional")
        self.assertIsNone(raid.wowsims_requirement_for_signup(
            1, 42, self.character, self.path))
        raid.save_signup(1, 42, self.character, self.path)
        self.assertFalse(raid.has_wowsims_export(42, self.character, self.path))
        self.save_policy("missing")
        # Leadership may enable the rule later without losing existing sign-ups.
        raid.save_signup(1, 42, self.character, self.path, note="Can stay late")
        self.assertEqual(raid.signup_note_details(1, 42, self.path)[2],
                         "Can stay late")
        raid.save_character(43, "NewMage", "Mage", "Frost", "DPS",
                            self.path)
        second = raid.member_characters(43, self.path)[0][0]
        with self.assertRaisesRegex(ValueError, "WoWSims export first"):
            raid.save_signup(1, 43, second, self.path)

    async def test_fresh_export_cutoff_stays_fixed_across_poster_edits(self):
        raid.save_wowsims_export(42, self.character,
                                example_export("Snore"), self.path)
        event = self.save_policy("fresh")
        cutoff = event["wowsims_refresh_after"]
        self.assertTrue(cutoff)
        self.assertEqual(raid.wowsims_requirement_for_signup(
            1, 42, self.character, self.path), "fresh")
        with self.assertRaisesRegex(ValueError, "fresh WoWSims export"):
            raid.save_signup(1, 42, self.character, self.path)
        again = self.save_policy("fresh")
        self.assertEqual(again["wowsims_refresh_after"], cutoff)
        # Older /raid edit commands must not reset the rule or cutoff.
        old_api = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00", "Hyjal (20m)"
        )
        self.assertEqual(old_api["wowsims_policy"], "fresh")
        self.assertEqual(old_api["wowsims_refresh_after"], cutoff)
        raid.save_wowsims_export(42, self.character,
                                example_export("Snore"), self.path)
        self.assertIsNone(raid.wowsims_requirement_for_signup(
            1, 42, self.character, self.path))
        raid.save_signup(1, 42, self.character, self.path)
        optional = self.save_policy("optional")
        self.assertIsNone(optional["wowsims_refresh_after"])
        reenabled = self.save_policy("fresh")
        self.assertTrue(reenabled["wowsims_refresh_after"])
        self.assertNotEqual(reenabled["wowsims_refresh_after"], cutoff)

    async def test_bad_policy_is_rejected_without_changing_raid(self):
        with self.assertRaisesRegex(ValueError, "valid WoWSims"):
            self.save_policy("none")
        self.assertEqual(schedule.read_raid(self.path, 1)["wowsims_policy"],
                         "missing")

    async def test_poster_contains_policy_settings_but_fits_component_limit(self):
        cog = SimpleNamespace(raid_db_path=lambda: self.path,
                              allowed=lambda _interaction: True)
        view = raid_poster.RaidPosterView(cog, 42,
                                         raid_poster.draft_from_event(self.raid))
        self.assertIn("WoWSims", view.description())
        pickers = [item for item in view.walk_children()
                   if isinstance(item, discord.ui.Select)]
        policy_picker = next(item for item in pickers
                             if "WoWSims" in item.placeholder)
        choices = {option.value for option in policy_picker.options}
        self.assertTrue({"wse:missing", "wse:fresh", "wse:optional",
                         "show:yes", "show:no"}.issubset(choices))
        self.assertLessEqual(view.total_children_count, 40)
        with patch.object(view, "changed", new_callable=AsyncMock) as changed:
            policy_picker._values = ["wse:fresh"]
            await policy_picker.callback(SimpleNamespace())
            changed.assert_awaited_once()
            self.assertEqual(changed.await_args.kwargs,
                             {"wowsims_policy": "fresh"})

    async def test_signup_ui_respects_optional_and_fresh_modes(self):
        row = raid.member_characters(42, self.path)[0]
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "member_characters", return_value=[row]), \
             patch.object(raid, "main_character_ids", return_value={self.character}), \
             patch.object(raid, "RAID_DB", self.path), \
             patch.object(raid, "read_raid", return_value={
                 "raid_id": 1, "roster_announce_at": None
             }):
            view = raid.CharacterActionView(42, raid_id=1, purpose="signup")
            picker = next(item for item in view.walk_children()
                          if isinstance(item, discord.ui.Select))
            picker._values = [str(self.character)]
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                response=SimpleNamespace(
                    edit_message=AsyncMock(), send_modal=AsyncMock()
                )
            )
            with patch.object(raid, "wowsims_requirement_for_signup",
                              return_value="fresh"):
                await picker.callback(interaction)
                self.assertIsInstance(
                    interaction.response.send_modal.await_args.args[0],
                    raid.WoWSimsExportModal
                )
            interaction.response.send_modal.reset_mock()
            with patch.object(raid, "wowsims_requirement_for_signup",
                              return_value=None):
                await picker.callback(interaction)
                self.assertIsInstance(
                    interaction.response.send_modal.await_args.args[0],
                    raid.SignupNoteModal
                )
            with patch.object(raid, "wowsims_requirement_for_signup",
                              return_value="missing"):
                await picker.callback(interaction)
                self.assertIsInstance(
                    interaction.response.edit_message.await_args.kwargs["view"],
                    raid.WoWSimsInstructionsView
                )


if __name__ == "__main__":
    unittest.main()
