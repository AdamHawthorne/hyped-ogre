"""Poll presentation and restart-safe routing. Every button is grey."""

import re
from datetime import datetime

import discord

from .store import PollError, deadline_from_text

IMAGE = "https://i.vgy.me/dy0EEH.png"
ACCENT = 0xB84A3A
VISIBILITY = {
    "hidden": "Hidden totals",
    "participation": "Participation only",
    "live": "Live results",
}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def marker(poll_id, kind):
    return f"hyped-poll:{poll_id}:{kind}"


def render(poll, *, private=False, results=False):
    reveal = (
        results
        or (private and poll["state"] == "closed")
        or poll["visibility"] == "live"
        or poll["results_message_id"] is not None
    )
    lines = []
    if poll["description"]:
        lines.extend([safe(poll["description"])[:1000], ""])
    for index, option in enumerate(poll["options"]):
        line = f"**{index + 1}. {safe(option)}**"
        if reveal:
            count = poll["counts"][index]
            percent = round(100 * count / poll["voters"], 1) if poll["voters"] else 0
            line += f" — **{count}** ({percent:g}%)"
        lines.append(line)
    lines.extend(
        [
            "",
            "**Anonymous voting** · "
            + (
                "Choose any options; click again to deselect."
                if poll["multiple"]
                else "One choice per person; you can change your vote."
            ),
        ]
    )
    if not reveal and poll["visibility"] == "hidden":
        lines.append("Totals stay hidden until Ogre publishes the results.")
    if reveal or poll["visibility"] == "participation":
        lines.append(
            f"**{poll['voters']}** {'person has' if poll['voters'] == 1 else 'people have'} voted."
        )
    if reveal and poll["multiple"]:
        lines.append("Percentages are of voters and can total more than 100%.")
    if poll["state"] == "closed":
        lines.append(
            "**Closed**"
            + (
                " · Final results."
                if results
                else " · Results published."
                if poll["results_message_id"]
                else " · Final results go privately to Ogre for review."
            )
        )
    elif poll["state"] == "cancelled":
        lines.append("**Draft cancelled**")
    elif poll["closes_at"]:
        lines.append(f"Closes <t:{poll['closes_at']}:f> (<t:{poll['closes_at']}:R>).")
    else:
        lines.append("Closes manually.")
    if private:
        channel = (
            f"<#{poll['public_channel_id']}>"
            if poll["public_channel_id"]
            else "Choose a channel in Configure"
        )
        lines.extend(
            [
                "",
                f"**{poll['state'].title()}** · Destination: {channel}",
                f"Display: {VISIBILITY[poll['visibility']]}",
            ]
        )
    result = discord.Embed(
        title=(("Results: " if results else "") + safe(poll["question"]))[:256],
        description="\n".join(lines),
        colour=ACCENT,
    )
    result.set_thumbnail(url=IMAGE)
    result.set_footer(
        text=f"Poll #{poll['id']} · Anonymous · {marker(poll['id'], 'results' if results else 'admin' if private else 'public')}"
    )
    return result


class PollButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"hyped_poll:v1:(?P<poll>[0-9]+):(?P<action>[a-z_]+):(?P<option>[0-9]+)",
):
    def __init__(self, poll_id, action, label, *, option=0, disabled=False, row=None):
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"hyped_poll:v1:{poll_id}:{action}:{option}",
                disabled=disabled,
                row=row,
            )
        )
        self.poll_id, self.action, self.option = poll_id, action, option

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(
            int(match["poll"]),
            match["action"],
            item.label or "Open",
            option=int(match["option"]),
        )

    async def callback(self, interaction):
        cog = interaction.client.get_cog("PollsCog")
        if cog is None:
            await interaction.response.send_message(
                "Ogre polls are starting. Try again shortly.", ephemeral=True
            )
            return
        try:
            await cog.dispatch(interaction, self.poll_id, self.action, self.option)
        except Exception as exc:  # noqa: BLE001 - sanitise errors at the interaction boundary
            await cog.report(interaction, exc, self.poll_id)


def public_view(poll, *, preview=False):
    result = discord.ui.View(timeout=None)
    closed = preview or poll["state"] != "open"
    for index, option in enumerate(poll["options"]):
        result.add_item(
            PollButton(
                poll["id"],
                "vote",
                option,
                option=index,
                disabled=closed,
                row=index // 5,
            )
        )
    row = (len(poll["options"]) + 4) // 5
    result.add_item(PollButton(poll["id"], "mine", "My vote", disabled=closed, row=row))
    result.add_item(
        PollButton(poll["id"], "remove", "Remove vote", disabled=closed, row=row)
    )
    return result


def admin_view(poll):
    result = discord.ui.View(timeout=None)
    ident, state = poll["id"], poll["state"]
    if state == "draft":
        for action, label in (
            ("configure", "Configure"),
            ("edit", "Edit question/options"),
            ("preview", "Preview"),
            ("post", "Post poll"),
            ("cancel", "Cancel draft"),
        ):
            result.add_item(
                PollButton(
                    ident,
                    action,
                    label,
                    disabled=action == "post" and not poll["public_channel_id"],
                )
            )
    elif state == "open":
        for action, label in (
            ("configure", "Display/deadline"),
            ("results", "Private results"),
            ("close", "Close poll"),
        ):
            result.add_item(PollButton(ident, action, label))
    elif state == "closed":
        result.add_item(PollButton(ident, "results", "Private results"))
        result.add_item(
            PollButton(
                ident,
                "publish",
                "Results published"
                if poll["results_message_id"]
                else "Publish results",
                disabled=bool(poll["results_message_id"]),
            )
        )
    return result


class OwnedView(discord.ui.View):
    def __init__(self, cog, owner_id, poll_id, *, timeout=300):
        super().__init__(timeout=timeout)
        self.cog, self.owner_id, self.poll_id = cog, owner_id, poll_id

    async def interaction_check(self, interaction):
        try:
            if interaction.user.id != self.owner_id:
                raise PollError("That private menu belongs to another person.")
            self.cog.require_admin(interaction)
        except PollError as exc:
            await self.cog.respond(interaction, str(exc))
            return False
        return True

    async def on_error(self, interaction, error, item):
        await self.cog.report(interaction, error, self.poll_id)


class Confirmation(OwnedView):
    def __init__(self, cog, owner_id, poll_id, action):
        super().__init__(cog, owner_id, poll_id, timeout=90)
        self.action = action

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.secondary)
    async def confirm(self, interaction, button):
        await self.cog.perform_admin(interaction, self.poll_id, self.action)
        self.stop()

    @discord.ui.button(label="Keep poll", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Poll kept as it is.", view=None
        )
        self.stop()


class PollEditor(discord.ui.Modal):
    def __init__(self, cog, owner_id, *, poll=None):
        super().__init__(title="Edit poll draft" if poll else "Create anonymous poll")
        self.cog, self.owner_id, self.poll = cog, owner_id, poll
        self.question = discord.ui.TextInput(
            label="Question / title",
            max_length=256,
            default=poll["question"] if poll else None,
        )
        self.choices = discord.ui.TextInput(
            label="Options: one per line (2–10)",
            style=discord.TextStyle.paragraph,
            max_length=809,
            default="\n".join(poll["options"]) if poll else "Yes\nNo",
        )
        self.details = discord.ui.TextInput(
            label="Additional details (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=poll["description"] if poll else None,
        )
        existing = (
            datetime.fromtimestamp(poll["closes_at"], cog.timezone).isoformat(
                timespec="minutes"
            )
            if poll and poll["closes_at"]
            else None
        )
        self.deadline = discord.ui.TextInput(
            label="Deadline: 2h / 7d / UK date-time (optional)",
            placeholder="Leave blank to close manually.",
            required=False,
            max_length=40,
            default=existing,
        )
        for field in (self.question, self.choices, self.details, self.deadline):
            self.add_item(field)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id:
            raise PollError("That editor belongs to another person.")
        self.cog.require_admin(interaction)
        closes_at = deadline_from_text(self.deadline.value, self.cog.timezone.key)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.poll:
            ident = self.poll["id"]
            await self.cog.run(
                self.cog.store.edit,
                interaction.guild_id,
                ident,
                self.question.value,
                self.choices.value,
                self.details.value,
                closes_at,
            )
        else:
            ident = await self.cog.run(
                self.cog.store.create,
                interaction.guild_id,
                interaction.user.id,
                interaction.channel_id,
                self.question.value,
                self.choices.value,
                self.details.value,
                closes_at,
            )
        await self.cog.sync(ident)
        await self.cog.respond(
            interaction,
            f"Poll #{ident} draft saved here. Use Configure to choose its destination, then Post poll.",
        )

    async def on_error(self, interaction, error):
        await self.cog.report(interaction, error, self.poll["id"] if self.poll else 0)


class DeadlineEditor(discord.ui.Modal):
    def __init__(self, cog, owner_id, poll):
        super().__init__(title="Poll deadline")
        self.cog, self.owner_id, self.poll = cog, owner_id, poll
        current = (
            datetime.fromtimestamp(poll["closes_at"], cog.timezone).isoformat(
                timespec="minutes"
            )
            if poll["closes_at"]
            else None
        )
        self.deadline = discord.ui.TextInput(
            label="2h / 7d / UK date-time; blank=manual",
            required=False,
            max_length=40,
            default=current,
        )
        self.add_item(self.deadline)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id:
            raise PollError("That editor belongs to another person.")
        value = deadline_from_text(self.deadline.value, self.cog.timezone.key)
        await self.cog.configure(interaction, self.poll["id"], "closes_at", value)

    async def on_error(self, interaction, error):
        await self.cog.report(interaction, error, self.poll["id"])


class Destination(discord.ui.ChannelSelect):
    def __init__(self, *, disabled=False):
        super().__init__(
            placeholder="Destination for the poll",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            disabled=disabled,
        )

    async def callback(self, interaction):
        channel = self.values[0]
        await self.view.cog.validate_destination(interaction, channel.id)
        await self.view.cog.configure(
            interaction, self.view.poll_id, "public_channel_id", channel.id
        )


class Display(discord.ui.Select):
    def __init__(self, current):
        super().__init__(
            placeholder="What members can see",
            options=[
                discord.SelectOption(label=label, value=key, default=key == current)
                for key, label in VISIBILITY.items()
            ],
        )

    async def callback(self, interaction):
        await self.view.cog.configure(
            interaction, self.view.poll_id, "visibility", self.values[0]
        )


class Voting(discord.ui.Select):
    def __init__(self, multiple, *, disabled=False):
        super().__init__(
            placeholder="Voting type",
            disabled=disabled,
            options=[
                discord.SelectOption(
                    label="One choice per person", value="single", default=not multiple
                ),
                discord.SelectOption(
                    label="Multiple choices", value="multiple", default=bool(multiple)
                ),
            ],
        )

    async def callback(self, interaction):
        await self.view.cog.configure(
            interaction, self.view.poll_id, "multiple", self.values[0] == "multiple"
        )


class SettingsView(OwnedView):
    def __init__(self, cog, owner_id, poll):
        super().__init__(cog, owner_id, poll["id"])
        self.add_item(Destination(disabled=poll["state"] != "draft"))
        self.add_item(Display(poll["visibility"]))
        self.add_item(Voting(poll["multiple"], disabled=poll["state"] != "draft"))

    @discord.ui.button(label="Set deadline", style=discord.ButtonStyle.secondary, row=3)
    async def deadline(self, interaction, button):
        poll = await self.cog.run(
            self.cog.store.get, interaction.guild_id, self.poll_id
        )
        await interaction.response.send_modal(
            DeadlineEditor(self.cog, interaction.user.id, poll)
        )
