"""Compatibility alias for existing LFG dungeon-request helpers.

The dungeon requests live in the existing lfg_board.db database, and the
LFG Discord extension keeps its original import path and button IDs.
The canonical module is hyped_ogre.features.lfg.dungeons.
"""

import sys

from hyped_ogre.features.lfg import dungeons as _canonical

sys.modules[__name__] = _canonical
