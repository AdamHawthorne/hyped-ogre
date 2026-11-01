"""Bounded, refreshable Forever-only item metadata lookup.

Data source: Napalmsteak/WoW-Classic-Item-Caches (Forever Cache.json).
Forever and Classic Era reuse IDs with potentially different stats. Never
substitute Era information for unknown Forever items. Cached snapshots keep
the periodic Google Sheets synchronisation independent of website uptime.
"""
import json
import logging
import os
import re
import tempfile
import time
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)
SOURCE = ("https://raw.githubusercontent.com/Napalmsteak/"
          "WoW-Classic-Item-Caches/main/Forever%20Cache.json")
WOWHEAD_BASE = "https://www.wowhead.com/forever/item="
MAX_DOWNLOAD = 24 * 1024 * 1024
MAX_AGE_SECONDS = 7 * 86400
_MEMO = {}
_FETCH_ATTEMPTED = set()
_LEVEL_PATTERN = re.compile(
    r"(?:I\s*t\s*e\s*m\s*\s*L\s*e\s*v\s*e\s*l|Item\s*Level)"
    r"\s*[:\s]*(\d{1,3})", re.IGNORECASE
)


def item_level(html):
    if not isinstance(html, str):
        return None
    clean = re.sub(r"<[^>]+>", " ", unescape(html))
    clean = re.sub(r"\s+", " ", clean)
    match = _LEVEL_PATTERN.search(clean)
    if not match:
        return None
    level = int(match.group(1))
    return level if 1 <= level <= 250 else None


def _cache_path(db_path):
    return Path(db_path).with_suffix(".forever_items.json")


def _read_cached(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1:
            records = data.get("items")
            if isinstance(records, dict):
                return records
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _download():
    """Maximum one bounded fetch, never retry every player or every slot."""
    request = Request(SOURCE, headers={
        "User-Agent": "HypedGuildBot/1.0 (community raid item-level lookup)",
        "Accept": "application/json",
    })
    with urlopen(request, timeout=15) as response:
        chunks, size = [], 0
        while True:
            chunk = response.read(128 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise ValueError("Forever item catalog too large")
            chunks.append(chunk)
    entries = json.loads(b"".join(chunks))
    if not isinstance(entries, list):
        raise ValueError("Unexpected Forever item catalog format")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("itemId")
        if not isinstance(item_id, int) or not 0 < item_id < 1_000_000_000:
            continue
        lvl = item_level(entry.get("tooltipHtml"))
        name = entry.get("name") if isinstance(entry.get("name"), str) else ""
        # Item levels not provided by the source remain unknown, never zero.
        if lvl or name:
            result[str(item_id)] = {
                "level": lvl, "name": name[:100],
            }
    if len(result) < 1000:
        raise ValueError("Forever item catalog contains too few items")
    return result


def cached_catalog(db_path):
    """Read known metadata without performing network calls in roster rendering."""
    path = _cache_path(db_path)
    key = str(path)
    memo = _MEMO.get(key)
    if memo:
        return memo[1]
    records = _read_cached(path)
    _MEMO[key] = (time.time(), records)
    return records


def catalog(db_path):
    """Use local cache for 7 days. Fetch failure never blocks raid Sheets."""
    path = _cache_path(db_path)
    key = str(path)
    now = time.time()
    memo = _MEMO.get(key)
    if memo and now - memo[0] < 300 and (
            memo[1] or key in _FETCH_ATTEMPTED):
        return memo[1]
    old = _read_cached(path)
    try:
        fresh = now - path.stat().st_mtime < MAX_AGE_SECONDS
    except OSError:
        fresh = False
    if fresh and old:
        _MEMO[key] = (now, old)
        return old
    try:
        _FETCH_ATTEMPTED.add(key)
        new = _download()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=".forever_items_", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "items": new}, stream, separators=(",", ":"))
            os.replace(filename, path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
        _MEMO[key] = (now, new)
        return new
    except (OSError, ValueError, TimeoutError, TypeError) as exc:
        LOGGER.warning("Forever item-level catalog unavailable: %s", exc)
        # On failure do not hammer the source again during every minute sync.
        _MEMO[key] = (now, old)
        return old


def lookup(items, item_id):
    if not item_id or not isinstance(items, dict):
        return {}
    record = items.get(str(item_id))
    return record if isinstance(record, dict) else {}
