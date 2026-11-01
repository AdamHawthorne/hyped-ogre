"""Compatibility and behaviour guards for shared Discord emoji helpers."""

from datetime import date
import unittest

from cogs import emojis as legacy
from hyped_ogre.core import emojis as core


class SharedEmojiTests(unittest.TestCase):
    def test_old_imports_reference_new_implementation(self):
        self.assertIs(legacy.EMOJI_IDS, core.EMOJI_IDS)
        for name in ("markup", "option_emoji", "combat_role", "roll_dice_name",
                     "roll_dice_markup", "roll_dice_emoji"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(core, name))

    def test_emoji_ids_and_markup_remain_unchanged(self):
        self.assertEqual(core.markup("ogrecoin"),
                         "<:ogrecoin:1565481626488807434>")
        self.assertEqual(core.markup("unknown"), "")
        self.assertIsNone(core.option_emoji("unknown"))

    def test_sunday_dice_are_different_from_weekdays(self):
        # The caller supplies the already-localised UK roll day.
        self.assertEqual(core.roll_dice_name(date(2026, 11, 1)), "ogredie")
        self.assertEqual(core.roll_dice_name(date(2026, 11, 2)), "ogreivory")
        self.assertEqual(core.roll_dice_markup(date(2026, 11, 1)),
                         "<:ogredie:1566175682034270288>")
        self.assertEqual(core.roll_dice_markup(date(2026, 11, 2)),
                         "<:ogreivory:1566177408556605460>")

    def test_visual_role_classification_unchanged(self):
        self.assertEqual(core.combat_role("DPS", "Hunter", "Survival"), "Melee")
        self.assertEqual(core.combat_role("DPS", "Hunter", "Beast Mastery"), "Ranged")
        self.assertEqual(core.combat_role("DPS", "Mage", "Frost"), "Caster")
        self.assertEqual(core.combat_role("Healer", "Druid", "Restoration"), "Healer")


if __name__ == "__main__":
    unittest.main()
