"""Legacy import alias for the shared Forever item metadata cache.

The implementation lives in hyped_ogre.features.characters.items.

Unlike a basic `from ... import` wrapper, this alias deliberately exposes
the *same module object*. Existing consumers and tests patch the module's
private `_download`, `_FETCH_ATTEMPTED` and cache state. Exposing separate
module globals would silently change cache behaviour and mocking.
"""

import sys

from hyped_ogre.features.characters import items as _canonical_items

sys.modules[__name__] = _canonical_items
