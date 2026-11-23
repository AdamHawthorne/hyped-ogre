"""Regressions for compact pinned professions V2 and 15-minute API refresh."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hyped_ogre.features.characters import crafting, armory
from hyped_ogre.features.characters import professions_channel as p


class ProfessionsV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "guild.db")
        with sqlite3.connect(self.path) as con:
            con.executescript("""
                CREATE TABLE characters (
                  character_id INTEGER PRIMARY KEY, discord_id INTEGER,
                  name TEXT, is_main INTEGER, roster_visible INTEGER);
                CREATE TABLE character_board_overrides (
                  character_id INTEGER PRIMARY KEY, hidden INTEGER);
            """)
            con.executemany("INSERT INTO characters VALUES (?,?,?,?,?)", [
                (1, 1, "First Crafter", 1, 1),
                (2, 2, "Second Crafter", 1, 1),
            ])
        crafting.initialise(self.path)
        armory.initialise(self.path)

    def test_no_featured_counts_and_skill_order(self):
        crafting.save_profile(self.path, 1, {
            "primaries": [{
                "profession": {"name": "Alchemy"},
                "skill_points": 250,
                "known_recipes": [{"id": 17187, "name": "Transmute: Arcanite"}],
            }],
            "secondaries": [],
        })
        crafting.save_profile(self.path, 2, {
            "primaries": [{
                "profession": {"name": "Alchemy"},
                "skill_points": 300,
                "known_recipes": [],
            }],
            "secondaries": [],
        })
        people = crafting.board_rows(self.path)["Alchemy"]
        self.assertEqual(people[0]["name"], "Second Crafter")
        self.assertNotIn("featured_count", people[0])

    def test_exactly_top_three_even_when_other_crafters_have_295_plus(self):
        candidates = [
            {"name": "a", "skill": 220}, {"name": "b", "skill": 240},
            {"name": "c", "skill": 250}, {"name": "d", "skill": 294},
            {"name": "e", "skill": 295}, {"name": "f", "skill": 300},
        ]
        self.assertEqual(
            [r["name"] for r in p.featured_selection("Alchemy", candidates)],
            ["a", "b", "c"],
        )

    def test_header_prompt_and_crafter_discord_handles(self):
        guild = SimpleNamespace(emojis=[])
        grouped = {"Alchemy": [
            {"name": "Character One", "skill": 300, "discord_id": 123456789},
            {"name": "Character Two", "skill": 299, "discord_id": None},
        ]}
        view = p.ProfessionsV2(grouped, guild, updated_at=1795460000)
        blocks = [
            getattr(component, "content", "")
            for child in view.children
            for component in getattr(child, "children", [])
        ]
        # Header is displayed in the Section rather than the container's
        # standalone text elements.
        section = next(
            component for child in view.children
            for component in getattr(child, "children", [])
            if isinstance(component, p.discord.ui.Section)
        )
        header = section.children[0].content
        self.assertIn("## <Hyped> Guild Crafters", header)
        self.assertIn(
            'Ask Ogre, "Who can enchant Crusader?", '
            '"Who can make Arcanite Bar?"', header
        )
        self.assertEqual(
            sum("Ask Ogre" in item for item in blocks), 0
        )
        roster = "\\n".join(blocks)
        self.assertIn(
            "**Character One** / <@123456789> · 300 skill", roster
        )
        self.assertIn("**Character Two** · 299 skill", roster)
        self.assertNotIn("<@None>", roster)

    def test_v2_footer_timestamp_and_exact_top_three(self):
        guild = SimpleNamespace(emojis=[])
        grouped = {"Alchemy": [
            {"name": f"Crafter {number}", "skill": 300}
            for number in range(1, 7)
        ]}
        view = p.ProfessionsV2(grouped, guild, updated_at=1795460000)
        contents = "\n".join(
            getattr(component, "content", "")
            for child in view.children
            for component in getattr(child, "children", [])
        )
        self.assertIn(
            "Last updated: <t:1795460000:f> · <t:1795460000:R>",
            contents,
        )
        for name in ("Crafter 1", "Crafter 2", "Crafter 3"):
            self.assertIn(name, contents)
        for name in ("Crafter 4", "Crafter 5", "Crafter 6"):
            self.assertNotIn(name, contents)

    def test_v2_removes_secondary_and_extra_instructions(self):
        guild = SimpleNamespace(emojis=[])
        grouped = {
            "Cooking": [{"name": "Cook", "skill": 300}],
            "Mining": [{"name": "Miner", "skill": 300}],
            "Alchemy": [{"name": "Alchemist", "skill": 300}],
        }
        view = p.ProfessionsV2(grouped, guild)
        self.assertIsInstance(view, p.discord.ui.LayoutView)
        contents = "\n".join(
            getattr(component, "content", "")
            for child in view.children
            for component in getattr(child, "children", [])
        )
        self.assertIn("Primary professions", contents)
        self.assertIn("Gathering", contents)
        self.assertIn("New recipes", contents)
        self.assertIn(
            'Ask Ogre, "Who can enchant Crusader?", '
            '"Who can make Arcanite Bar?"', contents
        )
        self.assertNotIn("Secondary professions", contents)
        self.assertNotIn("Cook", contents)
        self.assertNotIn("featured", contents)
        self.assertNotIn("Private pilot", contents)
        self.assertNotIn("Skill 200+", contents)

    def test_updates_note_heading_displays_uploaded_discord_emoji(self):
        class TestEmoji:
            name = "updates_note"
            def __str__(self):
                return "<:updates_note:123456>"
        guild = SimpleNamespace(emojis=[TestEmoji()])
        view = p.ProfessionsV2({}, guild)
        blocks = [
            getattr(component, "content", "")
            for child in view.children
            for component in getattr(child, "children", [])
        ]
        self.assertTrue(any(
            "### <:updates_note:123456> New recipes" in block
            for block in blocks
        ))

    def test_tailoring_icon_alias_and_no_fake_emoji(self):
        self.assertEqual(
            p.profession_emoji(SimpleNamespace(emojis=[]), "Tailoring"), ""
        )

    def test_crafting_refresh_is_stale_after_fifteen_minutes(self):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO character_armory_cache "
                "(character_id,character_name,level,updated_at) "
                "VALUES (1,'First Crafter',60,1000)")
        crafting.save_profile(self.path, 1, {
            "primaries": [{"profession": {"name": "Alchemy"},
                           "skill_points": 300, "known_recipes": []}],
        })
        self.assertNotIn(
            (1, "First Crafter"),
            armory.candidates(self.path, now=1100, for_crafting=True),
        )
        self.assertIn(
            (1, "First Crafter"),
            armory.candidates(self.path, now=1901, for_crafting=True),
        )


if __name__ == "__main__":
    unittest.main()
