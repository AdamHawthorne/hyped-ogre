"""LFG-only characters share one identity and opt into raids/roster deliberately."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from cogs import lfg_preview as lfg, raid_signup as raid, raid_schedule as schedule
from hyped_ogre.features.characters import hub, roster
from hyped_ogre.features.classrooms.config import GUILD_ID
from hyped_ogre.integrations import sheets


class SharedVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raid_path = str(Path(self.tmp.name) / "raid_signup.db")
        self.lfg_path = str(Path(self.tmp.name) / "lfg_board.db")
        raid.create_schema(self.raid_path)
        schedule.ensure_raid_fields(self.raid_path)
        raid.save_character(
            42, "Casual", "Warrior", "Protection", "Tank", self.raid_path,
            roster_visible=False,
        )
        self.cid = raid.member_characters(42, self.raid_path)[0][0]

    def test_casual_is_selectable_for_lfg_but_absent_from_raid_roster_and_sheets(self):
        self.assertEqual(len(raid.member_characters(42, self.raid_path)), 1)
        self.assertEqual(roster.rows(self.raid_path), [])
        self.assertEqual(sheets._raid_snapshot(self.raid_path)[0], [])
        self.assertEqual(sheets.character_discord_ids(self.raid_path), [])
        raid.set_main_character(42, self.cid, self.raid_path)
        self.assertEqual(roster.rows(self.raid_path), [])
        self.assertEqual(raid.member_characters(42, self.raid_path)[0][0], self.cid)

    def test_roster_add_reuses_case_insensitive_identity_without_demoting(self):
        with raid.database(self.raid_path) as db:
            db.execute("INSERT INTO gear_exports(character_id,submitted_at,format,raw_text) "
                       "VALUES (?,'2026-11-04','wowsims_json',?)",
                       (self.cid, json.dumps({"name":"Casual", "version":"v3.2.6",
                          "gear":{"items":[]}, "class":"warrior"})))
        self.assertEqual(raid.save_character(
            42, "  CASUAL  ", "Warrior", "Fury", "DPS", self.raid_path,
            roster_visible=True,
        ), "updated")
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT character_id,roster_visible,is_main FROM characters "
                "WHERE discord_id=42").fetchall(), [(self.cid, 1, 1)])
            self.assertEqual(db.execute(
                "SELECT character_id FROM gear_exports").fetchone()[0], self.cid)
        self.assertEqual(len(roster.rows(self.raid_path)), 1)
        raid.save_character(
            42, "casual", "Warrior", "Fury", "DPS", self.raid_path,
            roster_visible=False)
        self.assertEqual(len(roster.rows(self.raid_path)), 1)

    def test_first_visible_main_is_not_blocked_by_hidden_lfg_main(self):
        raid.save_character(
            42, "Raider", "Mage", "Frost", "DPS", self.raid_path)
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT name,is_main,roster_visible FROM characters "
                "WHERE discord_id=42 ORDER BY character_id").fetchall(),
                [("Casual", 0, 0), ("Raider", 1, 1)])
        self.assertEqual([r["name"] for r in roster.rows(self.raid_path)], ["Raider"])
        self.assertTrue(raid.promote_character_to_roster(42, self.cid, self.raid_path))
        self.assertFalse(raid.promote_character_to_roster(42, self.cid, self.raid_path))
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT name,is_main FROM characters "
                "WHERE discord_id=42 ORDER BY character_id").fetchall(),
                [("Casual", 0), ("Raider", 1)])
        self.assertEqual(len(raid.member_characters(42, self.raid_path)), 2)

    def test_hidden_lfg_main_cannot_displace_a_visible_roster_main(self):
        raid.save_character(42, "Raider", "Mage", "Frost", "DPS", self.raid_path)
        with self.assertRaisesRegex(ValueError, "Add this LFG character"):
            raid.set_main_character(42, self.cid, self.raid_path)
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT name FROM characters WHERE discord_id=42 AND "
                "roster_visible=1 AND is_main=1").fetchall(), [("Raider",)])
        self.assertEqual(len(roster.render(roster.rows(self.raid_path))[0].fields), 12)

    def test_deleting_visible_main_prefers_remaining_visible_character(self):
        raid.save_character(42, "Raider", "Mage", "Frost", "DPS", self.raid_path)
        raid.save_character(42, "Alt", "Druid", "Balance", "DPS", self.raid_path)
        raider = next(r[0] for r in raid.member_characters(42, self.raid_path)
                      if r[1] == "Raider")
        raid.delete_character(42, raider, self.raid_path)
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT name FROM characters WHERE discord_id=42 "
                "AND roster_visible=1 AND is_main=1").fetchall(), [("Alt",)])
        self.assertEqual(len(roster.rows(self.raid_path)), 1)

    def test_raid_signup_auto_promotes_exact_existing_lfg_character(self):
        event = schedule.save_raid(self.raid_path, 1, "2026-12-09 21:00",
                                   "Hyjal (20m)", wowsims_policy="optional")
        raid.save_signup(1, 42, self.cid, self.raid_path)
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute(
                "SELECT character_id,roster_visible FROM characters").fetchone(),
                (self.cid, 1))
            self.assertEqual(db.execute(
                "SELECT character_id FROM signups WHERE discord_id=42").fetchone(),
                (self.cid,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1)
        self.assertEqual(sheets._raid_snapshot(self.raid_path)[0][0]["name"], "Casual")

    def test_failed_signup_must_not_promote_an_lfg_character(self):
        with self.assertRaises(ValueError):
            raid.save_signup(999, 42, self.cid, self.raid_path)
        self.assertEqual(roster.rows(self.raid_path), [])

    def test_hidden_lfg_registration_does_not_duplicate_other_members(self):
        raid.save_character(99, "Casual", "Warrior", "Protection", "Tank",
                            self.raid_path, roster_visible=False)
        self.assertEqual(len(raid.member_characters(42, self.raid_path)), 1)
        self.assertEqual(len(raid.member_characters(99, self.raid_path)), 1)
        with self.assertRaises(ValueError):
            raid.promote_character_to_roster(99, self.cid, self.raid_path)


class LFGInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raid_path = str(Path(self.temp.name) / "raid_signup.db")
        self.lfg_path = str(Path(self.temp.name) / "lfg_board.db")
        self.patchers = [
            patch.object(raid, "RAID_DB", self.raid_path),
            patch.object(raid, "GUILD_ID", GUILD_ID),
            patch.object(lfg, "PATH", self.lfg_path),
            patch.dict(os.environ, {"HYPED_GUILD_ID": str(GUILD_ID)}),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        raid.create_schema(self.raid_path)
        schedule.ensure_raid_fields(self.raid_path)
        lfg.init_db()
        raid.save_character(42, "Casual", "Warrior", "Protection", "Tank",
                            self.raid_path, roster_visible=False)
        self.cid = raid.member_characters(42, self.raid_path)[0][0]
        # Default path arguments were bound when raid_signup loaded, so
        # patch the public wrappers rather than only its RAID_DB variable.
        original_members = raid.member_characters
        original_promote = raid.promote_character_to_roster
        member_patch = patch.object(
            raid, "member_characters",
            side_effect=lambda member_id, path=None:
                original_members(member_id, path or self.raid_path),
        )
        promotion_patch = patch.object(
            raid, "promote_character_to_roster",
            side_effect=lambda member_id, character_id, path=None:
                original_promote(member_id, character_id, path or self.raid_path),
        )
        for wrapper_patch in (member_patch, promotion_patch):
            wrapper_patch.start()
            self.addCleanup(wrapper_patch.stop)
        self.cog = SimpleNamespace(
            panels=lambda: [(123, 456, 0)], refresh=AsyncMock(),
        )

    def interaction(self):
        return SimpleNamespace(
            guild_id=GUILD_ID, user=SimpleNamespace(id=42),
            response=SimpleNamespace(
                send_message=AsyncMock(), edit_message=AsyncMock(),
                send_modal=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_character_board_add_offers_saved_selection_or_new_and_reuses_id(self):
        view = hub.RosterAddView(self.cog, 123, 42)
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[1].label, "New character")
        select = next(x for x in view.children if isinstance(x, discord.ui.Select))
        select._values = [str(self.cid)]
        interaction = self.interaction()
        await select.callback(interaction)
        self.assertEqual(len(roster.rows(self.raid_path)), 1)
        self.cog.refresh.assert_awaited_once_with(123, 456)
        with raid.database(self.raid_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM characters").fetchone()[0], 1)
        await view.children[1].callback(interaction)
        self.assertTrue(interaction.response.edit_message.call_args.kwargs["view"].roster_visible)

    async def test_lfg_character_creation_does_not_opt_in_to_roster(self):
        view = raid.CharacterActionView(42, purpose="lfg")
        select = next(x for row in view.children if isinstance(row, discord.ui.ActionRow)
                      for x in row.children if isinstance(x, discord.ui.Select))
        self.assertIn("Add character", [opt.label for opt in select.options])
        select._values = ["add"]
        interaction = self.interaction()
        await select.callback(interaction)
        opened = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(opened, raid.ClassPickerView)
        self.assertFalse(opened.roster_visible)

    async def test_dungeon_first_button_and_other_card_avatar(self):
        with lfg.database() as db:
            lfg.live_plans.set_mode(db, 111, "live")
            lfg.live_plans.save(db, 111, 42, self.cid,
                                "Levelling up", "This evening", "Anyone", "Notes",
                                "https://example.com/avatar.png")
        payload = json.dumps(lfg.BoardLayout(self.cog, 111, guild=None).to_components())
        self.assertIn("https://example.com/avatar.png", payload)
        self.assertIn("Other", payload)
        self.assertIn("LF Dungeons", payload)
        self.assertIn("Edit", payload)
        self.assertNotIn("Remove Plan", payload)
        self.assertNotIn("Remove Dungeon", payload)

    async def test_edit_can_remove_only_dungeon_with_confirmation(self):
        with lfg.database() as db:
            lfg.live_plans.set_mode(db, 111, "live")
            lfg.live_plans.save(db, 111, 42, self.cid, "PvP", "Tonight",
                                "Team", "", "https://example.com/avatar.png")
            lfg.lfg_dungeons.save_request(
                db, 111, 42, self.cid, ("Ragefire Chasm",), "",
                "Tonight", "", "https://example.com/avatar.png")
        interaction = self.interaction()
        await lfg.EditListingButton(self.cog, 111).callback(interaction)
        menu = interaction.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(menu, lfg.ListingEditView)
        remove = next(button for button in menu.children
                      if isinstance(button, discord.ui.Button)
                      and button.label == "Remove listing")
        await remove.callback(interaction)
        confirmation = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(confirmation, lfg.ListingRemoveConfirmation)
        await confirmation.children[0].callback(interaction)
        with lfg.database() as db:
            self.assertIsNone(lfg.lfg_dungeons.get_request(db, 111, 42))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM lfg_live_plans").fetchone()[0], 1)
        self.cog.refresh.assert_awaited_once_with(111)


if __name__ == "__main__":
    unittest.main()
