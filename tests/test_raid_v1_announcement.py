"""V1 announcement layout, real confirmation links, and V2 isolation."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import raid_acknowledgements as ack
from cogs import raid_schedule as schedule
from cogs import raid_signup as raid
from hyped_ogre.features.raids import announcer
from hyped_ogre.features.classrooms.config import GUILD_ID


class RaidAnnouncementLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raids.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        self.event = schedule.save_raid(
            self.path, 1, "2026-12-09 21:00",
            "Hyjal (20m)", "Barrows (10m)",
            wowsims_policy="fresh",
            check_message_url=(
                "https://discord.com/channels/451518969417957376/"
                "1563663151323553802/1565538887076679731"),
        )
        schedule.record_panel(self.path, 1, 111, 222)

    @staticmethod
    def selected():
        return [
            ("101", "Prot", "Hyjal, Barrows 🔸", "Warrior", "Tank", "Protection"),
            ("102", "Holy", "Hyjal, Barrows 🔹", "Priest", "Healer", "Holy"),
            ("103", "Survival", "Hyjal, Barrows 🔸", "Hunter", "DPS", "Survival"),
            ("104", "Beast", "Hyjal, Barrows 🔹", "Hunter", "DPS", "Beast Mastery"),
            ("105", "Frost", "Hyjal", "Mage", "DPS", "Frost"),
            ("106", "Feral", "Hyjal", "Druid", "DPS", "Feral"),
        ]

    def test_collecting_v2_has_wowsims_but_never_manual_confirmation_link(self):
        event = schedule.read_raid(self.path, 1)
        text = schedule.raid_panel_intro(event)
        self.assertIn("Fresh export required", text)
        self.assertIn("[Get addon]", text)
        self.assertNotIn("Confirm raid spot", text)
        self.assertNotIn("1565538887076679731", text)
        self.assertFalse(event.get("confirmation_url"))
        self.assertIsInstance(schedule.RaidEventPanel(event), discord.ui.LayoutView)

    def test_published_v2_links_v1_and_keeps_original_signup_wowsims(self):
        schedule.raid_status(
            self.path, 1, "confirm",
            confirmed_entries=[("101", "Prot", "Hyjal", "Warrior", "Tank", "Protection")],
        )
        before = schedule.read_raid(self.path, 1)
        self.assertNotIn("Confirm raid spot", schedule.raid_panel_intro(before))
        ack.record_announcement(self.path, 1, 333, 444, [("101", "Prot")])
        after = schedule.read_raid(self.path, 1)
        url = f"https://discord.com/channels/{GUILD_ID}/333/444"
        self.assertEqual(after["confirmation_url"], url)
        text = schedule.raid_panel_intro(after)
        self.assertIn(f"[Confirm raid spot]({url})", text)
        self.assertNotIn("1565538887076679731", text)
        self.assertIn("WoWSims:", text)
        self.assertIn("Get addon", text)
        self.assertIn("Instances:", text)
        # Even a cached event without its computed link renders correctly;
        # the persistent V2 panel reloads the recorded message ID.
        from cogs import raid_signup as signup
        with patch.object(signup, "RAID_DB", self.path):
            panel = signup.RaidPrototype(SimpleNamespace()).make_panel(before)
        rendered = "\n".join(item.content for item in panel.walk_children()
                             if isinstance(item, discord.ui.TextDisplay))
        self.assertIn(f"[Confirm raid spot]({url})", rendered)
        buttons = [item.label for item in panel.walk_children()
                   if isinstance(item, discord.ui.Button)]
        self.assertTrue({"Attend", "WoWSims", "Absent"}.issubset(buttons))
        # Unpublishing invalidates the link without deleting V2 sign-ups.
        ack.retract(self.path, 1)
        self.assertNotIn("Confirm raid spot",
                         schedule.raid_panel_intro(schedule.read_raid(self.path, 1)))

    def test_v1_two_by_two_role_grid_and_instance_split_counts(self):
        embeds = announcer.roster_embeds(
            schedule.read_raid(self.path, 1), self.selected()
        )
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].title,
                         "<Hyped> Raid #1 | Confirmed roster")
        self.assertEqual(embeds[0].url,
                         f"https://discord.com/channels/{GUILD_ID}/111/222")
        self.assertNotIn("author", embeds[0].to_dict())
        self.assertEqual(len(embeds[0].fields), 15)  # three groups, five fields each
        self.assertEqual([f.name for f in embeds[0].fields[:5]],
                         ["Hyjal (6) 🔹🔸 · Unassigned", "\u200b", "\u200b",
                          "Melee (2)", "Ranged (2)"])
        self.assertEqual([f.inline for f in embeds[0].fields[:5]],
                         [True, True, False, True, True])
        self.assertIn("**Tanks (1)**", embeds[0].fields[0].value)
        self.assertIn("**Healers (1)**", embeds[0].fields[1].value)
        self.assertEqual(embeds[0].fields[5].name, "Barrows (2) 🔸 · Unassigned")
        self.assertEqual(embeds[0].fields[10].name, "Barrows (2) 🔹 · Unassigned")
        self.assertIn("Survival", embeds[0].fields[3].value)
        self.assertIn("Beast", embeds[0].fields[4].value)
        self.assertIn("Frost", embeds[0].fields[4].value)
        self.assertEqual(embeds[0].thumbnail.url, schedule.RAID_PANEL_IMAGE)
        self.assertEqual(embeds[0].footer.text,
                         "Contact: @Leadership or mark absent if needed")
        self.assertNotIn("Fresh export", str([e.to_dict() for e in embeds]))
        for embed in embeds:
            self.assertLessEqual(len(embed.fields), 25)
            self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(sum(len(e) for e in embeds), 5900)

    def test_unregistered_guest_is_visible_in_grid_without_reaction_id(self):
        selection = self.selected() + [
            ("", "GuestShaman", "Hyjal", "Shaman", "DPS", "Enhancement"),
        ]
        embeds = announcer.roster_embeds(self.event, selection)
        self.assertEqual(embeds[0].fields[0].name,
                         "Hyjal (7) 🔹🔸 · Unassigned")
        self.assertIn("GuestShaman", embeds[0].fields[3].value)
        self.assertNotIn("<@None>", str([e.to_dict() for e in embeds]))

    def test_four_instance_groups_stay_in_single_v1_embed(self):
        selected = self.selected() + [
            ("107", "OnyTank", "Ony", "Warrior", "Tank", "Protection"),
        ]
        self.event["instance_3"] = "Ony (40m)"
        embeds = announcer.roster_embeds(self.event, selected)
        self.assertEqual(len(embeds), 1)
        self.assertEqual(len(embeds[0].fields), 20)
        self.assertEqual(embeds[0].fields[15].name, "Ony (1) · Unassigned")
        self.assertIn("**Tanks (1)**", embeds[0].fields[15].value)

    def test_thirty_guild_players_fit_without_losing_names(self):
        people = [
            (str(100+i), f"Member{i:02}", "Hyjal, Barrows " +
             ("🔸" if i % 2 else "🔹"), "Mage", "DPS", "Frost")
            for i in range(30)
        ]
        embeds = announcer.roster_embeds(self.event, people)
        self.assertLessEqual(len(embeds), 10)
        self.assertLessEqual(sum(len(e) for e in embeds), 5900)
        body = "\n".join(f.value for e in embeds for f in e.fields)
        for player in people:
            self.assertEqual(body.count(player[1]), 2)
        self.assertTrue(all(len(field.value) <= 1024
                            for embed in embeds for field in embed.fields))


class RaidAnnouncementWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "raids.db")
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 1, "2026-12-09 21:00", "Hyjal (20m)")
        schedule.record_panel(self.path, 1, 333, 555)
        schedule.raid_status(self.path, 1, "confirm",
                             confirmed_entries=[
                                 ("101", "Mage", "Hyjal", "Mage", "DPS", "Frost")])
        self.selected = [("101", "Mage", "Hyjal", "Mage", "DPS", "Frost")]
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.guild = SimpleNamespace(id=GUILD_ID, me=object())
        self.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True, send_messages=True,
            add_reactions=True, read_message_history=True,
        )
        self.message = SimpleNamespace(
            id=444, jump_url=f"https://discord.com/channels/{GUILD_ID}/333/444",
            add_reaction=AsyncMock(), delete=AsyncMock(),
        )
        self.channel.send = AsyncMock(return_value=self.message)
        self.bot = SimpleNamespace(get_channel=lambda _id: self.channel,
                                   fetch_channel=AsyncMock())

    async def test_initial_announcement_is_v1_and_saves_reaction_permalink(self):
        selected = self.selected + [
            ("", "GuestMage", "Hyjal", "Mage", "DPS", "Frost"),
        ]
        link = await announcer.announce(
            self.bot, self.path, 1, GUILD_ID, selected=selected,
        )
        self.assertEqual(link, self.message.jump_url)
        call = self.channel.send.await_args.kwargs
        self.assertIn("<@101>", call["content"])
        self.assertEqual(len(call["embeds"]), 1)
        self.assertTrue(call["embeds"][0].fields[0].name.startswith(
            "Hyjal (2) 🔹🔸"))
        self.assertIn("GuestMage", call["embeds"][0].fields[-1].value)
        self.assertNotIn("GuestMage", call["content"])
        self.assertTrue(all(isinstance(e, discord.Embed) for e in call["embeds"]))
        self.assertEqual(ack.announcement(self.path, 1), (333, 444))
        self.assertIn("/333/444", schedule.raid_panel_intro(
            schedule.read_raid(self.path, 1)))
        self.assertEqual(schedule.read_raid(self.path, 1)["panel_message_id"], 555)
        self.message.add_reaction.assert_awaited_once_with(ack.ACK_EMOJI)

    async def test_confirmation_no_longer_contains_early_invite_commands(self):
        import os
        with patch.dict(os.environ, {"OWNER_ID": "101"}):
            await announcer.announce(
                self.bot, self.path, 1, GUILD_ID, selected=self.selected,
            )
            text = self.channel.send.await_args.kwargs["content"]
            self.assertTrue(text.startswith(
                "React below to confirm raid spot " + ack.ACK_EMOJI
            ))
            self.assertIn("**Still need nod (1/1):** <@101>", text)
            self.assertNotIn("/w", text)
            self.assertNotIn("View raid roster", text)
            self.assertNotIn("Ogre cross name off list", text)
            from cogs import raid_sheets_v3 as sheets
            sheets._save_public_for_one(
                self.path, 1, [("Mage", "Hyjal", "Mage", "DPS", "Frost", "101")]
            )
            ack.acknowledge(self.path, 333, 444, 101)
            original = self.channel.send.await_args.kwargs
            edited = SimpleNamespace(
                content=original["content"], embeds=original["embeds"],
                edit=AsyncMock(),
            )
            self.channel.fetch_message = AsyncMock(return_value=edited)
            with patch.object(announcer.raid_sheets_v3, "sync_acknowledgements",
                              return_value=True):
                await announcer.refresh(self.bot, self.path, 1, GUILD_ID)
            updated = edited.edit.await_args.kwargs["content"]
            self.assertIn("**All 1 acknowledged.** Ogre got nod from everyone!", updated)
            self.assertNotIn("/w", updated)
            self.assertNotIn("View raid roster", updated)

    async def test_reaction_refresh_updates_outstanding_mentions_without_reping(self):
        from cogs import raid_sheets_v3 as sheets
        await announcer.announce(self.bot, self.path, 1, GUILD_ID,
                                 selected=self.selected)
        sheets._save_public_for_one(
            self.path, 1, [("Mage", "Hyjal", "Mage", "DPS", "Frost", "101")])
        recorded = ack.acknowledge(self.path, 333, 444, 101)
        self.assertEqual(recorded, 1)
        old = self.channel.send.await_args.kwargs
        edited = SimpleNamespace(content=old["content"], embeds=old["embeds"],
                                 edit=AsyncMock())
        self.channel.fetch_message = AsyncMock(return_value=edited)
        with patch.object(announcer.raid_sheets_v3, "sync_acknowledgements",
                          return_value=True):
            await announcer.refresh(self.bot, self.path, 1, GUILD_ID)
        kw = edited.edit.await_args.kwargs
        self.assertIn("All 1 acknowledged", kw["content"])
        self.assertNotIn("<@101>", kw["content"])
        self.assertFalse(kw["allowed_mentions"].everyone)
        self.assertFalse(kw["allowed_mentions"].users)
        self.assertEqual(ack.dirty_announcements(self.path), [])
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertIn(ack.ACK_EMOJI, kw["embeds"][0].fields[0].value)


if __name__ == "__main__":
    unittest.main()
