"""Offline guard for relocating the Hyped Ogre executable coordinator."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BotEntrypointModuleMigrationTests(unittest.TestCase):
    def test_legacy_boot_command_only_runs_canonical_bot_as_main(self):
        wrapper = (ROOT / "rollf.py").read_text(encoding="utf-8")
        tree = ast.parse(wrapper)
        self.assertIn('runpy.run_module("hyped_ogre.bot", run_name="__main__", alter_sys=False)', wrapper)
        self.assertNotIn("bot.run(", wrapper)
        self.assertNotIn("load_dotenv(", wrapper)
        self.assertNotIn("sqlite3.connect(", wrapper)
        self.assertTrue(any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            for node in tree.body
        ))

    def test_canonical_bot_retains_existing_database_and_command_functions(self):
        source = (ROOT / "hyped_ogre/bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {x.name for x in tree.body
                 if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertTrue({
            "ensure_schema", "db", "insert_roll", "bot_daily_roll",
            "perform_roll", "get_user_stats", "sync_flameboard_message",
            "load_hyped_extensions", "daily_post_loop",
        }.issubset(names))
        self.assertIn('await bot.load_extension("cogs.lfg_preview")', source)
        self.assertIn('await bot.load_extension("cogs.raid_signup")', source)
        self.assertIn('await bot.load_extension("cogs.countdown")', source)
        self.assertIn('await bot.load_extension("cogs.streams")', source)
        self.assertIn('await bot.load_extension("cogs.suggestions")', source)
        self.assertIn("bot.setup_hook = load_hyped_extensions", source)
        self.assertIn("bot.run(TOKEN)", source)
        self.assertIn("CREATE TABLE IF NOT EXISTS rolls", source)
        self.assertIn("CREATE TABLE IF NOT EXISTS flameboard_posts", source)


if __name__ == "__main__":
    unittest.main()
