"""Bounded HTTP 429 retries for Google Sheets quota responses."""
import unittest
from unittest.mock import MagicMock, patch

from hyped_ogre.integrations import sheets


class SheetsRateLimitTests(unittest.TestCase):
    def test_retry_after_respected_and_original_request_reused(self):
        limited = MagicMock(status_code=429, headers={"Retry-After": "3"})
        success = MagicMock(status_code=200, headers={})
        success.json.return_value = {"ok": True}
        session = MagicMock()
        session.request.side_effect = [limited, success]
        with patch.object(sheets.time, "sleep") as sleep:
            result = sheets._json(
                session, "POST", "https://example.invalid/test",
                json={"test": "kept"},
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.request.call_args_list[0].kwargs,
                         session.request.call_args_list[1].kwargs)
        sleep.assert_called_once_with(3)

    def test_persistent_quota_error_is_bounded_and_sanitised(self):
        from requests import HTTPError
        limited = MagicMock(status_code=429, headers={"Retry-After": "0"})
        limited.raise_for_status.side_effect = HTTPError("private response content")
        session = MagicMock()
        session.request.return_value = limited
        with patch.object(sheets.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "Google Sheets returned HTTP 429") as error:
                sheets._json(session, "GET", "https://example.invalid/test")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(session.request.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_non_quota_errors_are_not_retried(self):
        from requests import HTTPError
        denied = MagicMock(status_code=403, headers={})
        denied.raise_for_status.side_effect = HTTPError("sensitive")
        session = MagicMock()
        session.request.return_value = denied
        with patch.object(sheets.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                sheets._json(session, "GET", "https://example.invalid/test")
        session.request.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
