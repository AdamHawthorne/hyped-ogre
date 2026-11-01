from wowsims_helper import signup_with_export, seed_export, example_export
"""Regression tests for the four-button raid member flow and private gear uploads."""
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from PIL import Image

from cogs import raid_schedule, raid_signup as raid, raid_sheets, raid_sheets_v3


class RaidMemberFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_raid_sheet_names_resolve_from_discord_without_changing_rolls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid.save_character(42, "Character One", "Mage", "Frost", "DPS", path)
            raid.save_character(42, "Character Two", "Mage", "Frost", "DPS", path)
            member = SimpleNamespace(display_name="Ogre Leader")
            guild = SimpleNamespace(
                get_member=Mock(return_value=None),
                fetch_member=AsyncMock(return_value=member),
            )
            bot = SimpleNamespace(
                get_guild=Mock(return_value=guild),
                get_user=Mock(return_value=None),
            )
            cog = raid.RaidPrototype(bot)
            with patch.object(raid, "RAID_DB", path):
                names = await cog.raid_discord_names()
                self.assertEqual(names, {42: "Ogre Leader"})
                guild.fetch_member.assert_awaited_once_with(42)
                # A cached name avoids additional requests on the next sync.
                self.assertEqual(await cog.raid_discord_names(), names)
                guild.fetch_member.assert_awaited_once_with(42)
                with patch.object(raid_sheets_v3, "sync_once", return_value=(
                    {"characters": 2}, set()
                )) as sync:
                    self.assertEqual(
                        await cog.sync_raid_sheets(),
                        ({"characters": 2}, set()),
                    )
                    sync.assert_called_once_with(path, {42: "Ogre Leader"})

    async def test_main_panel_has_exactly_four_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)",
            )
            panel = raid_schedule.RaidEventPanel(
                event, on_signup=AsyncMock(), on_update=AsyncMock(),
                on_gear=AsyncMock(), on_absent=AsyncMock()
            )
            buttons = [
                item for item in panel.walk_children()
                if isinstance(item, discord.ui.Button)
            ]
            self.assertEqual([b.label for b in buttons],
                             ["Attend", None, "Characters", "WoWSims", "Absent"])
            self.assertTrue(all(not b.disabled for b in buttons if b.label is not None))
            self.assertTrue(next(b for b in buttons if b.label is None).disabled)
            self.assertEqual(buttons[0].style, discord.ButtonStyle.success)
            self.assertEqual(buttons[-1].style, discord.ButtonStyle.danger)
            self.assertTrue(panel.is_persistent())

    async def test_player_buttons_remain_available_after_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            event = raid_schedule.save_raid(
                path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            event["signup_open"] = 0
            event["roster_published"] = 1
            callbacks = dict(on_signup=AsyncMock(), on_update=AsyncMock(),
                             on_gear=AsyncMock(), on_absent=AsyncMock(),
                             on_remind=AsyncMock())
            def status(e):
                panel = raid_schedule.RaidEventPanel(e, **callbacks)
                return {b.label or "Remind": b.disabled for b in panel.walk_children()
                        if isinstance(b, discord.ui.Button)}
            locked = status(event)
            self.assertTrue(locked["Attend"])
            self.assertFalse(locked["Characters"])
            self.assertFalse(locked["WoWSims"])
            self.assertFalse(locked["Absent"])
            self.assertFalse(locked["Remind"])
            event["archived_at"] = "2026-12-10T01:00:00+00:00"
            archived = status(event)
            self.assertFalse(archived["Characters"])
            self.assertTrue(archived["WoWSims"])
            self.assertTrue(archived["Absent"])
            self.assertTrue(archived["Remind"])

    async def test_public_raid_panel_controls_rebind_after_restart(self):
        with patch.object(raid, "active_panels", return_value=[
                {"raid_id": 1, "panel_message_id": 12345,
                 "panel_channel_id": raid.RAID_CHANNEL_ID}
             ]), patch.object(raid_sheets, "configured", return_value=False):
            bot = SimpleNamespace(add_view=Mock())
            cog = raid.RaidPrototype(bot)
            view = object()
            with patch.object(cog, "make_panel", return_value=view):
                await cog.cog_load()
            bot.add_view.assert_called_once_with(view, message_id=12345)

    async def test_sheet_sync_refreshes_public_destination(self):
        bot_message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=bot_message))
        bot = SimpleNamespace(get_channel=Mock(return_value=channel))
        cog = raid.RaidPrototype(bot)
        event = {"raid_id": 1, "panel_message_id": 12345,
                 "panel_channel_id": raid.RAID_CHANNEL_ID}
        with patch.object(cog, "raid_discord_names", new_callable=AsyncMock,
                          return_value={}), \
             patch.object(raid_sheets_v3, "sync_once", return_value=({}, {1})), \
             patch.object(raid, "read_raid", return_value=event), \
             patch.object(cog, "make_panel", return_value="updated"):
            await cog.sync_raid_sheets()
        bot.get_channel.assert_called_once_with(raid.RAID_CHANNEL_ID)
        channel.fetch_message.assert_awaited_once_with(12345)
        bot_message.edit.assert_awaited_once_with(view="updated")

    async def test_signup_notes_optional_and_one_signup_per_raid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 2, "2026-12-09 21:00", "Ony (40m)")
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            signup_with_export(2, 42, char_id, path, note="Need to leave at 22:30")
            with raid.database(path) as con:
                first = con.execute(
                    "SELECT note, submitted_at FROM signups WHERE raid_id=2"
                ).fetchone()
            self.assertEqual(first[0], "Need to leave at 22:30")
            signup_with_export(2, 42, char_id, path)
            with raid.database(path) as con:
                rows = con.execute("SELECT note, submitted_at FROM signups").fetchall()
            self.assertEqual(rows, [first])
            signup_with_export(2, 42, char_id, path, note="")
            with raid.database(path) as con:
                self.assertEqual(con.execute(
                    "SELECT note FROM signups"
                ).fetchone()[0], "")
            with self.assertRaises(ValueError):
                signup_with_export(2, 42, char_id, path, note="X" * 501)
            signup_with_export(2, 42, char_id, path, note="Leaving early")
            raid.save_absence(2, 42, "Not coming", path)
            with raid.database(path) as con:
                self.assertEqual(
                    con.execute("SELECT note FROM signups WHERE raid_id=2"
                    ).fetchone()[0], "Leaving early"
                )
            self.assertEqual(raid.my_absence(2, 42, path)[0], "Not coming")

    async def test_profile_edit_keeps_character_and_raid_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            signup_with_export(1, 42, char_id, path)
            self.assertEqual(raid.update_character_profile(
                42, char_id, "Warrior", "Protection", "Tank", path
            ), "First Last")
            self.assertEqual(raid.member_characters(42, path)[0][0], char_id)
            self.assertEqual(raid.member_characters(42, path)[0][2:5],
                             ("Warrior", "Protection", "Tank"))
            self.assertEqual(raid.my_signup(1, 42, path), ("First Last", "Available"))
            with self.assertRaises(ValueError):
                raid.update_character_profile(
                    99, char_id, "Mage", "Frost", "DPS", path
                )

    async def test_screenshot_private_jpeg_and_sheets_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(42, "First Last", "Mage", "Frost", "DPS", path)
            char_id = raid.member_characters(42, path)[0][0]
            image = io.BytesIO()
            Image.new("RGB", (400, 250), (110, 120, 130)).save(
                image, format="PNG"
            )
            raid.save_gear_image(
                42, char_id, "character.png", image.getvalue(), path
            )
            filename, payload = raid.last_gear_image(char_id, path)
            self.assertEqual(filename, "character-panel.jpg")
            self.assertTrue(payload.startswith(b"\xff\xd8"))
            row = raid_sheets._desired_rows(path)["gear"][str(char_id)]
            self.assertEqual(row[4], "Screenshot")
            self.assertEqual(len(row), 5)
            self.assertNotIn(payload[:20].hex(), json.dumps(row))
            with self.assertRaises(ValueError):
                raid.save_gear_image(99, char_id, "character.png",
                                     image.getvalue(), path)
            with self.assertRaises(ValueError):
                raid.save_gear_image(42, char_id, "not-image.png", b"x" * 150, path)

    async def test_character_delete_requires_no_raid_history_and_promotes_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "raid.db")
            raid.create_schema(path)
            raid_schedule.ensure_raid_fields(path)
            raid.save_character(42, "Main One", "Warrior", "Protection", "Tank", path)
            raid.save_character(42, "Alt Two", "Mage", "Frost", "DPS", path)
            rows = raid.member_characters(42, path)
            main = next(row[0] for row in rows if row[1] == "Main One")
            alt = next(row[0] for row in rows if row[1] == "Alt Two")
            with self.assertRaises(ValueError):
                raid.delete_character(99, alt, path)
            raid.delete_character(42, main, path)
            self.assertEqual(raid.main_character_ids(42, path), {alt})
            raid_schedule.save_raid(path, 1, "2026-12-09 21:00", "Hyjal (20m)")
            with raid.database(path) as con:
                con.execute(
                    "INSERT INTO attendance (raid_id,discord_id,character_id) VALUES (?,?,?)",
                    (1, 42, alt),
                )
            with self.assertRaisesRegex(ValueError, "history"):
                raid.delete_character(42, alt, path)
            self.assertEqual(len(raid.member_characters(42, path)), 1)

    async def test_wowsims_modal_requires_paste_and_no_upload(self):
        modal = raid.WoWSimsExportModal(42, 15, "First Last")
        self.assertTrue(modal.export_text.required)
        self.assertEqual(modal.export_text.max_length, 4000)
        self.assertFalse(any(isinstance(c, discord.ui.FileUpload)
                             for c in modal.children))
        view = raid.WoWSimsInstructionsView(42, 15, "First Last")
        data = json.dumps(view.to_components())
        self.assertIn("/wse export", data)
        self.assertIn("curseforge.com/wow/addons/wowsims-exporter", data)


    async def test_wowsims_button_skips_instructions_after_first_export(self):
        character = (7, "Snore Mon", "Warrior", "Protection", "Tank", None)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), guild_id=77,
            response=SimpleNamespace(
                send_message=AsyncMock(), send_modal=AsyncMock(),
            ),
        )
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "member_characters", return_value=[character]), \
             patch.object(raid, "has_wowsims_export", return_value=False) as has_export:
            cog = raid.RaidPrototype(SimpleNamespace())
            await cog.on_gear(interaction)
            self.assertIsInstance(
                interaction.response.send_message.await_args.kwargs["view"],
                raid.WoWSimsInstructionsView,
            )
            interaction.response.send_message.reset_mock()
            has_export.return_value = True
            await cog.on_gear(interaction)
            modal = interaction.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, raid.WoWSimsExportModal)
            self.assertEqual(modal.char_id, 7)
            interaction.response.send_message.assert_not_awaited()
            self.assertEqual(has_export.call_count, 2)

    async def test_wowsims_multi_character_selection_checks_each_char(self):
        characters = [
            (7, "Snore Mon", "Warrior", "Protection", "Tank", None),
            (8, "Other Alt", "Mage", "Frost", "DPS", None),
        ]
        response = SimpleNamespace(
            edit_message=AsyncMock(), send_modal=AsyncMock(),
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), guild_id=77, response=response,
        )
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "member_characters", return_value=characters), \
             patch.object(raid, "main_character_ids", return_value={7}), \
             patch.object(raid, "has_wowsims_export",
                          side_effect=lambda user, character: character == 8):
            view = raid.CharacterActionView(42, purpose="gear")
            selection = next(item for item in view.walk_children()
                             if isinstance(item, discord.ui.Select))
            selection._values = ["7"]
            await selection.callback(interaction)
            self.assertIsInstance(
                response.edit_message.await_args.kwargs["view"],
                raid.WoWSimsInstructionsView,
            )
            response.edit_message.reset_mock()
            selection._values = ["8"]
            await selection.callback(interaction)
            modal = response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, raid.WoWSimsExportModal)
            self.assertEqual(modal.char_id, 8)
            response.edit_message.assert_not_awaited()

    async def test_signup_and_update_are_private_discord_views(self):
        with patch.object(raid, "member_characters", return_value=[
            (7, "First Last", "Mage", "Frost", "DPS", None)
        ]):
            for purpose in ("signup", "gear", "update"):
                view = raid.CharacterActionView(42, raid_id=1, purpose=purpose)
                labels = json.dumps(view.to_components())
                self.assertIn("First Last", labels)
                self.assertNotIn('"label": "Absent"', labels)
        first_time = raid.CharacterNameModal(
            42, "Mage", "Frost", "DPS", signup_raid_id=1,
        )
        self.assertEqual(len(first_time.children), 2)
        self.assertEqual(first_time.note.label, "Raid note (optional)")
        self.assertFalse(first_time.note.required)
        editing = raid.ClassPickerView(42, editing_character_id=7)
        self.assertIn("Update character", json.dumps(editing.to_components()))
        picker = raid.SpecPickerView(42, "Mage", editing_character_id=7)
        self.assertEqual(picker.editing_character_id, 7)

    async def test_signup_note_modal_saves_optional_note_for_correct_member(self):
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "my_signup", return_value=None), \
             patch.object(raid, "save_signup") as saving:
            modal = raid.SignupNoteModal(42, 1, 5, "First Last")
            modal.note._value = "Can stay for 2 hours"
            response = SimpleNamespace(send_message=AsyncMock())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID, response=response,
            )
            await modal.on_submit(interaction)
            saving.assert_called_once_with(
                1, 42, 5, note="Can stay for 2 hours"
            )
            self.assertTrue(response.send_message.await_args.kwargs["ephemeral"])
            response.send_message.reset_mock()
            interaction.user.id = 99
            await modal.on_submit(interaction)
            saving.assert_called_once()
            self.assertIn("isn't yours", response.send_message.await_args.args[0])

    async def test_character_manage_combines_profile_and_update_wowsims(self):
        character = (7, "Snore Mon", "Warrior", "Protection", "Tank", None)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42), guild_id=77,
            response=SimpleNamespace(
                edit_message=AsyncMock(), send_modal=AsyncMock(),
                send_message=AsyncMock(),
            ),
        )
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "character_race", return_value="Orc"), \
             patch.object(raid, "main_character_ids", return_value={7}), \
             patch.object(raid, "member_characters", return_value=[character]), \
             patch.object(raid, "has_wowsims_export", return_value=False) as has_export:
            view = raid.CharacterManageView(42, 7, character)
            buttons = {b.label: b for b in view.walk_children()
                       if isinstance(b, discord.ui.Button)}
            self.assertEqual(list(buttons), [
                "Class / Spec / Race", "Update WoWSims",
                "Set Main", "Delete", "Back",
            ])
            self.assertNotIn("Race", buttons)
            await buttons["Update WoWSims"].callback(interaction)
            self.assertIsInstance(
                interaction.response.edit_message.await_args.kwargs["view"],
                raid.WoWSimsInstructionsView,
            )
            has_export.return_value = True
            interaction.response.edit_message.reset_mock()
            await buttons["Update WoWSims"].callback(interaction)
            self.assertIsInstance(
                interaction.response.send_modal.await_args.args[0],
                raid.WoWSimsExportModal,
            )
            interaction.response.edit_message.assert_not_awaited()
            await buttons["Class / Spec / Race"].callback(interaction)
            screen = interaction.response.edit_message.await_args.kwargs["view"]
            self.assertIsInstance(screen, raid.CharacterProfileEditView)
            nested = {b.label: b for b in screen.walk_children()
                      if isinstance(b, discord.ui.Button)}
            self.assertEqual(list(nested), [
                "Change class / spec", "Change race", "Back",
            ])
            await nested["Change class / spec"].callback(interaction)
            self.assertIsInstance(
                interaction.response.edit_message.await_args.kwargs["view"],
                raid.ClassPickerView,
            )
            await nested["Change race"].callback(interaction)
            self.assertIsInstance(
                interaction.response.edit_message.await_args.kwargs["view"],
                raid.CharacterRaceView,
            )

    async def test_class_spec_change_returns_to_combined_race_editor(self):
        character = (7, "Snore Mon", "Warrior", "Fury", "DPS", None)
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "update_character_profile", return_value="Snore Mon"), \
             patch.object(raid, "member_characters", return_value=[character]), \
             patch.object(raid, "character_race", return_value="Orc"), \
             patch.object(raid, "refresh_character_signups", new_callable=AsyncMock), \
             patch.object(raid, "refresh_lfg_for_member", new_callable=AsyncMock):
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                response=SimpleNamespace(
                    edit_message=AsyncMock(), send_message=AsyncMock(),
                ),
            )
            await raid.apply_profile_update(
                interaction, 42, 7, "Warrior", "Fury", "DPS",
                return_to_editor=True,
            )
            screen = interaction.response.edit_message.await_args.kwargs["view"]
            self.assertIsInstance(screen, raid.CharacterProfileEditView)
            self.assertIn("Change race", [
                b.label for b in screen.walk_children()
                if isinstance(b, discord.ui.Button)
            ])

    async def test_update_wizard_saves_profile_in_place(self):
        with patch.object(raid, "GUILD_ID", 77), \
             patch.object(raid, "update_character_profile",
                          return_value="First Last") as saving:
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(
                    edit_message=AsyncMock(), send_message=AsyncMock()
                ),
            )
            await raid.apply_profile_update(
                interaction, 42, 7, "Warrior", "Protection", "Tank"
            )
            saving.assert_called_once_with(
                42, 7, "Warrior", "Protection", "Tank"
            )
            self.assertIsInstance(
                interaction.response.edit_message.await_args.kwargs["view"],
                raid.ProfileUpdatedView,
            )

    async def test_absent_button_opens_modal_directly(self):
        with patch.object(raid, "OWNER_ID", 42), patch.object(raid, "GUILD_ID", 77):
            cog = raid.RaidPrototype(SimpleNamespace())
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42), guild_id=77,
                channel_id=raid.TEST_CHANNEL_ID,
                response=SimpleNamespace(send_modal=AsyncMock()),
            )
            await cog.on_absent(interaction, 5)
            modal = interaction.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, raid.AbsenceNoteModal)
            self.assertEqual(modal.raid_id, 5)
