"""Private free-text group planning without obsolete launch dates/times."""

import sqlite3
import discord

from . import live


class LivePlanModal(discord.ui.Modal, title="LFG / LFM · Group plan"):
    def __init__(self, cog, channel_id, owner_id, character_id):
        super().__init__()
        self.cog, self.channel_id, self.owner_id, self.character_id = cog, channel_id, owner_id, character_id
        from cogs import lfg_preview as lfg
        with lfg.database() as con:
            previous = next((r for r in live.listings(con, channel_id) if r["user_id"] == owner_id), {})
        self.activity = discord.ui.TextInput(label="Activity / zone", max_length=120,
            placeholder="Level 30 questing in Stranglethorn, BRD run, PvP...", default=previous.get("activity") or None)
        self.availability = discord.ui.TextInput(label="When / availability (UK time)", max_length=120,
            placeholder="Tonight 19:00-22:00 UK", default=previous.get("availability") or None)
        self.looking_for = discord.ui.TextInput(label="Looking for (optional)", max_length=120, required=False,
            placeholder="Two DPS / a healer / a levelling partner", default=previous.get("looking_for") or None)
        self.notes = discord.ui.TextInput(label="Notes (optional)", max_length=180, required=False,
            style=discord.TextStyle.paragraph, default=previous.get("notes") or None)
        for field in (self.activity, self.availability, self.looking_for, self.notes):
            self.add_item(field)

    async def on_submit(self, interaction):
        from cogs import lfg_preview as lfg
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That group plan isn't yours.", ephemeral=True)
            return
        if interaction.guild_id != int(lfg.os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message("This board belongs to Hyped.", ephemeral=True)
            return
        try:
            character = lfg.shared_character(self.owner_id, self.character_id)
            if character is None:
                raise ValueError("Choose a currently registered character.")
            with lfg.database() as con:
                live.save(con, self.channel_id, self.owner_id, self.character_id,
                    self.activity.value, self.availability.value, self.looking_for.value,
                    self.notes.value, str(interaction.user.display_avatar.url))
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"Ogre couldn't save that plan: {exc}", ephemeral=True)
            return
        await interaction.response.send_message("Ogre saved your group plan. Update it when plans change.", ephemeral=True)
        try:
            await self.cog.refresh(self.channel_id)
        except (discord.HTTPException, sqlite3.Error):
            await interaction.followup.send("Saved. Ogre couldn't refresh the board yet; ask leadership to refresh it.", ephemeral=True)


class LiveCharacterPicker(discord.ui.View):
    def __init__(self, cog, channel_id, owner_id):
        super().__init__(timeout=300)
        from cogs import raid_signup as raid
        characters = raid.member_characters(owner_id)
        if characters:
            select = discord.ui.Select(placeholder="Choose your registered character", options=[
                discord.SelectOption(label=r[1], value=str(r[0]), description=f"{r[2]} / {r[3]} · {r[4]}")
                for r in characters[:25]])

            async def chosen(interaction):
                if interaction.user.id != owner_id:
                    await interaction.response.send_message("That character selection isn't yours.", ephemeral=True)
                    return
                await interaction.response.send_modal(LivePlanModal(cog, channel_id, owner_id, int(select.values[0])))

            select.callback = chosen
            self.add_item(select)
        button = discord.ui.Button(label="Add / edit characters", style=discord.ButtonStyle.secondary)

        async def edit(interaction):
            if interaction.user.id != owner_id:
                await interaction.response.send_message("That character menu isn't yours.", ephemeral=True)
                return
            await interaction.response.send_message(view=raid.CharacterActionView(owner_id, purpose="update"), ephemeral=True)

        button.callback = edit
        self.add_item(button)


class LivePlanButton(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Add / Update", style=discord.ButtonStyle.primary,
                         custom_id="hyped_lfg_live_plan_v1")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction):
        await interaction.response.send_message("Choose a character for your levelling, dungeon or PvP group plan.",
            view=LiveCharacterPicker(self.cog, self.channel_id, interaction.user.id), ephemeral=True)


class RemoveLivePlan(discord.ui.Button):
    def __init__(self, cog, channel_id):
        super().__init__(label="Remove Plan", custom_id="hyped_lfg_remove_live_v1")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction):
        from cogs import lfg_preview as lfg
        if interaction.guild_id != int(lfg.os.environ["HYPED_GUILD_ID"]):
            await interaction.response.send_message("This board belongs to Hyped.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        with lfg.database() as con:
            con.execute("DELETE FROM lfg_live_plans WHERE channel_id=? AND user_id=?",
                        (self.channel_id, interaction.user.id))
        await self.cog.refresh(self.channel_id)
        await interaction.followup.send("Ogre removed your current group plan.", ephemeral=True)
