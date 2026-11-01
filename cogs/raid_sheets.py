"""Compatibility alias for the original Hyped Ogre raid Google Sheets bridge.

The live bot and raid-sheet dashboard continue importing cogs.raid_sheets.
The canonical integration now lives at hyped_ogre.integrations.sheets.
Both import paths resolve to one module, retaining configuration state,
patchable HTTP helpers, and the same read/write ownership boundaries.
"""

import sys

from hyped_ogre.integrations import sheets as _canonical

sys.modules[__name__] = _canonical
