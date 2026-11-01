"""Regression guards for extracting raid signup and absence SQL writes.

Uses only temporary SQLite databases. Never sends Discord messages or
contacts Google Sheets or the live Fly.io volume.
"""

from contextlib import contextmanager
import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cogs import raid_schedule as schedule
from cogs import raid_signup as raid
from hyped_ogre.features.raids import signup_mutations


class RaidSignupAbsenceMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid_signup.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(
            self.path, 1, "2026-12-09 21:00", "Hyjal (20m)",
            wowsims_policy="optional",
        )
        raid.save_character(
            42, "Snore Mon", "Warrior", "Protection", "Tank", self.path,
        )
        self.cid = raid.member_characters(42, self.path)[0][0]

    def state(self):
        with raid.database(self.path) as db:
            return (
                db.execute(
                    "SELECT raid_id,discord_id,character_id,availability,note,submitted_at "
                    "FROM signups ORDER BY raid_id,discord_id"
                ).fetchall(),
                db.execute(
                    "SELECT raid_id,discord_id,note,signed_off_at "
                    "FROM raid_absences ORDER BY raid_id,discord_id"
                ).fetchall(),
            )

    def test_original_cog_owns_public_api_and_database_lifecycle(self):
        self.assertEqual(raid.RaidPrototype.__module__, "cogs.raid_signup")
        self.assertEqual(raid.setup.__module__, "cogs.raid_signup")
        self.assertEqual(
            list(inspect.signature(raid.save_signup).parameters),
            ["raid_id", "discord_id", "character_id", "path", "note"],
        )
        self.assertEqual(
            list(inspect.signature(raid.save_absence).parameters),
            ["raid_id", "discord_id", "note", "path"],
        )
        self.assertEqual(raid.save_signup.__defaults__, (raid.RAID_DB,))
        self.assertEqual(raid.save_signup.__kwdefaults__, {"note": None})
        self.assertEqual(raid.save_absence.__defaults__, ("", raid.RAID_DB))
        with patch.object(raid, "database", wraps=raid.database) as db, \
             patch.object(raid, "wowsims_requirement_for_signup",
                          return_value=None) as policy, \
             patch.object(signup_mutations, "record_signup",
                          wraps=signup_mutations.record_signup) as write:
            self.assertIsNone(raid.save_signup(
                1, 42, self.cid, self.path, note="Ready",
            ))
            db.assert_called_once_with(self.path)
            policy.assert_called_once_with(1, 42, self.cid, self.path)
            self.assertEqual(write.call_args.args[:1][0].__class__,
                             sqlite3.Connection)
        self.assertEqual(raid.my_signup(1, 42, self.path),
                         ("Snore Mon", "Available"))

    def test_notes_are_trimmed_and_repeat_signup_retains_time(self):
        raid.save_signup(1, 42, self.cid, self.path, note="  Ready at 21:00  ")
        first = self.state()[0][0]
        self.assertEqual(first[4], "Ready at 21:00")
        raid.save_signup(1, 42, self.cid, self.path)
        self.assertEqual(self.state()[0][0], first)
        raid.save_signup(1, 42, self.cid, self.path, note="")
        emptied = self.state()[0][0]
        self.assertEqual(emptied[4], "")
        self.assertEqual(emptied[5], first[5])
        with self.assertRaisesRegex(ValueError, "500"):
            raid.save_signup(1, 42, self.cid, self.path, note="x" * 501)
        self.assertEqual(self.state()[0][0], emptied)

    def test_absence_preserves_original_character_and_note_then_return_clears_it(self):
        raid.save_signup(1, 42, self.cid, self.path, note="Need to leave early")
        with raid.database(self.path) as db:
            db.execute(
                "UPDATE signups SET submitted_at='2020-01-01T00:00:00+00:00' "
                "WHERE raid_id=1 AND discord_id=42"
            )
        before = self.state()[0][0]
        signed_off = raid.save_absence(1, 42, "  Away today  ", self.path)
        absent_signup, absent_rows = self.state()
        self.assertEqual(absent_signup[0][2:5],
                         (self.cid, "Unavailable", "Need to leave early"))
        self.assertEqual(absent_signup[0][5], before[5])
        self.assertEqual(absent_rows[0][2:], ("Away today", signed_off))
        self.assertEqual(raid.my_absence(1, 42, self.path),
                         ("Away today", signed_off))
        raid.save_signup(1, 42, self.cid, self.path)
        returned, absent_rows = self.state()
        self.assertEqual(absent_rows, [])
        self.assertEqual(returned[0][2:5], (self.cid, "Available", None))
        self.assertNotEqual(returned[0][5], before[5])

    def test_missing_or_fresh_wowsims_enforced_without_erasing_existing_signup(self):
        raid.save_signup(1, 42, self.cid, self.path, note="Existing note")
        original = self.state()
        with patch.object(raid, "wowsims_requirement_for_signup",
                          return_value="missing") as policy:
            raid.save_signup(1, 42, self.cid, self.path)
            policy.assert_called_once_with(1, 42, self.cid, self.path)
        self.assertEqual(self.state(), original)
        raid.save_character(42, "Mage Alt", "Mage", "Frost", "DPS", self.path)
        alt = next(row[0] for row in raid.member_characters(42, self.path)
                   if row[1] == "Mage Alt")
        for need, phrase in (
            ("missing", "WoWSims export first"),
            ("fresh", "fresh WoWSims export"),
        ):
            with self.subTest(need=need), patch.object(
                raid, "wowsims_requirement_for_signup", return_value=need,
            ):
                with self.assertRaisesRegex(ValueError, phrase):
                    raid.save_signup(1, 42, alt, self.path)
                self.assertEqual(self.state(), original)
        with patch.object(raid, "wowsims_requirement_for_signup",
                          return_value=None):
            with self.assertRaisesRegex(ValueError, "own saved characters"):
                raid.save_signup(1, 99, self.cid, self.path)
        self.assertEqual(self.state(), original)

    def test_raid_state_guards_and_absence_without_character_remain_intact(self):
        stamp = raid.save_absence(1, 99, path=self.path)
        self.assertEqual(raid.my_absence(1, 99, self.path), (None, stamp))
        self.assertIsNone(raid.my_signup(1, 99, self.path))
        raid.save_absence(1, 99, "  Unavailable  ", self.path)
        self.assertEqual(raid.my_absence(1, 99, self.path)[0], "Unavailable")
        original = self.state()
        with self.assertRaisesRegex(ValueError, "500"):
            raid.save_absence(1, 99, "X" * 501, self.path)
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            raid.save_absence(999, 99, path=self.path)
        self.assertEqual(self.state(), original)
        with raid.database(self.path) as db:
            db.execute("UPDATE raids SET signup_open=0 WHERE raid_id=1")
        with self.assertRaisesRegex(ValueError, "aren't open"):
            raid.save_signup(1, 42, self.cid, self.path)
        with raid.database(self.path) as db:
            db.execute(
                "UPDATE raids SET archived_at='2026-12-10T01:00:00+00:00' "
                "WHERE raid_id=1"
            )
        with self.assertRaisesRegex(ValueError, "read-only"):
            raid.save_absence(1, 99, path=self.path)
        self.assertEqual(self.state(), original)

    def test_returning_from_absence_rolls_back_if_cleanup_fails(self):
        raid.save_signup(1, 42, self.cid, self.path, note="Existing note")
        raid.save_absence(1, 42, "Out", self.path)
        original = self.state()
        original_db = raid.database

        class FailAbsenceDelete:
            def __init__(self, con):
                self.con = con

            def execute(self, sql, *args, **kwargs):
                if sql.strip().startswith("DELETE FROM raid_absences"):
                    raise sqlite3.OperationalError("forced cleanup failure")
                return self.con.execute(sql, *args, **kwargs)

        @contextmanager
        def failing_database(path):
            with original_db(path) as con:
                yield FailAbsenceDelete(con)

        with patch.object(raid, "database", side_effect=failing_database):
            with self.assertRaisesRegex(sqlite3.OperationalError,
                                        "forced cleanup failure"):
                raid.save_signup(1, 42, self.cid, self.path)
        self.assertEqual(self.state(), original)

    def test_absence_rolls_back_if_updating_prior_signup_fails(self):
        raid.save_signup(1, 42, self.cid, self.path, note="Original")
        original = self.state()
        original_db = raid.database

        class FailAvailabilityChange:
            def __init__(self, con):
                self.con = con

            def execute(self, sql, *args, **kwargs):
                if "UPDATE signups SET availability='Unavailable'" in sql:
                    raise sqlite3.OperationalError("forced update failure")
                return self.con.execute(sql, *args, **kwargs)

        @contextmanager
        def failing_database(path):
            with original_db(path) as con:
                yield FailAvailabilityChange(con)

        with patch.object(raid, "database", side_effect=failing_database):
            with self.assertRaisesRegex(sqlite3.OperationalError,
                                        "forced update failure"):
                raid.save_absence(1, 42, "Out", self.path)
        self.assertEqual(self.state(), original)


if __name__ == "__main__":
    unittest.main()
