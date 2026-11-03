"""Private Leadership pings for committed absence and signup-note changes."""

import asyncio
import logging
import os
import sqlite3

import discord
from discord.ext import commands, tasks

from . import activity
from hyped_ogre.features.classrooms.config import BOT_ROLE_ID, GUILD_ID, LEADERSHIP_ROLE_ID

LOG = logging.getLogger(__name__)
CHANNEL_ID = 1564086170315395164


class RaidNotifications(commands.Cog):
    def __init__(self, bot):
        self.bot, self.lock = bot, asyncio.Lock()

    async def cog_load(self):
        if int(os.getenv("HYPED_GUILD_ID") or 0) == GUILD_ID:
            self.maintenance.start()

    async def cog_unload(self):
        task = self.maintenance.get_task()
        self.maintenance.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def read_pending():
        from cogs import raid_signup as raid
        with raid.database(raid.RAID_DB) as con:
            return activity.pending(con)

    @staticmethod
    def record(event_id, message_id):
        from cogs import raid_signup as raid
        with raid.database(raid.RAID_DB) as con:
            activity.delivered(con, event_id, message_id)

    async def flush(self):
        from cogs import raid_signup as raid
        async with self.lock:
            events = await asyncio.to_thread(self.read_pending)
            if not events:
                return 0
            channel = self.bot.get_channel(CHANNEL_ID) or await self.bot.fetch_channel(CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
                raise ValueError("Leadership notification channel is not in Hyped.")
            guild = channel.guild
            leader = guild.get_role(LEADERSHIP_ROLE_ID)
            if (leader is None or channel.permissions_for(guild.default_role).view_channel
                    or not channel.permissions_for(leader).view_channel):
                raise ValueError("Leadership notifications require the private Leadership channel.")
            if guild.me is None:
                raise ValueError("Ogre's guild member is unavailable.")
            trusted = {LEADERSHIP_ROLE_ID, BOT_ROLE_ID}
            if any(isinstance(role, discord.Role) and ow.view_channel is True
                   and role.id not in trusted and not role.permissions.administrator
                   for role, ow in channel.overwrites.items()):
                raise ValueError("Leadership channel has a general-member View Channel allow.")
            permissions = channel.permissions_for(guild.me)
            if not all(getattr(permissions, p) for p in (
                    "view_channel", "send_messages", "read_message_history", "embed_links")):
                raise ValueError("Leadership alerts need View, Send, History and Embed Links.")
            if not leader.mentionable and not permissions.mention_everyone:
                raise ValueError("Ogre cannot ping the Leadership role in this channel.")
            recent = {}
            async for message in channel.history(limit=100):
                if message.author.id == self.bot.user.id:
                    for embed in message.embeds:
                        if embed.footer.text:
                            recent[embed.footer.text] = message.id
            count = 0
            for event in events:
                marker = f"Raid activity #{event['event_id']}"
                if marker in recent:
                    await asyncio.to_thread(self.record, event["event_id"], recent[marker])
                    continue
                raid_event = await asyncio.to_thread(raid.read_raid, raid.RAID_DB, event["raid_id"])
                if raid_event is None:
                    # A deleted raid should not emit a stale leadership ping.
                    await asyncio.to_thread(self.record, event["event_id"], 0)
                    continue
                name = {"absent": "marked absent", "available": "returned to attending"}.get(
                    event["kind"], "updated their sign-up note")
                note = discord.utils.escape_mentions(discord.utils.escape_markdown(event["note"]))
                text = f"<@{event['discord_id']}> {name}.\n**Note:** {note or 'No note'}"
                if raid_event.get("panel_channel_id") and raid_event.get("panel_message_id"):
                    text += (f"\n[Open raid](https://discord.com/channels/{GUILD_ID}/"
                             f"{raid_event['panel_channel_id']}/{raid_event['panel_message_id']})")
                embed = discord.Embed(title=f"Raid #{event['raid_id']} · Attendance update",
                                      description=text, colour=0xB84A3A)
                embed.set_footer(text=marker)
                message = await channel.send(
                    content=f"<@&{LEADERSHIP_ROLE_ID}> Ogre has a raid update.", embed=embed,
                    nonce=f"raidactivity{event['event_id']}",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=False, roles=[leader], replied_user=False))
                await asyncio.to_thread(self.record, event["event_id"], message.id)
                recent[marker] = message.id
                count += 1
            return count

    @commands.Cog.listener()
    async def on_raid_activity_changed(self, guild_id):
        if guild_id == GUILD_ID:
            try:
                await self.flush()
            except (ValueError, sqlite3.Error, discord.HTTPException):
                LOG.warning("Leadership raid update delayed; saved event will retry.")

    @tasks.loop(seconds=30)
    async def maintenance(self):
        try:
            await self.flush()
        except (ValueError, sqlite3.Error, discord.HTTPException):
            LOG.warning("Leadership raid notifications delayed; saved events will retry.")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(RaidNotifications(bot))
