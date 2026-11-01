"""Compatibility module for Forever role panels and leadership applications.

Keep the original cogs import for rollf.py. The actual panels and callbacks
now live in hyped_ogre.features.forever.roles. Both paths refer to the
same module object, including persistent V2 and legacy view classes.
"""

import sys

from hyped_ogre.features.forever import roles as _canonical

sys.modules[__name__] = _canonical
