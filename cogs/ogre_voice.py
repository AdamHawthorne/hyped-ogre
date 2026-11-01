"""Backwards-compatible import path for Ogre voice functions.

Existing cogs import this module. The implementation now lives in the
independent hyped_ogre package; keep this shim until all consumers migrate.
"""

from hyped_ogre.features.voice.messages import PHRASES, count_label, pick

__all__ = ["PHRASES", "pick", "count_label"]
