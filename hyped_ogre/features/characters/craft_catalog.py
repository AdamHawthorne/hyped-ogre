"""Import official WoW Forever static recipe identifiers and names.

Static references describe possible crafts, NEVER learned ownership.
"""
import asyncio
import logging
import os

import aiohttp

from . import armory, crafting

LOG = logging.getLogger(__name__)
NAMESPACE = "static-forever-eu"
MAX_REQUESTS = 48


def _label(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("en_GB") or value.get("en_US") or value.get("name") or "")
    return ""


def extract_tier(tier, profession):
    """Walk nested Blizzard tier categories; deduplicate by recipe identifier."""
    output = {}
    def walk(value):
        if isinstance(value, list):
            for part in value:
                walk(part)
        elif isinstance(value, dict):
            if type(value.get("id")) is int and "name" in value and (
                    "recipe" in value.get("key", {}).get("href", "").lower()
                    if isinstance(value.get("key"), dict) else False):
                output[value["id"]] = {
                    "id": value["id"], "name": _label(value["name"]),
                    "profession": profession,
                }
            for key, v in value.items():
                if key == "recipes" and isinstance(v, list):
                    for recipe in v:
                        if isinstance(recipe, dict) and type(recipe.get("id")) is int:
                            output[recipe["id"]] = {
                                "id": recipe["id"], "name": _label(recipe.get("name")),
                                "profession": profession,
                            }
                elif key in ("categories", "subcategories", "children", "recipes"):
                    walk(v)
    walk(tier)
    return [v for v in output.values() if v["name"]]


async def refresh(path):
    """Daily bounded official catalog import; preserve cache on API failure."""
    if not crafting.catalog_due(path) or not armory.credentials_configured():
        return 0
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                armory.TOKEN_URL,
                auth=aiohttp.BasicAuth(os.getenv("BLIZZARD_CLIENT_ID"),
                                       os.getenv("BLIZZARD_CLIENT_SECRET")),
                data={"grant_type": "client_credentials"},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                token = (await response.json()).get("access_token")
            if not token:
                return 0
            headers = {"Authorization": "Bearer " + token}
            params = {"namespace": NAMESPACE, "locale": "en_GB"}

            async def get(url):
                async with session.get(
                    url, params=params, headers=headers, allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    return await response.json()

            index = await get(f"{armory.HOST}/data/wow/profession/index")
            professions = index.get("professions", [])
            recipes = []
            requests = 0
            for entry in professions:
                if requests >= MAX_REQUESTS:
                    break
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
                    continue
                ident = entry["id"]
                prof = _label(entry.get("name"))
                if not prof or prof.casefold() == "test profession [dnt]":
                    continue
                detail = await get(f"{armory.HOST}/data/wow/profession/{ident}")
                requests += 1
                for tier in detail.get("skill_tiers", []):
                    if requests >= MAX_REQUESTS:
                        break
                    if type(tier.get("id")) is not int:
                        continue
                    data = await get(
                        f"{armory.HOST}/data/wow/profession/{ident}/skill-tier/{tier['id']}"
                    )
                    recipes.extend(extract_tier(data, prof))
                    requests += 1
            if recipes:
                await asyncio.to_thread(crafting.save_catalog, path, recipes)
                # Only mark complete when every listed profession was covered.
                if requests < MAX_REQUESTS:
                    await asyncio.to_thread(crafting.mark_catalog, path)
            return len(recipes)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as exc:
        LOG.warning("Official Forever recipe index delayed: %s", type(exc).__name__)
        return 0
