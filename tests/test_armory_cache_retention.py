import json
import unittest

from hyped_ogre.features.characters.armory import merge_professions


class ArmoryCacheRetentionTests(unittest.TestCase):
    def setUp(self):
        self.previous = ("Big Snore", json.dumps([
            {"name": "Mining", "level": 300},
            {"name": "Blacksmithing", "level": 275},
        ]))

    def test_missing_response_preserves_professions(self):
        self.assertEqual(merge_professions(self.previous, "Big Snore", None),
                         json.loads(self.previous[1]))

    def test_unverified_response_preserves_professions(self):
        self.assertEqual(merge_professions(self.previous, "Big Snore", {
            "profession_details": [],
        }), json.loads(self.previous[1]))

    def test_missing_skill_preserves_skill_but_new_value_wins(self):
        result = merge_professions(self.previous, "Big Snore", {
            "professions_verified": True,
            "profession_details": [{"name": "Mining", "level": None},
                                   {"name": "Blacksmithing", "level": 280}],
        })
        self.assertEqual(result, [{"name": "Mining", "level": 300},
                                  {"name": "Blacksmithing", "level": 280}])

    def test_verified_empty_list_clears_professions(self):
        self.assertEqual(merge_professions(self.previous, "Big Snore", {
            "professions_verified": True, "profession_details": [],
        }), [])

    def test_renamed_character_does_not_inherit_old_professions(self):
        self.assertEqual(merge_professions(self.previous, "Other Name", None), [])
