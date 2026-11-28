"""Bounded Blizzard EU guild-roster recipe census; never edits registrations.

Public guild roster -> per-character /professions known_recipes, keeping the
source IDs separate from Ogre's Discord character IDs. No profile requests
are sent outside the fixed Blizzard endpoint/namespace.
"""
import asyncio
import logging
import os
import re
import time

import aiohttp

from . import armory, crafting

LOG = logging.getLogger(__name__)
GUILD_SLUG = "hyped"
PROFILES_PER_CYCLE = 18
PROFESSION_REFRESH = 6 * 3600


def parse_members(payload):
    """Ignore unusable API entries; do not confuse rank with profession skill."""
    if not isinstance(payload, dict) or not isinstance(payload.get("members"), list):
        return None
    result = []
    seen = set()
    for entry in payload["members"]:
        if not isinstance(entry, dict):
            continue
        char = entry.get("character")
        if not isinstance(char, dict):
            continue
        ident = char.get("id")
        name = char.get("name")
        surname = char.get("surname")
        fullname = char.get("name_search")
        if not fullname and isinstance(name, str) and isinstance(surname, str):
            fullname = f"{name} {surname}"
        if (type(ident) is not int or ident <= 0 or ident in seen
                or not isinstance(fullname, str)
                or armory.slug(fullname) is None):
            continue
        slug = armory.slug(fullname)
        if slug != str(char.get("slug") or slug).lower():
            # A disagreement means we cannot safely address this profile.
            continue
        seen.add(ident)
        result.append({
            "id": ident, "name": fullname, "slug": slug,
            "level": char.get("level"), "rank": entry.get("rank"),
        })
    if not result or len(result) * 10 < len(payload["members"]) * 8:
        return None
    return result


async def refresh(path, *, now=None, max_profiles=PROFILES_PER_CYCLE,
                  force_roster=False, force_profiles=False):
    """Sync guild roster hourly and fetch up to 18 profession snapshots/cycle."""
    result = {"members": 0, "attempted": 0, "verified": 0,
              "not_found": 0, "errors": 0, "rate_limited": False,
              "roster_updated": False}
    if not armory.credentials_configured():
        return result
    stamp = int(time.time() if now is None else now)
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
            if not isinstance(token, str) or not token:
                return result
            headers = {"Authorization": "Bearer " + token}
            params = {"namespace": armory.NAMESPACE, "locale": "en_GB"}
            guild_slug = str(os.getenv("BLIZZARD_FOREVER_GUILD_SLUG") or GUILD_SLUG).lower()
            if not re.fullmatch(r"[a-z0-9-]{2,80}", guild_slug):
                LOG.warning("Invalid configured Blizzard guild slug.")
                return result
            base = f"{armory.HOST}/data/wow/guild/{guild_slug}"
            if force_roster or await asyncio.to_thread(
                crafting.guild_roster_due, path, stamp
            ):
                async with session.get(
                    base, params=params, headers=headers,
                    allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    detail = await response.json()
                if (not isinstance(detail, dict)
                        or str(detail.get("name") or "").casefold() != "hyped"
                        or str(detail.get("slug") or guild_slug) != guild_slug
                        or (isinstance(detail.get("faction"), dict)
                            and detail["faction"].get("type") not in (None, "HORDE"))
                        or (isinstance(detail.get("ruleset"), dict)
                            and detail["ruleset"].get("id") not in (None, 1))):
                    LOG.warning("Blizzard guild details failed the Hyped identity check.")
                    return result
                async with session.get(
                    base + "/roster", params=params, headers=headers,
                    allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    members = parse_members(await response.json())
                if members is None:
                    LOG.warning("Blizzard guild roster empty or malformed; keeping cache.")
                    return result
                result["members"] = await asyncio.to_thread(
                    crafting.guild_roster, path, members, observed_at=stamp
                )
                result["roster_updated"] = True
            else:
                result["members"] = (await asyncio.to_thread(
                    crafting.guild_statistics, path
                ))["members"]

            eligible = await asyncio.to_thread(
                crafting.guild_candidates, path, now=stamp,
                limit=max_profiles, interval=0 if force_profiles else PROFESSION_REFRESH,
            )
            for index, member in enumerate(eligible):
                if index:
                    await asyncio.sleep(0.2)
                result["attempted"] += 1
                url = (f"{armory.HOST}/profile/wow/character/"
                       f"{member['slug']}/professions")
                try:
                    async with session.get(
                        url, params=params, headers=headers,
                        allow_redirects=False,
                    ) as response:
                        if response.status == 429:
                            result["rate_limited"] = True
                            break
                        if response.status in (403, 404):
                            result["not_found"] += 1
                            await asyncio.to_thread(
                                crafting.guild_profile_checked, path,
                                member["blizzard_id"], observed_at=stamp,
                            )
                            continue
                        response.raise_for_status()
                        payload = await response.json()
                    if await asyncio.to_thread(
                        crafting.save_profile, path,
                        -member["blizzard_id"], payload,
                        observed_at=stamp,
                    ):
                        result["verified"] += 1
                    await asyncio.to_thread(
                        crafting.guild_profile_checked, path,
                        member["blizzard_id"], observed_at=stamp,
                    )
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    LOG.warning("Guild professions temporarily unavailable: %s",
                                type(exc).__name__)
                    result["errors"] += 1
                    # Do not mark this profile checked; retry next cycle.
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        LOG.warning("Guild recipe census delayed: %s", type(exc).__name__)
        result["errors"] += 1
    return result
