"""Private off-spec selector reusing the raid/LFG character register."""

import sqlite3

import discord

from . import profiles
from hyped_ogre.features.raids.signup_reference import CLASS_SPECS


class OffspecView(discord.ui.View):
    def __init__(self, owner_id, character_id, class_name):
        super().__init__(timeout=300)
        self.owner_id, self.character_id, self.class_name = owner_id, character_id, class_name
        picker = discord.ui.Select(placeholder="Choose off-spec, or clear it", options=[
            discord.SelectOption(label="No off-spec", value="none"),
            *[discord.SelectOption(label=s, value=s) for s in CLASS_SPECS[class_name]],
        ])

        async def choose(interaction):
            if not await self.check(interaction):
                return
            spec = picker.values[0]
            if spec == "none":
                await self.save(interaction, "", "")
            elif len(CLASS_SPECS[class_name][spec]) == 1:
                await self.save(interaction, spec, CLASS_SPECS[class_name][spec][0])
            else:
                roles = discord.ui.Select(placeholder="Choose off-spec role", options=[
                    discord.SelectOption(label=r, value=r) for r in CLASS_SPECS[class_name][spec]
                ])

                async def choose_role(next_interaction):
                    if await self.check(next_interaction):
                        await self.save(next_interaction, spec, roles.values[0])

                roles.callback = choose_role
                self.clear_items()
                self.add_item(roles)
                await interaction.response.edit_message(content=f"Off-spec: **{spec}**. Choose its role.", view=self)

        picker.callback = choose
        self.add_item(picker)

    async def check(self, interaction):
        from cogs import raid_signup as raid
        if interaction.user.id != self.owner_id or not raid.is_test_member(interaction):
            await interaction.response.send_message("That character isn't yours.", ephemeral=True)
            return False
        return True

    async def save(self, interaction, spec, role):
        from cogs import raid_signup as raid
        try:
            with raid.database(raid.RAID_DB) as con:
                profiles.set_offspec(con, self.owner_id, self.character_id, self.class_name, spec, role)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"Ogre couldn't update off-spec: {exc}", ephemeral=True)
            return
        raid.notify_classrooms(interaction, self.owner_id)
        await interaction.response.edit_message(
            content=(f"Ogre saved off-spec: **{spec} · {role}**. Class chats update automatically."
                     if spec else "Ogre cleared off-spec. Class chats update automatically."), view=None)
