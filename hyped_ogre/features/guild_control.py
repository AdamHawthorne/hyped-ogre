"""Authenticated, limited controls for the private Hyped companion app."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any

LOG = logging.getLogger(__name__)
MAX_BODY_BYTES = 16 * 1024
MAX_CLOCK_SKEW = 300
REQUEST_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
ANNOUNCEMENT_OPTIONS = {"1", "2", "3", "7", "0", "none"}
ALLOWED_FIELDS = {
    "day", "time", "end_time", "instance_1", "instance_2", "instance_3",
    "announce", "custom_title", "wowsims_policy", "default_show_discord", "ping_role_ids",
}


class ControlPayloadError(ValueError):
    """A malformed or unsupported desktop control request."""


def valid_signature(secret: str, timestamp: str, signature: str,
                    body: bytes, now: int) -> bool:
    if not timestamp.isdecimal() or not signature:
        return False
    try:
        stamp = int(timestamp)
    except ValueError:
        return False
    if abs(now - stamp) > MAX_CLOCK_SKEW:
        return False
    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def validate_payload(payload: Any, allowed_instances: set[str] | None = None) -> dict[str, Any]:
    """Validate and reduce a request to the small prefill surface we support."""
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("action") != "raid_draft":
        raise ControlPayloadError("unsupported_request")
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ControlPayloadError("invalid_request_id")
    raw = payload.get("draft")
    if not isinstance(raw, dict) or set(raw) - ALLOWED_FIELDS:
        raise ControlPayloadError("invalid_draft_fields")

    try:
        raid_day = date.fromisoformat(str(raw.get("day", "")))
        start_time = datetime.strptime(str(raw.get("time", "")), "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise ControlPayloadError("invalid_raid_datetime") from exc

    options = allowed_instances or {"Hyjal (20m)", "Barrows (10m)", "Ony (40m)"}
    clean_draft: dict[str, Any] = {
        "day": raid_day.isoformat(),
        "time": start_time,
        "instance_1": raw.get("instance_1"),
        "instance_2": raw.get("instance_2") or None,
        "instance_3": raw.get("instance_3") or None,
        "announce": str(raw.get("announce", "1")),
        "end_time": raw.get("end_time") or None,
        "custom_title": raw.get("custom_title", ""),
    }
    for key in ("instance_1", "instance_2", "instance_3"):
        if clean_draft[key] is None and key == "instance_1":
            raise ControlPayloadError("first_instance_required")
        if clean_draft[key] is not None and clean_draft[key] not in options:
            raise ControlPayloadError("unsupported_instance")
    if clean_draft["announce"] not in ANNOUNCEMENT_OPTIONS:
        raise ControlPayloadError("unsupported_announcement")
    if clean_draft["end_time"] is not None:
        try:
            clean_draft["end_time"] = datetime.strptime(
                str(clean_draft["end_time"]), "%H:%M"
            ).strftime("%H:%M")
        except ValueError as exc:
            raise ControlPayloadError("invalid_end_time") from exc
    title = clean_draft["custom_title"]
    if not isinstance(title, str) or len(title) > 70 or any(ord(char) < 32 for char in title):
        raise ControlPayloadError("invalid_title")
    clean_draft["custom_title"] = " ".join(title.split())
    # These values are deliberately fixed. The desktop cannot ask for pings,
    # change roster policy, redirect public posts, or skip the bot confirmation.
    clean_draft["wowsims_policy"] = "missing"
    clean_draft["default_show_discord"] = True
    clean_draft["ping_role_ids"] = []
    return {"requestId": request_id.lower(), "draft": clean_draft}


def register_route(app: Any, bot: Any) -> None:
    """Install the route only when HYPED_CONTROL_ENABLED is explicitly true."""
    from aiohttp import web

    secret = os.getenv("HYPED_CONTROL_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("HYPED_CONTROL_ENABLED requires HYPED_CONTROL_SECRET with at least 32 characters")

    handled: dict[str, tuple[str, dict[str, Any]]] = {}

    async def raid_draft(request: Any):
        now_value = int(time.time())
        body = await request.read()
        timestamp = request.headers.get("X-Hyped-Timestamp", "")
        signature = request.headers.get("X-Hyped-Signature", "")
        if len(body) > MAX_BODY_BYTES or not valid_signature(secret, timestamp, signature, body, now_value):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = validate_payload(json.loads(body.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "invalid_json"}, status=400)
        except ControlPayloadError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        request_id = payload["requestId"]
        body_hash = hashlib.sha256(body).hexdigest()
        previous = handled.get(request_id)
        if previous:
            old_hash, old_response = previous
            if old_hash != body_hash:
                return web.json_response({"error": "request_id_conflict"}, status=409)
            return web.json_response({**old_response, "duplicate": True})

        # Reserve before awaiting Discord so concurrent retries cannot post
        # a second copy of the same draft.
        handled[request_id] = (body_hash, {
            "ok": True,
            "message": "Raid draft is being delivered to Ogre's private controls.",
        })

        try:
            cog = bot.get_cog("RaidPrototype")
            if cog is None:
                handled.pop(request_id, None)
                return web.json_response({"error": "raid_controls_unavailable"}, status=503)
            import cogs.raid_signup as raid_signup
            import cogs.raid_poster as poster

            guild = bot.get_guild(raid_signup.GUILD_ID)
            channel = guild.get_channel(raid_signup.TEST_CHANNEL_ID) if guild else None
            owner_id = int(raid_signup.OWNER_ID)
            if not channel or not owner_id:
                handled.pop(request_id, None)
                return web.json_response({"error": "private_control_channel_unavailable"}, status=503)

            draft = poster.draft_from_event(None, poster.list_raids(cog.raid_db_path()))
            draft.update(payload["draft"])
            poster.poster_schedule(draft)
            poster.poster_end_at(draft)
            from discord import AllowedMentions
            message = await channel.send(
                content="Raid draft prepared by Hyped Guild Companion. Review it, then use Ogre’s normal post confirmation.",
                view=poster.RaidPosterView(cog, owner_id, draft),
                allowed_mentions=AllowedMentions.none(),
            )
            response = {
                "ok": True,
                "messageUrl": getattr(message, "jump_url", ""),
                "message": "Draft sent to Ogre’s private raid controls. Public posting still needs confirmation.",
            }
            handled[request_id] = (body_hash, response)
            if len(handled) > 500:
                handled.pop(next(iter(handled)))
            LOG.info("Companion raid draft sent to private controls (request=%s)", request_id)
            return web.json_response(response)
        except (ValueError, OSError, RuntimeError) as exc:
            handled.pop(request_id, None)
            LOG.warning("Companion raid draft rejected (%s)", type(exc).__name__)
            return web.json_response({"error": str(exc)[:240]}, status=400)
        except Exception as exc:
            handled.pop(request_id, None)
            LOG.warning("Companion raid draft delivery failed (%s)", type(exc).__name__)
            return web.json_response({"error": "raid_draft_delivery_failed"}, status=503)

    app.router.add_post("/hyped/raid-draft", raid_draft)
