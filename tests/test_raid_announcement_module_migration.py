"""Compatibility and idempotence guards for raid announcements."""

import importlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from cogs import raid_acknowledgements as old_store
from cogs import raid_announcer as old_announcer
from cogs import raid_schedule, raid_signup
from hyped_ogre.features.raids import acknowledgements as store
from hyped_ogre.features.raids import announcer


class RaidAnnouncementModuleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid_signup.db")
        raid_signup.create_schema(self.path)
        raid_schedule.ensure_raid_fields(self.path)
        raid_schedule.save_raid(self.path, 1, "2026-12-09 21:00", "Hyjal (20m)")

    def test_legacy_imports_are_exact_same_module_objects(self):
        self.assertIs(old_store, store)
        self.assertIs(old_announcer, announcer)
        self.assertIs(importlib.import_module("cogs.raid_acknowledgements"), store)
        self.assertIs(importlib.import_module("cogs.raid_announcer"), announcer)
        self.assertIs(announcer.store, store)
        for name in (
            "announcement", "members", "pending", "record_announcement",
            "acknowledge", "retract", "is_retracted", "render",
            "render_retracted", "dirty_announcements", "mark_refreshed",
        ):
            self.assertIs(getattr(old_store, name), getattr(store, name))
        for name in ("announce", "refresh", "reaction", "retry_dirty"):
            self.assertIs(getattr(old_announcer, name), getattr(announcer, name))

    def test_single_post_and_acknowledgement_do_not_change_signup(self):
        store.record_announcement(
            self.path, 1, 123, 456,
            [("42", "Snore"), ("43", "Mage"), ("42", "Snore")],
        )
        self.assertEqual(store.announcement(self.path, 1), (123, 456))
        self.assertEqual(store.pending(self.path, 1), [
            ("42", "Snore"), ("43", "Mage"),
        ])
        with self.assertRaisesRegex(ValueError, "already announced"):
            store.record_announcement(self.path, 1, 123, 999, [("42", "Snore")])
        self.assertIsNone(store.acknowledge(self.path, 123, 999, 42))
        self.assertIsNone(store.acknowledge(self.path, 123, 456, 999))
        self.assertEqual(store.acknowledge(self.path, 123, 456, 42), 1)
        self.assertIsNone(store.acknowledge(self.path, 123, 456, 42))
        self.assertEqual(store.dirty_announcements(self.path), [1])
        self.assertNotIn("<@42>", store.render(
            1, store.pending(self.path, 1), 2,
        ))
        self.assertIn("<@43>", store.render(
            1, store.pending(self.path, 1), 2,
        ))
        store.mark_refreshed(self.path, 1)
        self.assertEqual(store.dirty_announcements(self.path), [])
        with raid_schedule.database(self.path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM signups").fetchone()[0], 0)

    def test_retraction_is_permanent_and_never_includes_mentions(self):
        store.record_announcement(self.path, 1, 123, 456, [("42", "Snore")])
        self.assertTrue(store.retract(self.path, 1))
        self.assertFalse(store.retract(self.path, 1))
        self.assertTrue(store.is_retracted(self.path, 1))
        self.assertIsNone(store.acknowledge(self.path, 123, 456, 42))
        text = store.render_retracted(1, "https://discord.com/channels/1/123/456")
        self.assertIn("Roster withdrawn", text)
        self.assertNotIn("<@42>", text)
        self.assertIn("View raid panel", text)
        self.assertEqual(store.dirty_announcements(self.path), [1])


class RaidAnnouncerSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_announcement_requires_published_active_roster_without_sending(self):
        bot = SimpleNamespace()
        for event in (
            None,
            {"roster_published": 0},
            {"roster_published": 1, "archived_at": "2026-12-10"},
        ):
            with self.subTest(event=event), patch.object(
                announcer, "read_raid", return_value=event
            ):
                with self.assertRaisesRegex(ValueError, "Publish an active roster"):
                    await announcer.announce(bot, "nonexistent.db", 1, 100)

    async def test_other_guilds_and_reactions_cannot_acknowledge(self):
        bot = SimpleNamespace(user=SimpleNamespace(id=99))
        payload = SimpleNamespace(
            guild_id=123, emoji="✅", user_id=42,
            channel_id=50, message_id=60,
        )
        with patch.object(store, "acknowledge") as mark:
            self.assertFalse(await announcer.reaction(bot, "nonexistent.db", payload, 999))
            mark.assert_not_called()
            payload.guild_id = 999
            payload.emoji = "❌"
            self.assertFalse(await announcer.reaction(bot, "nonexistent.db", payload, 999))
            mark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
