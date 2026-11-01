"""Legacy import path for the Hyped Ogre raid poster editor.

The public code, stored raid settings and permission checks are unchanged.
Both paths use one module object, so existing raid_signup imports, tests,
class identities and any patches continue to work during migration.
"""

import sys

from hyped_ogre.features.raids import poster as _canonical

sys.modules[__name__] = _canonical
