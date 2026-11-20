"""Read-only owner command for Discord channel message statistics."""

from collections import Counter
import os

import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = int(os.getenv("HYPED_GUILD_ID", "451518969417957376"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
IN_GAME_CHAT_ID = 1567173054113652807


def summarise_messages(counts: Counter, total: int, bot_messages: int) -> tuple[int, int, list[tuple[int, int]]]:
    """Return unique contributors, human messages, and top five authors."""
    return len(counts), total - bot_messages, counts.most_common(5)


class ChannelStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="channelstats",
        description="Privately count messages and contributors in a Hyped text channel",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel to inspect (defaults to #in-game-chat)")
    async def channelstats(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        # An explicit runtime check is essential: default_permissions is only a hint.
        if not OWNER_ID or interaction.user.id != OWNER_ID or interaction.guild_id != GUILD_ID:
            await interaction.response.send_message(
                "Only Ogre's owner can inspect channel statistics.", ephemeral=True
            )
            return

        target = channel or interaction.guild.get_channel(IN_GAME_CHAT_ID)
        if not isinstance(target, discord.TextChannel) or target.guild.id != GUILD_ID:
            await interaction.response.send_message(
                "Ogre cannot find #in-game-chat. Choose a text channel instead.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        permissions = target.permissions_for(me) if me else None
        if not permissions or not (permissions.view_channel and permissions.read_message_history):
            await interaction.response.send_message(
                "Ogre needs View Channel and Read Message History permissions there.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        counts: Counter[int] = Counter()
        names: dict[int, str] = {}
        total = 0
        bot_messages = 0
        try:
            # Discord history is paginated and includes old messages; nothing is stored.
            async for message in target.history(limit=None, oldest_first=False):
                total += 1
                counts[message.author.id] += 1
                names[message.author.id] = discord.utils.escape_markdown(
                    message.author.display_name
                )
                if message.author.bot:
                    bot_messages += 1
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Ogre couldn't finish reading this channel's history. "
                "No partial count is shown.",
                ephemeral=True,
            )
            return

        contributors, human_messages, leaders = summarise_messages(
            counts, total, bot_messages
        )
        leaderboard = "\n".join(
            f"{index}. {names[user_id]}: **{count:,}**"
            for index, (user_id, count) in enumerate(leaders, start=1)
        ) or "No messages found."

        embed = discord.Embed(
            title=f"Channel statistics: #{target.name}",
            colour=0x8C5A3C,
            description=(
                f"**Total messages:** {total:,}\n"
                f"**Member messages:** {human_messages:,}\n"
                f"**Bot messages:** {bot_messages:,}\n"
                f"**Unique contributors:** {contributors:,}\n\n"
                f"**Most active contributors**\n{leaderboard}"
            ),
        )
        embed.set_footer(text="All available history, including bots and system messages. Read-only.")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelStats(bot))
