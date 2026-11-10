"""Forever WoWSims exports first names, while Discord stores full names."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import raid_signup as raid
from hyped_ogre.features.characters.equipment import parse_export


class WSEFirstNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "raid.db")
        raid.create_schema(self.path)
        raid.save_character(42, "Big Snore", "Warrior", "Protection",
                            "Tank", self.path)
        self.character_id = raid.member_characters(42, self.path)[0][0]
        self.export = {
            "talents": "-0513-250033120031021251",
            "id": "Player-6940-005F73AB",
            "class": "warrior",
            "professions": [
                {"name": "Blacksmithing", "level": 208},
                {"name": "Mining", "level": 228},
            ],
            "flavor": "forever",
            "name": "Big",
            "race": "Tauren",
            "version": "v3.2.7",
            "spec": "protection",
            "gear": {"items": [{"enchant": 20640}, {"enchant": 9641}]},
            "level": 49,
            "unit": "player",
            "realm": "Snore",
        }

    def submit(self, *, entered_name="Big Snore"):
        raw = json.dumps(self.export)
        raid.save_wowsims_export(
            42, self.character_id, raw, self.path, entered_name=entered_name
        )
        return raw

    def test_accepts_first_name_and_preserves_full_name_and_raw_export(self):
        raw = self.submit()
        with raid.database(self.path) as con:
            self.assertEqual(con.execute(
                "SELECT name FROM characters WHERE character_id=?",
                (self.character_id,)).fetchone(), ("Big Snore",))
            self.assertEqual(con.execute(
                "SELECT raw_text FROM gear_exports WHERE character_id=?",
                (self.character_id,)).fetchone(), (raw,))
        parsed = parse_export(raw)
        self.assertEqual(parsed["level"], 49)
        self.assertEqual(parsed["profession_details"], [
            {"name": "Blacksmithing", "level": 208},
            {"name": "Mining", "level": 228},
        ])

    def test_comparison_ignores_case_and_whitespace(self):
        self.export["name"] = "  bIg  "
        self.submit(entered_name="  BIG    SNORE ")
        with raid.database(self.path) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM gear_exports").fetchone()[0], 1)

    def test_mismatch_in_exported_first_name_rejects_without_saving(self):
        self.export["name"] = "Wrong"
        with self.assertRaisesRegex(ValueError, "first name must match"):
            self.submit()
        with raid.database(self.path) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM gear_exports").fetchone()[0], 0)

    def test_confirmed_full_name_must_match_selected_character(self):
        with self.assertRaisesRegex(ValueError, "Confirmed name does not match"):
            self.submit(entered_name="Big Other")
        with self.assertRaisesRegex(ValueError, "Confirmed name does not match"):
            self.submit(entered_name="  ")
        with raid.database(self.path) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM gear_exports").fetchone()[0], 0)

    def test_existing_callers_without_explicit_name_still_work(self):
        raid.save_wowsims_export(
            42, self.character_id, json.dumps(self.export), self.path)
        with raid.database(self.path) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM gear_exports").fetchone()[0], 1)

    def test_cannot_use_other_members_character(self):
        with self.assertRaisesRegex(ValueError, "belongs to you"):
            raid.save_wowsims_export(
                99, self.character_id, json.dumps(self.export), self.path,
                entered_name="Big Snore")


class WSENameModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_modal_asks_for_full_name_and_passes_it_to_verification(self):
        modal = raid.WoWSimsExportModal(42, 123, "Big Snore")
        self.assertEqual(modal.confirm_name.label, "Confirm full character name")
        self.assertEqual(modal.confirm_name.default, "Big Snore")
        modal.confirm_name._value = "Big Snore"
        modal.export_text._value = '{"name":"Big","class":"warrior"}'
        interaction = SimpleNamespace(
            guild_id=77, user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "save_wowsims_export") as save:
            await modal.on_submit(interaction)
            save.assert_called_once_with(
                42, 123, modal.export_text.value, entered_name="Big Snore")
        self.assertIn("Big Snore", interaction.response.send_message.await_args.args[0])

    async def test_registration_asks_for_full_character_name(self):
        modal = raid.CharacterNameModal(42, "Warrior", "Protection", "Tank")
        self.assertEqual(modal.name.label, "Full character name")


if __name__ == "__main__":
    unittest.main()
