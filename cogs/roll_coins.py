"""Legacy import compatibility for roll_coins.

Hyped Ogre's roll engine still imports the original cogs path. Keep one
module object so stored state, classes and patch targets remain identical.
Canonical source: hyped_ogre.features.rolls.coins.
"""

import sys

from hyped_ogre.features.rolls import coins as _canonical

sys.modules[__name__] = _canonical
