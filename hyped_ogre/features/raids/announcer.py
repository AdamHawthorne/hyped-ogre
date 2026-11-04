"""Public raid acknowledgement messages: opt-in pings, durable ✅ reactions."""
import asyncio
import logging
import os

import discord

from cogs import raid_acknowledgements as store, raid_sheets_v3
from cogs.raid_schedule import (
    RAID_PANEL_ACCENT, RAID_PANEL_IMAGE, read_raid, read_public_roster,
    raid_panel_when, roster_groups,
)
from hyped_ogre.features.raids.leaders import effective_leaders, group_label
from hyped_ogre.features.classrooms.config import GUILD_ID

LOGGER = logging.getLogger(__name__)

ROLES = ("Tanks", "Healers", "Melee", "Ranged")


def _member_chunks(names):
    """No role field exceeds 1,024 characters or hides a raider."""
    chunks, current = [], []
    for name in names:
        if len(name) > 950:
            raise ValueError("A raid roster entry is too long for Discord.")
        if current and (len(current) >= 10 or
                        len("\n".join((*current, name))) > 950):
            chunks.append(current)
            current = []
        current.append(name)
    if current:
        chunks.append(current)
    return chunks or [[]]


def roster_embeds(event, selected, *, path=None, acknowledged_ids=(),
                  creator_name=None):
    """One standard V1 embed for all normal raid instances and both splits.

    Exactly six embed fields per group: full-width group/leader header, two
    inline Tank/Healer cells, a full-width spacer, two inline Melee/Ranged
    cells. Four nonempty groups therefore fit in 24 of Discord's 25 fields.
    Create a continuation embed only if the full roster exceeds Discord limits.
    The V2 sign-up post is never modified by this renderer.
    """
    entries = [
        (str(row[1]), str(row[2]), str(row[3]), str(row[4]),
         str(row[5]), str(row[0]))
        for row in selected if len(row) >= 6 and str(row[1]).strip()
    ]
    if not entries:
        return []
    leader_ids = effective_leaders(path, event, selected) if path else {}
    checked = {str(value) for value in acknowledged_ids}
    result = []
    panel_url = (
        f"https://discord.com/channels/{GUILD_ID}/"
        f"{event['panel_channel_id']}/{event['panel_message_id']}"
        if event.get("panel_channel_id") and event.get("panel_message_id") else None
    )
    main = discord.Embed(
        title=f"<Hyped> Raid #{event['raid_id']} | Confirmed roster",
        url=panel_url,
        colour=RAID_PANEL_ACCENT,
    )
    creator_id = str(event.get("created_by") or os.getenv("OWNER_ID") or "")
    if creator_id.isdigit() and int(creator_id) > 0:
        main.set_author(
            name=f"Created by {creator_name or 'Snore'}",
            url=f"https://discord.com/users/{creator_id}",
            icon_url=RAID_PANEL_IMAGE,
        )
    else:
        main.set_author(name="Hyped raid team", icon_url=RAID_PANEL_IMAGE)
    detail = raid_panel_when(event["raid_at"], event.get("end_at"))
    if event.get("softres_url"):
        detail += f"\n[SoftRes sign-up]({event['softres_url']})"
    if event.get("wcl_url"):
        detail += f"\n[Warcraft Logs]({event['wcl_url']})"
    main.description = detail
    current = main

    for group, players in roster_groups(
        event, entries, by_role="four", acknowledged_ids=checked
    ):
        if not players:
            continue
        members = {role: [] for role in ROLES}
        for role, name in players:
            members[role].append(name)
        chunks = {role: _member_chunks(members[role]) for role in ROLES}
        pages = max(map(len, chunks.values()))
        key = group_label(group)
        if key == "Hyjal":
            title = f"Hyjal ({len(players)}) 🔹🔸"
        elif key.startswith("Barrows"):
            title = f"Barrows ({len(players)}) {key[-1]}"
        elif key == "Ony":
            title = f"Ony ({len(players)})"
        else:
            title = f"{group} ({len(players)})"
        leader = leader_ids.get(key)
        leader_text = f"Leader: <@{leader}>" if leader else "Leader: Unassigned"

        for page in range(pages):
            fields = [(title if page == 0 else f"{title} · continued",
                       leader_text if page == 0 else "—", False)]
            for role in ("Tanks", "Healers"):
                names = chunks[role][page] if page < len(chunks[role]) else []
                fields.append((
                    f"{role} ({len(members[role])})" if page == 0 else role,
                    "\n".join(names) or "—", True,
                ))
            fields.append(("\u200b", "\u200b", False))
            for role in ("Melee", "Ranged"):
                names = chunks[role][page] if page < len(chunks[role]) else []
                fields.append((
                    f"{role} ({len(members[role])})" if page == 0 else role,
                    "\n".join(names) or "—", True,
                ))
            addition = sum(len(n) + len(v) for n, v, _ in fields)
            if (len(current.fields) + len(fields) > 25 or
                    len(current) + addition > 5850):
                result.append(current)
                current = discord.Embed(
                    title=f"<Hyped> Raid #{event['raid_id']} | Roster continued",
                    colour=RAID_PANEL_ACCENT,
                )
            for name, value, inline in fields:
                current.add_field(name=name, value=value, inline=inline)

    if current.fields:
        result.append(current)
    if len(result) > 10 or sum(len(embed) for embed in result) > 5900:
        raise ValueError("Confirmed raid roster exceeds Discord's embed limits.")
    return result

async def announce(bot, path, raid_id, guild_id, selected=None):
    """One announcement per raid. Never reping on a panel edit or a retry."""
    event = read_raid(path, raid_id)
    if not event or not event.get("roster_published") or event.get("archived_at"):
        raise ValueError("Publish an active roster before announcing.")
    if store.announcement(path, raid_id):
        raise ValueError("Ogre already announced this raid. No extra pings sent.")
    channel_id, panel_id = event.get("panel_channel_id"), event.get("panel_message_id")
    if not channel_id or not panel_id:
        raise ValueError("Ogre needs a posted raid panel to announce.")
    if selected is None:
        selected = await asyncio.to_thread(raid_sheets_v3.read_selected, path, raid_id)
    # Everyone on the selected roster appears in the V1 grid, including
    # PUGs with no Discord account. Only saved Discord IDs receive pings.
    selected = [tuple(row) for row in selected if len(row) >= 6 and str(row[1]).strip()]
    pinged = [(str(row[0]), str(row[1])) for row in selected
              if str(row[0]).isdigit() and int(row[0]) > 0]
    if not pinged:
        raise ValueError("No selected Discord members to ping. PUGs cannot be tagged.")
    panel_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{panel_id}"
    content = store.render(raid_id, pinged, len(pinged), panel_url)
    owner_id = str(event.get("created_by") or os.getenv("OWNER_ID") or "")
    owner = (bot.get_user(int(owner_id)) if owner_id.isdigit()
             and hasattr(bot, "get_user") else None)
    name = getattr(owner, "display_name", None) or getattr(owner, "name", None)
    embeds = roster_embeds(event, selected, path=path, creator_name=name)
    if not embeds:
        raise ValueError("Selected roster lacks class and raid assignment details.")
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        raise ValueError("Ogre can't access the raid panel channel.")
    me = channel.guild.me
    if me is None:
        raise ValueError("Ogre couldn't verify channel permissions.")
    permissions = channel.permissions_for(me)
    if not (permissions.view_channel and permissions.send_messages
            and permissions.add_reactions and permissions.read_message_history):
        raise ValueError("Ogre needs View Channel, Send Messages, Add Reactions "
                         "and Read Message History in the raid channel.")
    message = await channel.send(
        content=content, embeds=embeds,
        allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=True),
    )
    try:
        store.record_announcement(path, raid_id, channel_id, message.id, pinged)
    except Exception:
        try:
            await message.delete()
        except discord.HTTPException:
            LOGGER.warning("Untracked raid announcement could not be removed.")
        raise
    try:
        await message.add_reaction(store.ACK_EMOJI)
    except discord.HTTPException:
        LOGGER.warning("Ogre could not pre-add the ✅ reaction for Raid #%s", raid_id)
    return message.jump_url


async def refresh(bot, path, raid_id, guild_id):
    """Remove acknowledged mentions from the existing message, without pinging."""
    location = store.announcement(path, raid_id)
    if not location:
        return
    channel_id, message_id = location
    event = read_raid(path, raid_id)
    withdrawn = store.is_retracted(path, raid_id)
    if event and (event.get("archived_at") or withdrawn):
        # Delete only Ogre's separate V1 confirmation + reaction message.
        # The original V2 sign-up panel is a separate, protected message ID.
        # Completed raids retain acknowledgement history; withdrawn ones
        # clear it only after deletion so selected members can be pinged anew.
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
            raise ValueError("Saved acknowledgement channel is outside this guild.")
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            # Someone already deleted the announcement. Mark cleanup complete.
            pass
        else:
            if bot.user is None or message.author.id != bot.user.id:
                raise ValueError("Saved acknowledgement is not authored by Ogre.")
            await message.delete()
        if withdrawn and not event.get("archived_at"):
            store.clear_withdrawn(path, raid_id)
        else:
            store.mark_deleted(path, raid_id)
        return
    entries = store.members(path, raid_id)
    waiting = [(member_id, name) for member_id, name, acked in entries
               if acked is None]
    panel_url = (
        f"https://discord.com/channels/{guild_id}/{event['panel_channel_id']}/{event['panel_message_id']}"
        if event and event.get("panel_channel_id") and event.get("panel_message_id")
        else None
    )
    content = store.render(raid_id, waiting, len(entries), panel_url)
    embeds = roster_embeds(
        event, [
            (member_id, name, assignment, klass, role, spec)
            for name, assignment, klass, role, spec, member_id
            in read_public_roster(path, raid_id, include_identity=True)
        ],
        path=path,
        acknowledged_ids={member_id for member_id, _, acked in entries
                          if acked is not None},
    )
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    message = await channel.fetch_message(message_id)
    # Edit without notifying members again. The V1 group grid stays current
    # when reaction acknowledgements refresh the public message.
    if message.content != content or (
        embeds and [e.to_dict() for e in message.embeds] !=
        [e.to_dict() for e in embeds]
    ):
        await message.edit(
            content=content, embeds=embeds,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    # Keep the Sheet tick in the same retry boundary as the public message.
    # A configured but temporarily unavailable Sheet leaves needs_refresh set.
    await asyncio.to_thread(raid_sheets_v3.sync_acknowledgements, path, raid_id)
    store.mark_refreshed(path, raid_id)


async def reaction(bot, path, payload, guild_id):
    if (payload.guild_id != guild_id or
            str(payload.emoji) not in (store.ACK_EMOJI, "✅") or
            (bot.user and payload.user_id == bot.user.id)):
        return False
    raid_id = store.acknowledge(
        path, payload.channel_id, payload.message_id, payload.user_id)
    if raid_id is None:
        return False
    try:
        await refresh(bot, path, raid_id, guild_id)
    except (discord.HTTPException, ValueError) as exc:
        # The SQL record is durable; the background sync will retry.
        LOGGER.warning("Raid #%s acknowledgement refresh delayed: %s", raid_id, exc)
    return True


async def retry_dirty(bot, path, guild_id):
    for raid_id in store.dirty_announcements(path):
        try:
            await refresh(bot, path, raid_id, guild_id)
        except (discord.HTTPException, ValueError) as exc:
            LOGGER.warning("Raid #%s acknowledgement retry delayed: %s", raid_id, exc)
