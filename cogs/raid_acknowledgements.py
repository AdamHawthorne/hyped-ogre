"""Compatibility alias for raid roster announcement acknowledgement storage.

The existing cogs.raid_acknowledgements path and the canonical Hyped Ogre
implementation resolve to the same module object. Keep this alias while
raid_signup, raid_poster and their persistent Discord messages are migrated.
"""

import sys

from hyped_ogre.features.raids import acknowledgements as _canonical

sys.modules[__name__] = _canonical
