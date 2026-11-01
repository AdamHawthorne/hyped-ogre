"""Compatibility alias for the original countdown clock helpers.

Keep existing cogs.countdown_core imports working for posted reminder
panels and owner controls. The implementation now lives in
hyped_ogre.features.countdowns.core, with the *same Python module object*
shared between legacy and canonical import paths.
"""

import sys

from hyped_ogre.features.countdowns import core as _canonical

sys.modules[__name__] = _canonical
