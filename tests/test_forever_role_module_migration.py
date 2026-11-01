"""Forever role and leadership application module migration guards."""

import importlib
from unittest.mock import AsyncMock
import unittest

import discord

from cogs import forever_role as old
from hyped_ogre.features.forever import roles


class ForeverRoleModuleMigrationTests(unittest.TestCase):
    def test_same_module_and_classes_preserve_legacy_import(self):
        self.assertIs(old, roles)
        self.assertIs(importlib.import_module("cogs.forever_role"), roles)
        for name in ("ForeverRoleView", "ForeverRoleV2Panel",
                     "LeadershipApplicationPost", "LeadershipApplicationModal",
                     "LeadershipInterestView", "build_forever_role_embed",
                     "toggle_forever_role", "LEADERSHIP_INTERESTS"):
            with self.subTest(name=name):
                self.assertIs(getattr(old, name), getattr(roles, name))

    def test_both_persistent_role_views_have_original_discord_buttons(self):
        original = old.ForeverRoleView(77, 88, lambda message: None)
        self.assertTrue(original.is_persistent())
        self.assertIn(
            "hyped_ogre_forever_role",
            [x.custom_id for x in original.walk_children()
             if isinstance(x, discord.ui.Button)],
        )
        panel = roles.ForeverRoleV2Panel(
            77, 88, lambda message: None, preview=False
        )
        self.assertTrue(panel.is_persistent())
        ids = {x.custom_id for x in panel.walk_children()
               if isinstance(x, discord.ui.Button)}
        self.assertEqual(ids, {
            "hyped_ogre_forever_role_v2",
            "hyped_ogre_forever_leadership_v2",
        })
        preview = old.ForeverRoleV2Panel(
            77, 88, lambda message: None, preview=True
        )
        self.assertEqual(preview.timeout, 300)


if __name__ == "__main__":
    unittest.main()
