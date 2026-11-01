# -----------------------------------------
# Hyped Ogre – legacy executable entrypoint
# Derived from RollF by hegernat/plutonium87.
# Original source: https://github.com/hegernat/rollf-bot
# Copyright (c) 2026 hegernat/plutonium87
# Modifications Copyright (c) 2026 AdamHawthorne
# Licensed under the MIT License. See LICENSE.
# -----------------------------------------
"""Preserve the existing `python rollf.py` Docker/Fly boot command.

The entire original bot coordinator (Discord commands, roll history and
coin reconciliation, startup tasks, Flameboard, webhooks) now lives in
hyped_ogre.bot. Running this script gives the implementation the original
__main__ module namespace and leaves sys.argv[0] and the cwd unchanged.

Do not import the real bot module from tests: it starts Discord at import.
"""

import runpy


if __name__ == "__main__":
    runpy.run_module("hyped_ogre.bot", run_name="__main__", alter_sys=False)
