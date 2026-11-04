"""One-time Forever data reset must never access Ogre roll/coin storage."""
from contextlib import closing
from pathlib import Path
import hashlib
import sqlite3
import tempfile
import unittest

from cogs import raid_signup, raid_schedule
from hyped_ogre import launch_reset


class LaunchResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raid = self.root / "raid_signup.db"
        self.lfg = self.root / "lfg_board.db"
        self.roll = self.root / "bot.db"
        raid_signup.create_schema(str(self.raid))
        raid_schedule.ensure_raid_fields(str(self.raid))
        with closing(sqlite3.connect(self.raid)) as db:
            db.execute("INSERT INTO characters(discord_id,name,class_name,spec,role) "
                       "VALUES (42,'Test Mage','Mage','Frost','DPS')")
            db.execute("INSERT INTO raids(raid_id,title,raid_at,signup_open) "
                       "VALUES (1,'Test Raid','2026-12-09T21:00:00+00:00',1)")
            db.execute("INSERT INTO signups(raid_id,discord_id,character_id,submitted_at) "
                       "VALUES (1,42,1,'2026-11-04')")
            db.execute("INSERT INTO gear_exports(character_id,submitted_at,format,raw_text) "
                       "VALUES (1,'2026-11-04','wowsims_json','{}')")
            db.execute("INSERT INTO attendance(raid_id,discord_id,character_id,status) "
                       "VALUES (1,42,1,'Pending')")
            db.execute("INSERT INTO raid_ack_announcements(raid_id,channel_id,message_id,created_at) "
                       "VALUES (1,123,456,'2026-11-04')")
            db.execute("UPDATE raids SET panel_channel_id=123,panel_message_id=555 WHERE raid_id=1")
            db.execute("CREATE TABLE character_panels "
                       "(guild_id INTEGER,channel_id INTEGER,message_id INTEGER,page_index INTEGER)")
            db.execute("INSERT INTO character_panels VALUES(1,20,30,0)")
            db.commit()
        with closing(sqlite3.connect(self.lfg)) as db:
            db.execute("CREATE TABLE lfg_boards "
                       "(channel_id INTEGER PRIMARY KEY, guild_id INTEGER, message_id INTEGER)")
            db.execute("CREATE TABLE lfg_board_parts "
                       "(channel_id INTEGER,position INTEGER,message_id INTEGER)")
            db.execute("CREATE TABLE lfg_settings "
                       "(channel_id INTEGER PRIMARY KEY, mode TEXT)")
            db.execute("CREATE TABLE lfg_listings "
                       "(channel_id INTEGER, user_id INTEGER)")
            db.execute("CREATE TABLE lfg_dungeon_requests "
                       "(channel_id INTEGER, user_id INTEGER)")
            db.execute("CREATE TABLE lfg_live_plans "
                       "(channel_id INTEGER, user_id INTEGER)")
            db.execute("INSERT INTO lfg_boards VALUES (10,1,100)")
            db.execute("INSERT INTO lfg_board_parts VALUES (10,1,101)")
            db.execute("INSERT INTO lfg_settings VALUES (10,'launch')")
            db.execute("INSERT INTO lfg_listings VALUES (10,42)")
            db.execute("INSERT INTO lfg_dungeon_requests VALUES (10,42)")
            db.execute("INSERT INTO lfg_live_plans VALUES (10,42)")
            db.commit()
        # Real release code is never allowed to connect to the roll store,
        # regardless of its filename or content.
        with closing(sqlite3.connect(self.roll)) as db:
            db.execute("CREATE TABLE rolls (id INTEGER, score INTEGER)")
            db.execute("INSERT INTO rolls VALUES(5,100)")
            db.commit()

    def _roll_hash(self):
        return hashlib.sha256(self.roll.read_bytes()).hexdigest()

    def test_audit_is_read_only_and_reveals_message_ids(self):
        initial_roll = self._roll_hash()
        before = launch_reset.audit(self.root)
        self.assertEqual((before["characters"], before["raids"], before["lfg"]),
                         (1, 1, 3))
        self.assertEqual(before["raid_discord_posts_to_remove"], [(1, 123, 555)])
        self.assertEqual(before["raid_confirmations_to_remove"], [(1, 123, 456)])
        self.assertEqual(before["lfg_boards_to_keep"], [(10, 100)])
        self.assertEqual(self._roll_hash(), initial_roll)
        self.assertFalse((self.root / launch_reset.MARKER).exists())

    def test_execute_preserves_roll_and_persistent_discord_board_ids(self):
        initial_roll = self._roll_hash()
        result = launch_reset.execute(
            self.root, confirm=launch_reset.CONFIRMATION,
            expected_characters=1, expected_raids=1, expected_lfg=3,
        )
        self.assertEqual(result["after"]["characters"], 0)
        self.assertEqual(result["after"]["raids"], 0)
        self.assertEqual(result["after"]["lfg"], 0)
        with closing(sqlite3.connect(self.raid)) as db:
            self.assertEqual(db.execute("SELECT * FROM character_panels").fetchall(),
                             [(1, 20, 30, 0)])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with closing(sqlite3.connect(self.lfg)) as db:
            self.assertEqual(db.execute("SELECT * FROM lfg_boards").fetchall(),
                             [(10, 1, 100)])
            self.assertEqual(db.execute("SELECT * FROM lfg_board_parts").fetchall(),
                             [(10, 1, 101)])
            self.assertEqual(db.execute("SELECT mode FROM lfg_settings").fetchone(),
                             ("live",))
        self.assertEqual(self._roll_hash(), initial_roll)
        folder = Path(result["backup_directory"])
        self.assertTrue(folder.is_dir())
        with closing(sqlite3.connect(folder / "raid_signup.db")) as old:
            self.assertEqual(old.execute("SELECT COUNT(*) FROM characters").fetchone(),
                             (1,))
        with closing(sqlite3.connect(folder / "lfg_board.db")) as old:
            self.assertEqual(old.execute("SELECT COUNT(*) FROM lfg_listings").fetchone(),
                             (1,))
        self.assertTrue((self.root / launch_reset.MARKER).exists())
        with self.assertRaisesRegex(RuntimeError, "already been performed"):
            launch_reset.execute(
                self.root, confirm=launch_reset.CONFIRMATION,
                expected_characters=0, expected_raids=0, expected_lfg=0,
            )

    def test_wrong_count_or_confirmation_is_safe(self):
        initial_roll = self._roll_hash()
        for keyword in (
            {"confirm": "NO", "expected_characters": 1,
             "expected_raids": 1, "expected_lfg": 3},
            {"confirm": launch_reset.CONFIRMATION, "expected_characters": 1,
             "expected_raids": 1, "expected_lfg": 2},
        ):
            with self.assertRaises((ValueError, RuntimeError)):
                launch_reset.execute(self.root, **keyword)
        self.assertEqual(launch_reset.audit(self.root)["lfg"], 3)
        self.assertEqual(self._roll_hash(), initial_roll)
        self.assertFalse((self.root / launch_reset.MARKER).exists())

    def test_missing_lfg_database_never_creates_a_new_one(self):
        self.lfg.unlink()
        with self.assertRaisesRegex(RuntimeError, "Expected a real"):
            launch_reset.audit(self.root)
        self.assertFalse(self.lfg.exists())
        self.assertFalse((self.root / launch_reset.MARKER).exists())


if __name__ == "__main__":
    unittest.main()
