"""Compatibility alias for original LFG launch-preview utilities.

The original cogs import still resolves to the same module object, while
the implementation lives in the Hyped Ogre LFG feature package.
"""

import sys

from hyped_ogre.features.lfg import demo as _canonical

sys.modules[__name__] = _canonical
