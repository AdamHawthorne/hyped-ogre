"""Legacy import compatibility for roll_gamble.

Hyped Ogre's roll engine still imports the original cogs path. Keep one
module object so stored state, classes and patch targets remain identical.
Canonical source: hyped_ogre.features.rolls.gamble.
"""

import sys

from hyped_ogre.features.rolls import gamble as _canonical

sys.modules[__name__] = _canonical
