"""Legacy listener module for Hyped Flameboard.

rollf.py imports FlameboardEvents here and registers the Cog itself.
The canonical listener lives in hyped_ogre.features.flameboard.events.
Both paths resolve to the same module and class.
"""

import sys

from hyped_ogre.features.flameboard import events as _canonical

sys.modules[__name__] = _canonical
