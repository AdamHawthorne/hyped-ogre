"""Private, owner-checked absence and removal controls for the roster."""

import asyncio
import sqlite3

import discord
from cogs import raid_signup as raid
from hyped_ogre.features.classrooms.config import GUILD_ID
from . import availability


async def check_owner(interaction, owner_id):
    if interaction.guild_id == GUILD_ID and interaction.user.id == owner_id:
        return True
    await interaction.response.send_message("This menu belongs to another member.", ephemeral=True)
    return False


class OwnedView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        return await check_owner(interaction, self.owner_id)

    async def on_error(self, interaction, error, item):
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send("Ogre couldn't finish. Reopen Remove / absent and try again.", ephemeral=True)


class AbsenceModal(discord.ui.Modal, title="Long-term absence"):
    period = discord.ui.TextInput(label="When will you be away?", max_length=100,
                                  placeholder="Dates, expected return, or until further notice")
    note = discord.ui.TextInput(label="Note for Leadership (optional)", required=False,
                                style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id

    async def on_submit(self, interaction):
        if not await check_owner(interaction, self.owner_id):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            changed = await asyncio.to_thread(availability.save, raid.RAID_DB, self.owner_id,
                                              self.period.value, self.note.value)
        except (ValueError, sqlite3.Error):
            await interaction.followup.send("Ogre couldn't save your absence. Please try again.", ephemeral=True)
            return
        if changed:
            availability.notify(interaction)
        await interaction.followup.send(
            "Ogre saved your long-term absence. Leadership will be notified privately. "
            "Use Absent on any raids you have already signed for. "
            "When you return, choose Remove / absent > I'm back.", ephemeral=True)


class RemovalView(OwnedView):
    def __init__(self, owner_id, page=0):
        super().__init__(owner_id)
        characters = raid.member_characters(owner_id)
        pages = max(1, (len(characters) + 24) // 25)
        page = max(0, min(page, pages - 1))
        if characters:
            picker = discord.ui.Select(placeholder="Choose a character to remove", options=[
                discord.SelectOption(label=r[1][:100], value=str(r[0]), emoji=raid.option_emoji(r[2]))
                for r in characters[page * 25:(page + 1) * 25]])

            async def select(interaction):
                if not await check_owner(interaction, owner_id):
                    return
                selected = next((r for r in raid.member_characters(owner_id)
                                 if str(r[0]) == picker.values[0]), None)
                if selected is None:
                    await interaction.response.send_message("That character is no longer available.", ephemeral=True)
                    return
                await interaction.response.send_message(
                    view=raid.ConfirmCharacterDeleteView(owner_id, selected[0], selected[1]), ephemeral=True)

            picker.callback = select
            self.add_item(picker)
        if pages > 1:
            for label, step in (("Previous", -1), ("Next", 1)):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                           disabled=not 0 <= page + step < pages)

                async def turn(interaction, step=step):
                    if await check_owner(interaction, owner_id):
                        await interaction.response.edit_message(view=RemovalView(owner_id, page + step))

                button.callback = turn
                self.add_item(button)


class AbsenceActions(OwnedView):
    @discord.ui.button(label="Long-term absence", style=discord.ButtonStyle.secondary)
    async def absent(self, interaction, button):
        await interaction.response.send_modal(AbsenceModal(self.owner_id))

    @discord.ui.button(label="I'm back", style=discord.ButtonStyle.secondary)
    async def returned(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            changed = await asyncio.to_thread(availability.clear, raid.RAID_DB, self.owner_id)
        except sqlite3.Error:
            await interaction.followup.send("Ogre couldn't clear your absence. Please try again.", ephemeral=True)
            return
        if changed:
            availability.notify(interaction)
        await interaction.followup.send(
            "Ogre cleared your long-term absence. Leadership will be notified. Raid sign-ups stay as you set them."
            if changed else "Ogre has no long-term absence saved for you.", ephemeral=True)

    @discord.ui.button(label="Remove character", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        view = RemovalView(self.owner_id)
        await interaction.response.send_message(
            "Choose a character. Ogre will ask you to confirm and notify Leadership after removal."
            if view.children else "Ogre has no characters for you to remove.",
            view=view, ephemeral=True)
