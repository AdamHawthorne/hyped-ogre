"""Legacy import path for Hyped Ogre raid scheduling and roster panels.

All existing raid signup, Sheets, poster and Discord view code continues
importing cogs.raid_schedule. Both import paths share the same module,
including its _UNCHANGED sentinel, database context manager and V2 view.
"""

import sys

from hyped_ogre.features.raids import schedule as _canonical

sys.modules[__name__] = _canonical
