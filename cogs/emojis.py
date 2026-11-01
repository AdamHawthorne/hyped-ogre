"""Compatibility shim for the original Hyped Ogre emoji import path.

Existing cogs and persistent Discord views continue using cogs.emojis.
The implementation lives in hyped_ogre.core.emojis.
"""

from hyped_ogre.core.emojis import (
    EMOJI_IDS, markup, option_emoji, combat_role,
    roll_dice_name, roll_dice_markup, roll_dice_emoji,
)

__all__ = [
    "EMOJI_IDS", "markup", "option_emoji", "combat_role",
    "roll_dice_name", "roll_dice_markup", "roll_dice_emoji",
]
