import unittest

from hyped_ogre.features.guild_control import (
    ControlPayloadError,
    valid_signature,
    validate_payload,
)
import hashlib
import hmac


class GuildControlTests(unittest.TestCase):
    def make_payload(self, **draft_changes):
        draft = {
            "day": "2026-11-08",
            "time": "20:00",
            "end_time": "23:00",
            "instance_1": "Hyjal (20m)",
            "instance_2": "Barrows (10m)",
            "instance_3": None,
            "announce": "1",
            "custom_title": "",
        }
        draft.update(draft_changes)
        return {
            "version": 1,
            "action": "raid_draft",
            "requestId": "12345678-1234-1234-1234-123456789abc",
            "draft": draft,
        }

    def test_accepts_supported_raid_draft_and_fixes_sensitive_options(self):
        payload = self.make_payload(ping_role_ids=[123], wowsims_policy="optional")
        result = validate_payload(payload)
        self.assertEqual(result["draft"]["day"], "2026-11-08")
        self.assertEqual(result["draft"]["ping_role_ids"], [])
        self.assertEqual(result["draft"]["wowsims_policy"], "missing")

    def test_rejects_unsupported_instance(self):
        with self.assertRaises(ControlPayloadError):
            validate_payload(self.make_payload(instance_1="Untrusted channel"))

    def test_rejects_invalid_date_and_unknown_fields(self):
        with self.assertRaises(ControlPayloadError):
            validate_payload(self.make_payload(day="tomorrow"))
        with self.assertRaises(ControlPayloadError):
            validate_payload(self.make_payload(channel_id=123))

    def test_title_is_trimmed_and_control_characters_are_rejected(self):
        result = validate_payload(self.make_payload(custom_title="  First clear  "))
        self.assertEqual(result["draft"]["custom_title"], "First clear")
        with self.assertRaises(ControlPayloadError):
            validate_payload(self.make_payload(custom_title="Clear\n@everyone"))

    def test_signed_requests_reject_stale_timestamps(self):
        secret = "test-secret"
        timestamp = "12345"
        body = b'{"version":1}'
        signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body,
                             hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(secret, timestamp, signature, body, 12345))
        self.assertFalse(valid_signature(secret, timestamp, signature, body, 12345 + 301))


if __name__ == "__main__":
    unittest.main()
