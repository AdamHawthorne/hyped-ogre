"""Legacy import path for WoW: Forever raid composition coverage.

The canonical, unchanged implementation now lives in
hyped_ogre.features.raids.buffs. Existing Google Sheets and raid code
continues importing cogs.forever_raid_buffs during the migration.
"""

from hyped_ogre.features.raids.buffs import (
    CLASS_COVERAGE,
    SPEC_COVERAGE,
    _selected,
    missing_entries,
    missing_coverage,
    instance_summaries,
)

__all__ = [
    "CLASS_COVERAGE", "SPEC_COVERAGE", "_selected",
    "missing_entries", "missing_coverage", "instance_summaries",
]
