"""Safety guards for relocating active Discord extensions by source path.

Original cogs module identities and setup functions must be preserved for
discord.py's extension lifecycle, cross-feature imports and monkeypatching.
No bot login or public messages are sent by these checks.
"""

import importlib
import inspect
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import discord
from discord.ext import commands

from hyped_ogre.core.legacy_source import load_legacy_extension, _SOURCE_FILES


class DiscordExtensionModuleMigrationTests(unittest.TestCase):
    def test_source_paths_are_allowlisted_and_not_arbitrary(self):
        self.assertEqual(set(_SOURCE_FILES), {
            "cogs.countdown", "cogs.lfg_preview", "cogs.raid_signup",
            "cogs.streams", "cogs.suggestions",
        })
        with self.assertRaisesRegex(ValueError, "Unrecognised"):
            load_legacy_extension("cogs.untrusted")
        with self.assertRaisesRegex(ValueError, "Unrecognised"):
            load_legacy_extension("../../anything")
        root = Path(__file__).resolve().parents[1]
        for relative in _SOURCE_FILES.values():
            with self.subTest(relative=relative):
                self.assertTrue((root / "hyped_ogre" / relative).is_file())

    def test_original_extension_modules_retain_cog_and_setup_ownership(self):
        with patch.dict(os.environ, {"HYPED_GUILD_ID": "77"}):
            for module_name, class_name in (
                ("cogs.countdown", "CountdownCog"),
                ("cogs.lfg_preview", "LFGPreview"),
                ("cogs.raid_signup", "RaidPrototype"),
                ("cogs.streams", "StreamsCog"),
                ("cogs.suggestions", "SuggestionsCog"),
            ):
                with self.subTest(module=module_name):
                    module = importlib.import_module(module_name)
                    cog = getattr(module, class_name)
                    self.assertEqual(module.__name__, module_name)
                    self.assertTrue(Path(module.__file__).as_posix().endswith(
                        module_name.replace(".", "/") + ".py"
                    ))
                    self.assertEqual(module.setup.__module__, module_name)
                    self.assertEqual(cog.__module__, module_name)
                    self.assertTrue(issubclass(cog, commands.Cog))
                    self.assertEqual(module.__package__, "cogs")
                    source_file = inspect.getsourcefile(module.setup)
                    self.assertTrue(
                        Path(source_file).as_posix().endswith(_SOURCE_FILES[module_name]),
                        source_file,
                    )
        from cogs import raid_signup, lfg_preview
        self.assertIs(lfg_preview.raid_signup, raid_signup)

    def test_old_source_stays_in_single_canonical_location(self):
        root = Path(__file__).resolve().parents[1]
        for name, relative in _SOURCE_FILES.items():
            with self.subTest(module=name):
                old = root / (name.replace(".", "/") + ".py")
                stub = old.read_text(encoding="utf-8")
                self.assertIn("load_legacy_extension(__name__)", stub)
                self.assertNotIn("async def setup(bot):", stub)
                source = (root / "hyped_ogre" / relative).read_text(encoding="utf-8")
                self.assertIn("async def setup(bot):", source)
                self.assertNotIn("load_legacy_extension(", source)


class DiscordExtensionUnloadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_suggestions_extension_load_unload_releases_registered_cog(self):
        with patch.dict(os.environ, {"HYPED_GUILD_ID": "77"}):
            bot = commands.Bot(command_prefix="test_", intents=discord.Intents.none())
            try:
                await bot.load_extension("cogs.suggestions")
                self.assertIsNotNone(bot.get_cog("SuggestionsCog"))
                self.assertIn("cogs.suggestions", bot.extensions)
                await bot.unload_extension("cogs.suggestions")
                self.assertIsNone(bot.get_cog("SuggestionsCog"))
                self.assertNotIn("cogs.suggestions", bot.extensions)
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
