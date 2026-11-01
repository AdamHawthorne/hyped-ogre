"""Legacy import alias for the private, per-raid Google Sheets dashboard.

The authoritative implementation now lives in
hyped_ogre.features.raids.sheets_dashboard.

This *same-module* alias preserves existing raid_signup imports, shared
mutable formatting caches, monkeypatch targets, selection/visibility
handling and the current Google Sheets/SQLite contracts.
"""

import sys

from hyped_ogre.features.raids import sheets_dashboard as _canonical

sys.modules[__name__] = _canonical
