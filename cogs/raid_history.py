"""Backward-compatible import path for raid attendance history.

The implementation now lives in hyped_ogre.features.raids.history.
Keep these exports available for the existing raid and Google Sheets cogs.
"""

from hyped_ogre.features.raids.history import STATES, history_for_raid

__all__ = ["STATES", "history_for_raid"]
