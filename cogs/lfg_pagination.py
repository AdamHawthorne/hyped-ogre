"""Backward-compatible LFG pagination import path.

The implementation lives in hyped_ogre.features.lfg.pagination. Existing
LFG cogs and tests can continue importing cogs.lfg_pagination unchanged.
"""

from hyped_ogre.features.lfg.pagination import (
    PAGE_SIZE,
    page_count,
    clamp_page,
    page_rows,
    BOARD_CHUNK_SIZE,
    BOARD_TEXT_BUDGET,
    board_chunks,
)

__all__ = [
    "PAGE_SIZE", "page_count", "clamp_page", "page_rows",
    "BOARD_CHUNK_SIZE", "BOARD_TEXT_BUDGET", "board_chunks",
]
