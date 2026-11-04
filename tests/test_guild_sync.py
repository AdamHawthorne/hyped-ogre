import sqlite3
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from hyped_ogre.features.guild_sync import (
    SyncPayloadError,
    latest_levels_by_name,
    persist_payload,
    validate_payload,
    _valid_signature,
)


NOW = 1_800_000_000


def payload():
    return {
        "version": 1,
        "source": "HypedGuildTools",
        "guild": "Hyped",
        "rosterCapturedAt": NOW,
        "roster": [
            {"name": "Mainone", "level": 60, "class": "WARRIOR", "capturedAt": NOW},
            {"name": "NotRegistered", "level": 45, "capturedAt": NOW},
        ],
        "bankEvents": [
            {"eventId": "evt-a1", "kind": "deposit", "character": "Bankalt",
             "item": "Dreamfoil", "count": 20, "tab": "2", "ageSeconds": 600,
             "capturedAt": NOW},
        ],
    }


class GuildSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.raid_db = str(Path(self.temp.name) / "raid.db")
        self.sync_db = str(Path(self.temp.name) / "sync.db")
        with sqlite3.connect(self.raid_db) as con:
            con.execute("CREATE TABLE characters (name TEXT, is_main INTEGER)")
            con.executemany("INSERT INTO characters VALUES (?,?)", [("Mainone", 1), ("Altone", 0)])

    def tearDown(self):
        self.temp.cleanup()

    def test_only_registered_mains_get_level_rows_and_bank_events_are_idempotent(self):
        first = persist_payload(payload(), self.raid_db, self.sync_db, now=NOW)
        second = persist_payload(payload(), self.raid_db, self.sync_db, now=NOW)
        self.assertEqual(first, {"levelsUpdated": 1, "levelsIgnored": 1, "bankEventsReceived": 1})
        self.assertEqual(second["levelsUpdated"], 0)
        self.assertEqual(second["bankEventsReceived"], 0)
        self.assertEqual(latest_levels_by_name(self.sync_db, now=NOW)["mainone"]["level"], 60)

    def test_rejects_replay_shaped_or_ambiguous_payload(self):
        invalid = payload()
        invalid["roster"].append({"name": "MAINONE-Realm", "level": 59, "capturedAt": NOW})
        with self.assertRaises(SyncPayloadError):
            validate_payload(invalid, now=NOW)

    def test_rejects_stale_capture_time(self):
        invalid = payload()
        invalid["roster"][0]["capturedAt"] = NOW - 31 * 24 * 60 * 60
        with self.assertRaises(SyncPayloadError):
            validate_payload(invalid, now=NOW)

    def test_hmac_signature_expires(self):
        secret = "x" * 40
        timestamp = str(NOW)
        body = b'{"version":1}'
        digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        self.assertTrue(_valid_signature(secret, timestamp, "sha256=" + digest, body, NOW))
        self.assertFalse(_valid_signature(secret, str(NOW - 301), "sha256=" + digest, body, NOW))


if __name__ == "__main__":
    unittest.main()

