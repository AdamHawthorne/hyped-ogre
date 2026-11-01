"""Schedule-first raid poster, advanced options, lifecycle and command footer."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import discord

from cogs import raid_poster
from cogs import raid_schedule as schedule
from cogs import raid_signup as raid


class RaidPosterWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.cog = SimpleNamespace(
            raid_db_path=lambda: self.path,
            allowed=lambda _interaction: True,
            post_panel=AsyncMock(),
            sync_raid_sheets=AsyncMock(),
            refresh_saved_panel=AsyncMock(),
        )

    @staticmethod
    def buttons(view):
        return {x.label: x for x in view.walk_children()
                if isinstance(x, discord.ui.Button)}

    def test_sunday_defaults_and_future_existing_raid(self):
        now = datetime(2026, 11, 1, 10, 0, tzinfo=ZoneInfo("Europe/London"))
        self.assertEqual(raid_poster.next_sunday_date(now=now), "2026-11-08")
        self.assertEqual(
            raid_poster.next_sunday_date(
                [{"raid_at": "2026-12-09T21:00:00+00:00"}], now=now
            ), "2026-12-13"
        )
        initial = raid_poster.RaidPosterView(self.cog, 42)
        self.assertEqual(initial.draft["instance_1"], "Hyjal (20m)")
        self.assertEqual(initial.draft["instance_2"], "Barrows (10m)")
        self.assertEqual(initial.draft["wowsims_policy"], "missing")
        self.assertEqual(initial.draft["channel_id"],
                         raid_poster.DEFAULT_RAID_CHANNEL_ID)
        self.assertEqual(initial.draft["time"], "20:00")
        self.assertIsNone(initial.draft["end_time"])
        self.assertEqual(initial.draft["ping_role_ids"], [])
        self.assertLessEqual(initial.total_children_count, 40)
        event = schedule.save_raid(
            self.path, 8, "2026-12-09 21:00", "Hyjal (20m)"
        )
        schedule.record_panel(self.path, 8, 777, 888)
        following = raid_poster.RaidPosterView(self.cog, 42)
        self.assertEqual(following.draft["number"], 9)
        self.assertEqual(following.draft["day"], "2026-12-13")
        self.assertEqual(following.draft["channel_id"], 777)

    def test_schedule_vs_advanced_controls_and_bottom_commands(self):
        schedule_view = raid_poster.RaidPosterView(self.cog, 42)
        basic = json.dumps(schedule_view.to_components())
        self.assertIn("Target roster confirmation (manual)", basic)
        self.assertIn("Raid instance #2", basic)
        self.assertNotIn("Choose end time", basic)
        self.assertIn("Advanced settings", basic)
        self.assertNotIn("Delete Raid", basic)
        self.assertEqual(
            self.buttons(schedule_view)["Preview / Confirm Roster"].disabled,
            True,
        )
        self.assertIn("Raid commands",
                      schedule_view.to_components()[-1]["components"][0]["content"])
        self.assertIn("/raid reopen",
                      schedule_view.to_components()[-1]["components"][0]["content"])
        self.assertNotIn("Won't", schedule_view.description())
        advanced = raid_poster.RaidPosterView(
            self.cog, 42, schedule_view.draft, page="advanced"
        )
        more = json.dumps(advanced.to_components())
        self.assertIn("Choose end time", more)
        self.assertIn("WoWSims requirement", more)
        self.assertIn("Raid instance #3", more)
        self.assertIn("Choose destination channel", more)
        self.assertIn("Roles to ping", more)
        self.assertIn("Optional SoftRes", more)
        self.assertIn("Check Message Link", more)
        self.assertIn("Back to schedule", more)
        self.assertIn("/raid panel",
                      advanced.to_components()[-1]["components"][0]["content"])
        self.assertLessEqual(advanced.total_children_count, 40)

    async def test_navigation_preserves_unposted_changes(self):
        initial = raid_poster.RaidPosterView(self.cog, 42)
        initial.draft["softres_url"] = "https://softres.it/raid/example"
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(edit_message=AsyncMock())
        )
        await self.buttons(initial)["Advanced settings"].callback(interaction)
        advanced = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(advanced.page, "advanced")
        self.assertEqual(advanced.draft["softres_url"],
                         "https://softres.it/raid/example")
        advanced.draft["wowsims_policy"] = "fresh"
        await self.buttons(advanced)["Back to schedule"].callback(interaction)
        schedule_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(schedule_view.page, "schedule")
        self.assertEqual(schedule_view.draft["wowsims_policy"], "fresh")
        self.assertEqual(schedule_view.draft["softres_url"],
                         "https://softres.it/raid/example")

    async def test_post_refreshes_private_card_and_enables_next_steps(self):
        initial = raid_poster.RaidPosterView(self.cog, 42)
        private_message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            message=private_message,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        async def save_post(_interaction, event, **kwargs):
            schedule.record_panel(self.path, event["raid_id"], 777, 888)
        self.cog.post_panel.side_effect = save_post
        await self.buttons(initial)["Post / Update Raid"].callback(interaction)
        self.cog.post_panel.assert_awaited_once()
        updated = private_message.edit.await_args.kwargs["view"]
        self.assertEqual(updated.page, "schedule")
        self.assertIn("Collecting sign-ups", updated.description())
        self.assertFalse(self.buttons(updated)["Preview / Confirm Roster"].disabled)
        self.assertFalse(self.buttons(updated)["Manage Raid"].disabled)
        self.assertEqual(schedule.read_raid(self.path, 1)["signup_open"], 1)

    async def test_publishing_refreshes_private_card_to_managing_stage(self):
        schedule.save_raid(self.path, 1, "2026-12-09 21:00", "Hyjal (20m)")
        schedule.record_panel(self.path, 1, 777, 888)
        poster_message = SimpleNamespace(edit=AsyncMock())
        chosen = [("42", "Snore", "Hyjal")]
        preview = raid_poster.RaidRosterPreview(
            self.cog, 42, 1, chosen,
            poster_message=poster_message,
            poster_draft=raid_poster.draft_from_event(
                schedule.read_raid(self.path, 1)
            ),
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        from cogs import raid_sheets_v3
        with patch.object(raid_sheets_v3, "read_selected",
                          return_value=chosen):
            await preview._publish(interaction, notify=False)
        self.assertEqual(schedule.read_raid(self.path, 1)["roster_published"], 1)
        updated = poster_message.edit.await_args.kwargs["view"]
        self.assertIn("Roster published", updated.description())
        self.assertFalse(self.buttons(updated)["Manage Raid"].disabled)
        self.cog.sync_raid_sheets.assert_awaited_once()
        self.cog.refresh_saved_panel.assert_awaited_once_with(1)
