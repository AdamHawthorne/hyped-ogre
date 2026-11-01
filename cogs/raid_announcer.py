"""Compatibility alias for raid announcement posting and reaction handling.

Existing raid signup/panel code imports cogs.raid_announcer. The canonical
implementation is now hyped_ogre.features.raids.announcer, with identical
methods, module state and Discord message/reaction semantics.
"""

import sys

from hyped_ogre.features.raids import announcer as _canonical

sys.modules[__name__] = _canonical
