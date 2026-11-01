"""Stable Discord extension entrypoint for cogs/raid_signup.py.

The implementation lives inside the Hyped Ogre feature package. It is
executed into this original `cogs.*` module to preserve class identity,
app commands, persistent views, monkeypatches and extension unloading.
No Discord handlers or setup tasks are registered by the shim itself.
"""

from hyped_ogre.core.legacy_source import load_legacy_extension

load_legacy_extension(__name__)
