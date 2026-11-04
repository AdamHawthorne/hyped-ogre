"""Reconcile daily player roll reactions, including after a bot restart."""
import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class RollReactions:
    def __init__(self, bot, channel_id, db, today_range, log_error, guild_id=0):
        self.bot = bot
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.db = db
        self.today_range = today_range
        self.log_error = log_error
        self.lock = asyncio.Lock()

    async def sync(self):
        # Serialize overlapping rolls so an older update cannot undo a newer one.
        async with self.lock:
            try:
                await self._sync()
            except Exception as exc:
                # Cosmetic failures must never prevent a roll or role update.
                self.log_error(f"ROLL REACTIONS FAILED: {exc!r}")

    async def _sync(self):
        start, end = self.today_range()
        with self.db() as con:
            rows = con.execute(
                "SELECT user_id, value, actor_type FROM rolls WHERE actor_type IN ('user', 'gamble', 'bot') "
                "AND rolled_at >= ? AND rolled_at < ? ORDER BY rolled_at ASC, rowid ASC",
                (start, end),
            ).fetchall()
        # A higher gamble competes exactly like a free roll, with its own
        # timestamp for the earliest-highest tie rule.
        all_scores = {}
        for user_id, value, actor_type in rows:
            all_scores[user_id] = max(value, all_scores.get(user_id, 0))
        scores = {uid: value for uid, value in all_scores.items() if uid != 0}
        if not scores:
            return
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.channel_id)

        # Read real messages instead of persisting webhook response objects.
        # This also recovers today's rolls posted before this feature was deployed.
        messages = []
        latest_by_user = {}
        first_by_user = {}
        gamble_by_user = {}
        messages_by_user = {}
        async for message in channel.history(
            limit=None, after=datetime.fromtimestamp(start, timezone.utc),
            before=datetime.fromtimestamp(end + 1, timezone.utc), oldest_first=True,
        ):
            if message.author.id != self.bot.user.id:
                continue
            match = re.match(
                r"^<@!?(\d+)> rolled (?:💯|\*\*[\d,]+\*\*) (?:🎲|<:(?:ogredice|ogreivory|ogredie):\d+>|:(?:ogredice|ogreivory|ogredie):)",
                message.content,
            )
            if not match:
                continue
            user_id = int(match.group(1))
            if user_id in scores:
                # Convert bot-authored roll messages to the custom dice.
                # Preserve message identity, score text and all reactions.
                from hyped_ogre.core.emojis import roll_dice_markup
                try:
                    local_day = datetime.fromtimestamp(start, timezone.utc).astimezone(
                        ZoneInfo("Europe/London")
                    ).date()
                except ZoneInfoNotFoundError:
                    # Test/dev machines without tzdata: the date starts at the
                    # supplied UK midnight, whose UTC date is the same.
                    local_day = datetime.fromtimestamp(start, timezone.utc).date()
                expected = roll_dice_markup(local_day)
                fixed = re.sub(
                    r"(?:🎲|<:(?:ogredice|ogreivory|ogredie):\d+>|(?<!<):(?:ogredice|ogreivory|ogredie):)",
                    lambda _match: expected, message.content,
                )
                if fixed != message.content:
                    try:
                        await message.edit(content=fixed)
                    except Exception as exc:
                        self.log_error(
                            f"ROLL DICE MESSAGE REFRESH FAILED message={message.id}: {exc!r}"
                        )
                messages.append((user_id, message))
                latest_by_user[user_id] = message.id
                # Reconstruct the matching message for the *winning roll*.
                # The user's original and gamble posts remain distinct.
                if "risked a coin to roll again:" in message.content:
                    gamble_by_user.setdefault(user_id, message.id)
                else:
                    first_by_user.setdefault(user_id, message.id)
                messages_by_user.setdefault(user_id, []).append(message)

        # Only visible human roll messages count for reaction ranking.
        # Keep Ogre's saved roll even though his message has no user mention.
        all_scores = {user_id: value for user_id, value in all_scores.items()
                      if user_id == 0 or user_id in latest_by_user}
        # Rank by stored values, never by April Fools' display values.
        scores = {user_id: value for user_id, value in scores.items() if user_id in latest_by_user}
        if not scores:
            return
        # Rows are oldest-first. Ties retain the earliest *actual* 85/100,
        # not the oldest user's earlier low free roll. The coin emoji is on
        # the very message that achieved today's highest eligible value.
        high, high_value, high_kind = None, -1, None
        for user_id, value, kind in rows:
            if user_id in scores and value > high_value:
                high, high_value, high_kind = user_id, value, kind
        high_message_id = (
            gamble_by_user.get(high) if high_kind == "gamble"
            else first_by_user.get(high)
        )
        # Rank poop by individual free/gamble rolls, not personal bests.
        # Ogre can block poop, but never receives it; ties favour the first roll.
        eligible = [(uid, value, kind) for uid, value, kind in rows
                    if uid == 0 or uid in scores]
        lowest_value = min(value for _, value, _ in eligible)
        lowest_roll = next((entry for entry in eligible if entry[1] == lowest_value))
        # An Ogre tie for the lowest score blocks poop, regardless of
        # who rolled first. Ogre himself never receives the reaction.
        ogre_tied_lowest = any(uid == 0 and value == lowest_value
                              for uid, value, _ in eligible)
        low = (lowest_roll[0] if not ogre_tied_lowest
               and lowest_roll[0] != 0
               and lowest_value < max(all_scores.values()) else None)
        low_message_id = None
        if low is not None:
            # Match the chronologically ordered DB rolls to visible messages.
            user_rolls = [(value, kind) for uid, value, kind in rows
                          if uid == low and kind in ('user', 'gamble')]
            target_index = next((i for i, (value, _) in enumerate(user_rolls)
                                 if value == lowest_value), None)
            posts = messages_by_user.get(low, [])
            if target_index is not None and target_index < len(posts):
                low_message_id = posts[target_index].id
        guild = self.bot.get_guild(self.guild_id) if self.guild_id and hasattr(self.bot, 'get_guild') else None
        coin = next((e for e in guild.emojis if e.name == "ogrecoin"), None) if guild else None
        high_emoji = coin or "<:ogrecoin:1564336079430099035>"
        from hyped_ogre.core.emojis import markup
        poop_emoji = markup("ogrepoop")
        for user_id, message in messages:
            # Retire the old crown reaction without touching member reactions.
            for reaction in message.reactions:
                if str(reaction.emoji) == "👑" and reaction.me:
                    try:
                        await message.remove_reaction("👑", self.bot.user)
                    except Exception as exc:
                        self.log_error(f"ROLL REACTION CLEANUP FAILED message={message.id}: {exc!r}")
            # Remove Ogre's old Unicode reaction without touching member reactions.
            for reaction in message.reactions:
                if str(reaction.emoji) == "💩" and reaction.me:
                    try:
                        await message.remove_reaction("💩", self.bot.user)
                    except Exception as exc:
                        self.log_error(f"LEGACY POOP CLEANUP FAILED message={message.id}: {exc!r}")
            for emoji, wanted in (
                (high_emoji, user_id == high and message.id == high_message_id),
                (poop_emoji, low is not None and user_id == low
                 and message.id == low_message_id),
            ):
                present = any(str(r.emoji) == str(emoji) and r.me for r in message.reactions)
                try:
                    if wanted and not present:
                        await message.add_reaction(emoji)
                    elif present and not wanted:
                        # Remove only our reaction; leave members' reactions alone.
                        await message.remove_reaction(emoji, self.bot.user)
                except Exception as exc:
                    self.log_error(f"ROLL REACTION FAILED message={message.id}: {exc!r}")
