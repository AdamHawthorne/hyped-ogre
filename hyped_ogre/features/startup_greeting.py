"""One short private Ogre greeting after a successful Discord READY event.

The guard is per bot process, so Fly.io deploy/restart announces again but
Discord gateway reconnects or repeated READY events do not spam leadership.
"""
import logging
import secrets

import discord

LOGGER = logging.getLogger(__name__)

# Hyped Ogre private control channel. Never post startup noise to public chats.
OGRE_CONTROL_CHANNEL_ID = 1564594901746716713

OGRE_WAKEUPS = (
    "Oi! Ogre back again. Who touch buttons while Ogre gone?",
    "Ogre wake up! Brain still loading. Club ready.",
    "Hrm. Ogre back on duty. Try not break things.",
    "Oi, Ogre return! Was only resting eyes.",
    "Ogre awake again. Who need orderin' about?",
    "Back again! Ogre hungry, but Ogre working.",
    "Ogre online! No funny business while Ogre gone, yes?",
    "Ogre return from nap. Everything still standing? Good.",
    "Oi! Big Ogre back. Little problems better run.",
    "Ogre here again. Someone bring snacks.",
)


class StartupGreeting:
    def __init__(self, guild_id, channel_id=OGRE_CONTROL_CHANNEL_ID):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self._attempted = False

    async def announce_once(self, bot):
        """Post at most once per running process, including reconnects.

        Claim before awaiting Discord so overlapping READY callbacks cannot
        double-post. A failed network request is logged, not replayed: the
        gateway may have received a send even if the response was lost.
        """
        if self._attempted or not self.guild_id:
            return False
        self._attempted = True
        try:
            channel = bot.get_channel(self.channel_id)
            if channel is None:
                channel = await bot.fetch_channel(self.channel_id)
            if (not isinstance(channel, discord.TextChannel) or
                    channel.guild.id != self.guild_id):
                LOGGER.warning("Ogre startup greeting destination is not a Hyped text channel")
                return False
            await channel.send(
                secrets.choice(OGRE_WAKEUPS),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            LOGGER.warning("Ogre startup greeting not sent: %s", exc)
            return False
        LOGGER.info("Ogre startup greeting posted privately")
        return True
