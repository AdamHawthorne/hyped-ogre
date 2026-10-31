"""Public raid acknowledgement messages: opt-in pings, durable ✅ reactions."""
import asyncio
import logging

import discord

from cogs import raid_acknowledgements as store, raid_sheets_v3
from cogs.raid_schedule import read_raid

LOGGER = logging.getLogger(__name__)


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
    selected = [(str(row[0]), str(row[1])) for row in selected
                if str(row[0]).isdigit() and int(row[0]) > 0]
    if not selected:
        raise ValueError("No selected Discord members to ping. PUGs cannot be tagged.")
    panel_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{panel_id}"
    content = store.render(raid_id, selected, len(selected), panel_url)
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        raise ValueError("Ogre can't access the raid panel channel.")
    message = await channel.send(
        content=content,
        allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=True),
    )
    try:
        store.record_announcement(path, raid_id, channel_id, message.id, selected)
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
    entries = store.members(path, raid_id)
    waiting = [(member_id, name) for member_id, name, acked in entries
               if acked is None]
    event = read_raid(path, raid_id)
    panel_url = (
        f"https://discord.com/channels/{guild_id}/{event['panel_channel_id']}/{event['panel_message_id']}"
        if event and event.get("panel_channel_id") and event.get("panel_message_id")
        else None
    )
    content = store.render(raid_id, waiting, len(entries), panel_url)
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    message = await channel.fetch_message(message_id)
    if message.content != content:
        await message.edit(content=content,
                           allowed_mentions=discord.AllowedMentions.none())
    store.mark_refreshed(path, raid_id)


async def reaction(bot, path, payload, guild_id):
    if (payload.guild_id != guild_id or
            str(payload.emoji) != store.ACK_EMOJI or
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
