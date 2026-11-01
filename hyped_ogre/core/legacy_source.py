"""Relocate trusted Discord extension source without changing its module identity.

discord.py tracks Cog ownership and unregisters commands/listeners by the
extension's module name. Several cogs also import one another and existing
unit tests/users monkeypatch the original `cogs.*` module-level functions.

A normal `from hyped_ogre.features... import Cog` would change its
`__module__`, breaking clean extension unload/reload and possibly leaving
tasks or slash commands registered after a reload. A same-module alias is
also incompatible with the extension loader's import/spec ownership.

Instead, explicitly execute an allow-listed source file into the existing
Python module object using the standard-library SourceFileLoader. The code,
globals, `__name__`, `__package__`, module-level defaults, Cog classes and
`async setup(bot)` all retain their original `cogs.*` identity. The
original code itself remains unchanged, now physically grouped by feature.

Only checked-in, static package paths in this map can ever be executed.
Never use this loader for user-provided scripts or plugin input.
"""

from importlib.machinery import SourceFileLoader
from pathlib import Path
import sys


_SOURCE_FILES = {
    "cogs.countdown": "features/countdowns/extension_source.py",
    "cogs.lfg_preview": "features/lfg/extension_source.py",
    "cogs.raid_signup": "features/raids/signup_extension_source.py",
    "cogs.streams": "features/streams/extension_source.py",
    "cogs.suggestions": "features/suggestions/extension_source.py",
}


def load_legacy_extension(module_name):
    """Load original checked-in feature source under its live Discord name."""
    relative = _SOURCE_FILES.get(module_name)
    if relative is None:
        raise ValueError(f"Unrecognised Hyped Ogre extension: {module_name}")
    module = sys.modules.get(module_name)
    if module is None or module.__name__ != module_name:
        raise RuntimeError(f"Extension module is not initialised: {module_name}")
    path = Path(__file__).resolve().parents[1] / relative
    loader = SourceFileLoader(module_name, str(path))
    loader.exec_module(module)
