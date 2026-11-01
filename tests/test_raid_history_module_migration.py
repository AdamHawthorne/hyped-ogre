"""Regression guards for moving raid attendance history into Hyped Ogre."""

import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from cogs import raid_history as legacy
from hyped_ogre.features.raids import history as canonical


class RaidHistoryModuleMigrationTests(unittest.TestCase):
    def test_original_imports_are_exact_canonical_functions(self):
        self.assertIs(legacy.STATES, canonical.STATES)
        self.assertIs(legacy.history_for_raid, canonical.history_for_raid)

    def test_history_preserves_attendance_precedence_and_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            with closing(sqlite3.connect(path)) as con, con:
                con.executescript("""
                    CREATE TABLE raids (raid_id INTEGER, raid_at TEXT);
                    CREATE TABLE signups (raid_id INTEGER, discord_id INTEGER, availability TEXT);
                    CREATE TABLE raid_absences (raid_id INTEGER, discord_id INTEGER);
                    CREATE TABLE attendance (raid_id INTEGER, discord_id INTEGER, status TEXT);
                    CREATE TABLE characters (discord_id INTEGER);
                    INSERT INTO raids VALUES (1,'2026-12-01 20:00');
                    INSERT INTO raids VALUES (2,'2026-12-04 20:00');
                    INSERT INTO raids VALUES (3,'2026-12-07 20:00');
                    INSERT INTO characters VALUES (42);
                    INSERT INTO signups VALUES (1,42,'Available');
                    INSERT INTO attendance VALUES (1,42,'No-show');
                    INSERT INTO raid_absences VALUES (3,42);
                """)
            actual = canonical.history_for_raid(str(path), 2)
            self.assertEqual(actual["42"],
                             "— · — · No-show | Absent · — · —")
            self.assertEqual(actual, legacy.history_for_raid(str(path), 2))
            self.assertEqual(canonical.history_for_raid(str(path), 100), {})
            with closing(sqlite3.connect(path)) as con, con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM attendance").fetchone()[0], 1)

    def test_previous_states_still_have_original_labels(self):
        self.assertEqual(canonical.STATES["No-show"], "No-show")
        self.assertEqual(canonical.STATES["Late"], "Late")
        self.assertNotIn("Pending", canonical.STATES)


if __name__ == "__main__":
    unittest.main()
