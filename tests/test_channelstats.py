"""Static regression tests for the private channel statistics command."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ChannelStatsTests(unittest.TestCase):
    def test_command_module_parses(self):
        source = (ROOT / "hyped_ogre/features/channelstats.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertTrue(any(
            isinstance(node, ast.AsyncFunctionDef) and node.name == "setup"
            for node in tree.body
        ))

    def test_command_is_owner_only_ephemeral_and_read_only(self):
        source = (ROOT / "hyped_ogre/features/channelstats.py").read_text(encoding="utf-8")
        self.assertIn("interaction.user.id != OWNER_ID", source)
        self.assertIn("interaction.guild_id != GUILD_ID", source)
        self.assertIn("default_permissions(manage_guild=True)", source)
        self.assertIn("defer(ephemeral=True", source)
        self.assertIn("history(limit=None", source)
        self.assertIn("Counter()", source)
        self.assertNotIn("sqlite3", source)

    def test_extension_loads_at_startup(self):
        coordinator = (ROOT / "hyped_ogre/bot.py").read_text(encoding="utf-8")
        self.assertIn('await bot.load_extension("hyped_ogre.features.channelstats")', coordinator)


if __name__ == "__main__":
    unittest.main()
