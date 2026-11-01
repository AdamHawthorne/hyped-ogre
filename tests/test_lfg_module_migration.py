"""Compatibility guards for the Hyped Ogre LFG module migration.

The legacy import paths are needed by existing production cogs and any
previously written tests. Keeping object identity prevents subtle drift.
"""
import unittest

from cogs import lfg_sort as old_sort
from cogs import lfg_pagination as old_page
from hyped_ogre.features.lfg import sorting as new_sort
from hyped_ogre.features.lfg import pagination as new_page


class LFGModuleMigrationTests(unittest.TestCase):
    def test_sorting_imports_use_identical_implementation(self):
        self.assertIs(old_sort.PLAYING_DAYS, new_sort.PLAYING_DAYS)
        for name in ("is_solo_or_sorted", "is_open_lfm",
                     "listing_start_key", "listing_board_key"):
            with self.subTest(name=name):
                self.assertIs(getattr(old_sort, name), getattr(new_sort, name))

    def test_pagination_imports_use_identical_implementation(self):
        for name in ("page_count", "clamp_page", "page_rows", "board_chunks"):
            with self.subTest(name=name):
                self.assertIs(getattr(old_page, name), getattr(new_page, name))
        self.assertEqual(old_page.PAGE_SIZE, new_page.PAGE_SIZE)
        self.assertEqual(old_page.BOARD_CHUNK_SIZE, new_page.BOARD_CHUNK_SIZE)
        self.assertEqual(old_page.BOARD_TEXT_BUDGET, new_page.BOARD_TEXT_BUDGET)

    def test_saved_group_plans_keep_previous_sort_order(self):
        records = [
            dict(user_id=1, character="Sorted", activity="Full Group / Sorted",
                 availability="Launch 23:00 UK - 03:00 UK",
                 days_off="Launch Night · Wed 4 Nov"),
            dict(user_id=2, character="Looking", activity="Open / Flexible",
                 availability="Later", days_off="Thu 5 Nov"),
            dict(user_id=3, character="LFM", activity="Group Questing (LFM)",
                 availability="01:00 UK", days_off="Thu 5 Nov"),
        ]
        original = sorted(records, key=old_sort.listing_board_key)
        migrated = sorted(records, key=new_sort.listing_board_key)
        self.assertEqual(original, migrated)
        self.assertEqual([r["user_id"] for r in original], [3, 2, 1])

    def test_existing_board_chunks_are_unchanged(self):
        rows = [dict(user_id=i, character=f"Player{i}") for i in range(23)]
        self.assertEqual(old_page.board_chunks(rows), new_page.board_chunks(rows))
        self.assertEqual(new_page.board_chunks([]), [[]])
        self.assertEqual(
            [row["user_id"] for chunk in new_page.board_chunks(rows)
             for row in chunk],
            list(range(23)),
        )


if __name__ == "__main__":
    unittest.main()
