"""Legacy import path for WoW: Forever race and class reference data.

The canonical data now lives in hyped_ogre.features.characters.races.
Keep these exports stable for posted LFG and raid character interactions.
"""

from hyped_ogre.features.characters.races import (
    RACES,
    STARTING_ZONES,
    RACE_CLASSES,
)

__all__ = ["RACES", "STARTING_ZONES", "RACE_CLASSES"]
