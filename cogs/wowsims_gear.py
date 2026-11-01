"""Compatibility alias for Hyped Ogre's saved WoWSims equipment views.

The implementation resides in hyped_ogre.features.characters.equipment.

Keep a shared module object: existing raid sheets import cogs.wowsims_gear,
and the tests and integrations expect the same constants, helpers and
patchable functions. No startup, database, or Discord changes are needed.
"""

import sys

from hyped_ogre.features.characters import equipment as _canonical_equipment

sys.modules[__name__] = _canonical_equipment
