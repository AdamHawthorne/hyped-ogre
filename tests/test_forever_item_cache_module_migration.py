"""Regression tests for moving Forever item metadata without changing caches."""

import importlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cogs import forever_item_data as legacy
from hyped_ogre.features.characters import items as canonical


class ForeverItemCacheMigrationTests(unittest.TestCase):
    def test_legacy_and_new_imports_share_mutable_module_state(self):
        self.assertIs(legacy, canonical)
        self.assertIs(importlib.import_module("cogs.forever_item_data"), canonical)
        self.assertIs(legacy._MEMO, canonical._MEMO)
        self.assertIs(legacy._FETCH_ATTEMPTED, canonical._FETCH_ATTEMPTED)
        self.assertIs(legacy.catalog, canonical.catalog)
        self.assertIs(legacy.cached_catalog, canonical.cached_catalog)
        self.assertIs(legacy.lookup, canonical.lookup)

    def test_existing_cache_path_remains_next_to_raid_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "raid_signup.db")
            expected = Path(directory) / "raid_signup.forever_items.json"
            self.assertEqual(legacy._cache_path(db_path), expected)
            self.assertFalse(expected.exists())

    def test_legacy_patches_control_new_cache_and_offline_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "raid_signup.db")
            records = {"10638": {"name": "Helm", "level": 42}}
            with patch.dict(legacy._MEMO, {}, clear=True), \
                 patch.object(legacy, "_FETCH_ATTEMPTED", set()), \
                 patch.object(legacy, "_download", return_value=records) as fetch:
                self.assertEqual(canonical.cached_catalog(db_path), {})
                self.assertEqual(legacy.catalog(db_path), records)
                fetch.assert_called_once()
                path = canonical._cache_path(db_path)
                self.assertTrue(path.is_file())
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["version"], 1)
                self.assertEqual(saved["items"], records)
                self.assertEqual(canonical.catalog(db_path), records)
                fetch.assert_called_once()

                # A simulated restart re-reads the same on-disk cache
                # without downloading or touching the adjacent database.
                legacy._MEMO.clear()
                self.assertEqual(canonical.cached_catalog(db_path), records)
                self.assertEqual(legacy.lookup(records, 10638)["level"], 42)
                self.assertFalse(Path(db_path).exists())

    def test_failed_refresh_keeps_existing_catalog_without_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "raid_signup.db")
            path = canonical._cache_path(db_path)
            existing = {"123": {"name": "Existing item", "level": 55}}
            path.write_text(json.dumps({"version": 1, "items": existing}),
                            encoding="utf-8")
            old = time.time() - canonical.MAX_AGE_SECONDS - 100
            os.utime(path, (old, old))
            before = path.read_bytes()
            with patch.dict(legacy._MEMO, {}, clear=True), \
                 patch.object(legacy, "_FETCH_ATTEMPTED", set()), \
                 patch.object(legacy, "_download", side_effect=TimeoutError("offline")):
                self.assertEqual(legacy.catalog(db_path), existing)
                self.assertEqual(canonical.catalog(db_path), existing)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
