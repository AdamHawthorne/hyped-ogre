"""Components V2 daily roll and all-time coin leaderboard."""
import discord
from datetime import date

from hyped_ogre.core.emojis import option_emoji, roll_dice_emoji

COIN_IMAGE = "https://i.vgy.me/agW53e.png"
SUNDAY_DICE_IMAGE = "https://imagizer.imageshack.com/img924/9212/xkmy2Y.png"
WEEKDAY_DICE_IMAGE = "https://imagizer.imageshack.com/img923/9120/KmjqUP.png"
LEDGER_IMAGE = "https://i.vgy.me/n5T2kU.png"
GOLD = 0xD19A3B
PAGE_SIZE = 15


class DailyRollPanel(discord.ui.LayoutView):
    def __init__(self, *, day, winner_text, tagline, on_roll, on_leaderboard,
                 on_stats, top_five=(), coin_emoji="gold coin", ogre_id=0,
                 preview=False, on_gamble=None):
        super().__init__(timeout=60 if preview else None)
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "## Hyped Rolls · Daily Reset\n"
                    f"**{tagline}**\n\n"
                    "One roll per day. Ogre give coin to highest."
                ),
                accessory=discord.ui.Thumbnail(
                    SUNDAY_DICE_IMAGE if date.fromisoformat(day).weekday() == 6 else WEEKDAY_DICE_IMAGE,
                    description="Hyped dice",
                ),
            ),
            discord.ui.TextDisplay(
                "\n\n\n## Ogre's Ledger\n"
                + ("\n".join(
                    f"<@{ogre_id if row['user_id'] == 0 and ogre_id else row['user_id']}> · **{row['coins']}** "
                    + coin_emoji
                    for row in list(top_five)[:5]
                ) or "No gold coins awarded yet.")
            ),
            accent_color=GOLD,
        ))
        controls = discord.ui.ActionRow()
        actions = [
            ("Roll", "hyped_ogre_daily_roll_v2", on_roll),
            ("Leaderboard", "hyped_ogre_coin_leaderboard", on_leaderboard),
            ("My stats", "hyped_ogre_roll_stats", on_stats),
        ]
        if on_gamble is not None:
            actions.insert(1, ("Gamble", "hyped_ogre_coin_gamble_v1", on_gamble))
        for label, custom_id, callback in actions:
            button = discord.ui.Button(
                label=label, custom_id=custom_id,
                emoji=(roll_dice_emoji(date.fromisoformat(day)) if label == "Roll" else
                       option_emoji("ogrecoin") if label == "Gamble" else None),
                style=(discord.ButtonStyle.success if label == "Roll" else
                       discord.ButtonStyle.danger if label == "Gamble" else
                       discord.ButtonStyle.secondary),
                disabled=preview,
            )
            button.callback = callback
            controls.add_item(button)
        self.add_item(controls)


class CoinLeaderboard(discord.ui.LayoutView):
    def __init__(self, rows, *, owner_id, coin_emoji="gold coin", ogre_id=0, page=0):
        super().__init__(timeout=300)
        self.rows = rows
        self.owner_id = owner_id
        self.coin_emoji = coin_emoji
        self.ogre_id = ogre_id
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(page, pages - 1))
        visible = rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        lines = [f"<@{ogre_id if r['user_id'] == 0 and ogre_id else r['user_id']}> · **{r['coins']}** {coin_emoji}" for r in visible]
        ledger_text = (
            "## Ogre' Ledger\n"
            + ("\n".join(lines) or "No gold coins awarded yet. First award at midnight UK time.")
            + f"\n\n-# Ogre give out {sum(r['coins'] for r in rows)} "
              f"{'coin' if sum(r['coins'] for r in rows) == 1 else 'coins'}."
        )
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(ledger_text),
                accessory=discord.ui.Thumbnail(
                    LEDGER_IMAGE, description="Ogre's Ledger"
                ),
            ),
            accent_color=GOLD,
        ))
        if pages > 1:
            controls = discord.ui.ActionRow()
            for label, delta, disabled in (
                ("‹ Previous", -1, self.page == 0),
                ("Next ›", 1, self.page == pages - 1),
            ):
                button = discord.ui.Button(label=label, disabled=disabled,
                                           style=discord.ButtonStyle.secondary)

                async def turn(interaction, delta=delta):
                    await interaction.response.edit_message(view=CoinLeaderboard(
                        self.rows, owner_id=self.owner_id,
                        coin_emoji=self.coin_emoji, ogre_id=self.ogre_id,
                        page=self.page + delta,
                    ))

                button.callback = turn
                controls.add_item(button)
            self.add_item(controls)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Use /leaderboard to open your own leaderboard.", ephemeral=True,
            )
            return False
        return True
