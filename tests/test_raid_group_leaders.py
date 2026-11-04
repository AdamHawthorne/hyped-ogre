"""Instance leaders, original V2 links, and compact confirmation layout."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import raid_schedule as schedule, raid_signup as signup
from cogs import raid_sheets_v3 as sheets
from hyped_ogre.features.raids import announcer
from hyped_ogre.features.raids.leaders import effective_leaders, set_group_leader, read_leaders
from hyped_ogre.features.classrooms.config import GUILD_ID


class RaidGroupLeaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "raid.db")
        signup.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        schedule.save_raid(self.path, 2, "2026-12-09 20:00",
                           "Hyjal (20m)", "Barrows (10m)", "Ony (40m)")
        schedule.record_panel(self.path, 2, 333, 444, creator_id=42)
        self.selected = [
            ("42", "Snore", "Hyjal, Barrows 🔸, Ony", "Warrior", "Tank", "Protection"),
            ("43", "Other", "Barrows 🔹", "Mage", "DPS", "Frost"),
            ("44", "TankTwo", "Hyjal, Barrows 🔸", "Warrior", "Tank", "Protection"),
            ("45", "OnyMage", "Ony", "Mage", "DPS", "Frost"),
        ]
        schedule.raid_status(self.path, 2, "confirm", confirmed_entries=self.selected)
        sheets._save_public_for_one(
            self.path, 2,
            [(name, assignment, klass, role, spec, member)
             for member, name, assignment, klass, role, spec in self.selected],
        )

    def test_owner_defaults_to_each_eligible_instance_but_not_both_barrows(self):
        event = schedule.read_raid(self.path, 2)
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            leaders = effective_leaders(self.path, event, self.selected)
        self.assertEqual(leaders, {
            "Hyjal": "42", "Barrows 🔸": "42", "Ony": "42",
        })
        self.assertNotIn("Barrows 🔹", leaders)

    def test_select_explicit_leader_and_reject_double_lead(self):
        self.assertEqual(set_group_leader(self.path, 2, "Barrows 🔹", 43), "43")
        self.assertEqual(read_leaders(self.path, 2)["Barrows 🔹"], "43")
        with self.assertRaisesRegex(ValueError, "assigned"):
            set_group_leader(self.path, 2, "Hyjal", 43)
        # Member 43 isn't in Hyjal: the membership check also blocks this.
        self.assertEqual(set_group_leader(self.path, 2, "Hyjal", 44), "44")
        self.assertEqual(set_group_leader(self.path, 2, "Hyjal", None), None)
        self.assertEqual(read_leaders(self.path, 2)["Hyjal"], "")

    def test_one_player_can_lead_hyjal_barrows_and_ony(self):
        with self.assertRaisesRegex(ValueError, "assigned"):
            set_group_leader(self.path, 2, "Hyjal", 43)
        self.assertEqual(set_group_leader(self.path, 2, "Hyjal", 42), "42")
        self.assertEqual(set_group_leader(self.path, 2, "Barrows 🔸", 42), "42")
        self.assertEqual(set_group_leader(self.path, 2, "Ony", 42), "42")

    def test_both_barrows_leadership_is_rejected_even_when_roster_ambiguous(self):
        set_group_leader(self.path, 2, "Barrows 🔸", 42)
        # For corrupted/legacy input where a character is in both split
        # selections, guard the actual write, not just the eligibility check.
        from hyped_ogre.features.raids import leaders
        original = leaders.member_groups
        def both_for_snore(assignment):
            groups = original(assignment)
            if assignment.startswith("Hyjal"):
                return groups | {"Barrows 🔹"}
            return groups
        with patch.object(leaders, "member_groups", side_effect=both_for_snore):
            with self.assertRaisesRegex(ValueError, "cannot lead both Barrows"):
                set_group_leader(self.path, 2, "Barrows 🔹", 42)
        self.assertNotIn("Barrows 🔹", read_leaders(self.path, 2))

    def test_embed_is_single_grid_and_links_title_and_creator(self):
        with patch.dict(os.environ, {"OWNER_ID": "42"}):
            embeds = announcer.roster_embeds(
                schedule.read_raid(self.path, 2),
                self.selected, path=self.path, creator_name="Snore",
            )
        self.assertEqual(len(embeds), 1)
        embed = embeds[0]
        self.assertEqual(embed.title, "<Hyped> Raid #2 | Confirmed roster")
        self.assertEqual(embed.url,
            f"https://discord.com/channels/{GUILD_ID}/333/444")
        self.assertEqual(embed.author.name, "Created by Snore")
        self.assertEqual(embed.author.url, "https://discord.com/users/42")
        self.assertEqual(len(embed.fields), 24)
        self.assertEqual([field.inline for field in embed.fields[:6]],
                         [False, True, True, False, True, True])
        self.assertEqual(embed.fields[0].name, "Hyjal (2) 🔹🔸")
        self.assertEqual(embed.fields[0].value, "Leader: <@42>")
        self.assertEqual(embed.fields[6].name, "Barrows (2) 🔸")
        self.assertEqual(embed.fields[12].name, "Barrows (1) 🔹")
        self.assertEqual(embed.fields[18].name, "Ony (2)")
        self.assertNotIn("\\n", embed.fields[1].value)
        self.assertLess(len(embed), 5900)

    def test_reaction_tick_inside_v1_not_in_v2(self):
        event = schedule.read_raid(self.path, 2)
        embeds = announcer.roster_embeds(
            event, self.selected, acknowledged_ids={"42"}
        )
        self.assertIn("<:check:1566948806535159808>", embeds[0].fields[1].value)
        self.assertNotIn("<:check:1566948806535159808>", embeds[0].fields[2].value)
        # The saved V2 post identity is never replaced with a new ID.
        self.assertEqual(schedule.read_raid(self.path, 2)["panel_message_id"], 444)


if __name__ == "__main__":
    unittest.main()
