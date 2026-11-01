"""Leadership-only V2 raid poster: menu-driven setup, no public applicant list."""
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import sqlite3

from cogs.raid_schedule import (
    RAID_IMAGE, ACCENT, format_discord_date, validate_check_message_url,
    raid_panel_when,
    list_raids, parse_uk_datetime, read_raid, save_raid,
    validate_softres_url, validate_banner_url, set_softres_url,
)

UK = ZoneInfo("Europe/London")
# Prefer the guild raid channel; never accidentally publish a new raid to #ogre.
DEFAULT_RAID_CHANNEL_ID = 1563663151323553802
INSTANCES = ("Hyjal (20m)", "Barrows (10m)", "Ony (40m)")
TIMES = ("18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
         "21:00", "21:30", "22:00")
WOWSIMS_POLICIES = {
    "missing": "Required if missing (default)",
    "fresh": "Require fresh export for this raid",
    "optional": "Optional (no requirement)",
}
ANNOUNCEMENTS = {
    "1": "1 day before",
    "2": "2 days before",
    "3": "3 days before",
    "7": "1 week before",
    "0": "On raid day",
    "none": "To be confirmed",
    "custom": "Choose exact announcement date & time…",
}


def next_sunday_date(raids=(), now=None):
    """The next Sunday slot, after any already scheduled raids."""
    now = now or datetime.now(UK)
    today = now.date()
    scheduled = [
        datetime.fromisoformat(raid["raid_at"]).astimezone(UK).date()
        for raid in raids if raid.get("raid_at")
    ]
    latest = max(scheduled) if scheduled else None
    anchor = max(today, latest) if latest else today
    days = (6 - anchor.weekday()) % 7
    if days == 0 and (
        (latest is not None and latest >= today) or
        (anchor == today and now.hour >= 20)
    ):
        days = 7
    return (anchor + timedelta(days=days)).isoformat()


def draft_from_event(event, raids=()):
    """New Sunday raid with safe defaults; existing raid preserves its values."""
    if not event:
        saved_channel = next(
            (raid.get("panel_channel_id") for raid in raids
             if raid.get("panel_channel_id")), None
        )
        return {
            "number": None, "day": next_sunday_date(raids), "time": "20:00",
            "channel_id": saved_channel or DEFAULT_RAID_CHANNEL_ID,
            "ping_role_ids": [], "default_show_discord": True,
            "wowsims_policy": "missing", "end_time": None,
            "check_message_url": None,
            "instance_1": "Hyjal (20m)", "instance_2": "Barrows (10m)",
            "instance_3": None,
            "announce": "1", "custom_announce": None, "softres_url": None,
            "custom_title": "", "banner_url": None,
        }
    start = datetime.fromisoformat(event["raid_at"]).astimezone(UK)
    return {
        "number": event["raid_id"], "channel_id": event.get("panel_channel_id"), "ping_role_ids": [],
        "default_show_discord": bool(event.get("default_show_discord", 1)),
        "wowsims_policy": event.get("wowsims_policy") or "missing",
        "day": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "end_time": (datetime.fromisoformat(event["end_at"]).astimezone(UK).strftime("%H:%M")
                     if event.get("end_at") else None),
        "check_message_url": event.get("check_message_url"),
        "instance_1": event.get("instance_1"),
        "instance_2": event.get("instance_2"),
        "instance_3": event.get("instance_3"),
        "announce": "custom" if event.get("roster_announce_at") else "none",
        "custom_announce": event.get("roster_announce_at"),
        "softres_url": event.get("softres_url"),
        "custom_title": event.get("custom_title") or "",
        "banner_url": event.get("banner_url"),
    }


def poster_schedule(draft):
    """Validated UK local clock times to save in the same format as /raid setup."""
    if not draft["day"]:
        raise ValueError("Choose the raid day.")
    if not draft["instance_1"]:
        raise ValueError("Choose raid instance #1.")
    raid_at = f'{draft["day"]} {draft["time"]}'
    parse_uk_datetime(raid_at)
    announce_type = draft["announce"]
    if announce_type == "none":
        announce = None
    elif announce_type == "custom":
        if not draft["custom_announce"]:
            raise ValueError("Set the custom roster announcement date.")
        announce_at = datetime.fromisoformat(draft["custom_announce"])
        announce = announce_at.astimezone(UK).strftime("%Y-%m-%d %H:%M")
    else:
        day = date.fromisoformat(draft["day"]) - timedelta(days=int(announce_type))
        announce = f'{day.isoformat()} {draft["time"]}'
    return raid_at, announce


def poster_end_at(draft):
    """Return optional UK end datetime, or None for a three-hour default."""
    if not draft.get("end_time"):
        return None
    start = parse_uk_datetime(f'{draft["day"]} {draft["time"]}')
    try:
        end_clock = datetime.strptime(draft["end_time"], "%H:%M").time()
    except (ValueError, TypeError) as exc:
        raise ValueError("End time must use HH:MM (UK time).") from exc
    # A clock time at/before the start means an end after midnight.
    end_day = start.date() + timedelta(days=end_clock <= start.time())
    end = parse_uk_datetime(f'{end_day.isoformat()} {end_clock:%H:%M}')
    duration = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
    if not 0 < duration <= 12 * 3600:
        raise ValueError("End time must be after the start, within 12 hours.")
    return end.isoformat()


class PosterTextModal(discord.ui.Modal):
    """Only custom values require typing; most setup uses dropdowns."""
    def __init__(self, poster, owner_id, message_id, field, title, label, placeholder,
                 required=True):
        super().__init__(title=title)
        self.poster = poster
        self.owner_id = owner_id
        self.message_id = message_id
        self.field = field
        self.text_field = discord.ui.TextInput(
            label=label, placeholder=placeholder,
            max_length=(1500 if field == "softres_url"
                        else 512 if field in ("banner_url", "check_message_url")
                        else 70 if field == "custom_title"
                        else 5 if field in ("time", "end_time") else 80),
            required=required,
        )
        self.add_item(self.text_field)

    async def on_submit(self, interaction):
        if not self.poster.authorised(interaction):
            await interaction.response.send_message("Only Ogre's owner can edit raids.", ephemeral=True)
            return
        value = self.text_field.value.strip()
        try:
            if self.field == "softres_url":
                value = validate_softres_url(value)
            elif self.field == "day":
                date.fromisoformat(value)
            elif self.field == "custom_announce":
                value = parse_uk_datetime(value).isoformat()
            elif self.field == "custom_title":
                value = " ".join(value.split())
            elif self.field in ("time", "end_time"):
                if value:
                    datetime.strptime(value, "%H:%M")
            elif self.field == "banner_url":
                value = validate_banner_url(value)
            elif self.field == "check_message_url":
                value = validate_check_message_url(value)
            elif self.field.startswith("instance_"):
                if not value:
                    raise ValueError("First raid instance cannot be blank.")
        except ValueError as exc:
            await interaction.response.send_message(f"Ogre couldn't use that value: {exc}", ephemeral=True)
            return
        new_draft = dict(self.poster.draft)
        new_draft[self.field] = value or None
        if self.field in ("time", "end_time") and new_draft["day"]:
            try:
                poster_end_at(new_draft)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        if self.field == "custom_announce":
            new_draft["announce"] = "custom"
        await interaction.response.defer(ephemeral=True)
        try:
            message = await interaction.channel.fetch_message(self.message_id)
            await message.edit(view=RaidPosterView(
                self.poster.cog, self.owner_id, new_draft,
                page=self.poster.page,
            ))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("Ogre couldn't update raid poster.", ephemeral=True)


class RaidDeleteConfirmation(discord.ui.LayoutView):
    """Destructive action needs a second, explicit owner-only confirmation."""

    def __init__(self, cog, owner_id, raid_number):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.raid_number = raid_number
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"## Delete <Hyped> Raid #{raid_number}?\n\n"
                "**This cannot be undone.**\n"
                "Ogre will remove its raid card, sign-ups and attendance.\n"
                "Saved characters, gear exports and other raids remain untouched."
            ), accent_color=0xBA4C40,
        ))
        confirm = discord.ui.Button(label=f"Yes, delete Raid #{raid_number}",
                                    style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def do_delete(interaction):
            if not self.cog.allowed(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Only Ogre's owner may delete raids.", ephemeral=True
                )
                return
            await self.cog.delete_raid_from_poster(interaction, self.raid_number)

        async def undo(interaction):
            if not self.cog.allowed(interaction) or interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Only Ogre's owner may edit raids.", ephemeral=True
                )
                return
            from cogs.raid_schedule import read_raid
            event = read_raid(self.cog.raid_db_path(), self.raid_number)
            await interaction.response.edit_message(
                view=RaidPosterView(self.cog, self.owner_id, draft_from_event(event))
            )

        confirm.callback = do_delete
        cancel.callback = undo
        self.add_item(discord.ui.ActionRow(confirm, cancel))


class RaidRosterPreview(discord.ui.View):
    """Owner-only preview: publish quietly, or publish and ping selected users."""
    def __init__(self, cog, owner_id, raid_id, entries, already_announced=False):
        super().__init__(timeout=300)
        self.cog, self.owner_id, self.raid_id = cog, owner_id, raid_id
        self.snapshot = tuple(tuple(x) for x in entries)
        if already_announced:
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.label == "Publish + ping selected":
                    child.disabled = True

    async def _publish(self, interaction, *, notify):
        if interaction.user.id != self.owner_id or not self.cog.allowed(interaction):
            await interaction.response.send_message(
                "Only the poster owner can publish.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from cogs import raid_sheets_v3
        from cogs.raid_schedule import raid_status
        try:
            current = await __import__("asyncio").to_thread(
                raid_sheets_v3.read_selected, self.cog.raid_db_path(), self.raid_id)
            if tuple(tuple(x) for x in current) != self.snapshot:
                await interaction.followup.send(
                    "Sheet selection changed. Open a fresh preview before publishing.",
                    ephemeral=True)
                return
            raid_status(self.cog.raid_db_path(), self.raid_id, "confirm",
                        confirmed_entries=current)
            await self.cog.sync_raid_sheets()
            await self.cog.refresh_saved_panel(self.raid_id)
        except (ValueError, RuntimeError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.followup.send(f"Roster publish: {exc}", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
        response = (f"Raid #{self.raid_id} roster published. "
                    "Sign-ups stay open; Sheet assignments remain editable.")
        if notify:
            try:
                link = await self.cog.announce_roster(self.raid_id, selected=current)
            except (ValueError, RuntimeError, sqlite3.Error, discord.HTTPException) as exc:
                response += f" Ogre could not announce: {exc}"
            else:
                response += f" Ogre pinged selected raiders once: {link}"
        await interaction.followup.send(response, ephemeral=True)

    @discord.ui.button(label="Publish roster only", style=discord.ButtonStyle.success)
    async def publish_only(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._publish(interaction, notify=False)

    @discord.ui.button(label="Publish + ping selected", style=discord.ButtonStyle.primary)
    async def publish_and_ping(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._publish(interaction, notify=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the poster owner.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Roster preview cancelled.", view=None)


class RaidPosterView(discord.ui.LayoutView):
    """Owner-controlled non-ephemeral setup card; published raid is a separate message."""
    def __init__(self, cog, owner_id, draft=None, *, page="schedule"):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.page = page if page in ("schedule", "advanced") else "schedule"
        raids = list_raids(cog.raid_db_path())
        self.draft = dict(draft) if draft is not None else draft_from_event(None, raids)
        if self.draft["number"] is None:
            self.draft["number"] = (raids[0]["raid_id"] + 1) if raids else 1
        self.saved_raid_ids = {raid["raid_id"] for raid in raids}
        self.saved_event = (
            read_raid(cog.raid_db_path(), self.draft["number"])
            if self.draft["number"] in self.saved_raid_ids else None
        )
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(self.description()),
                accessory=discord.ui.Thumbnail(RAID_IMAGE),
            ), accent_color=ACCENT,
        ))
        if self.page == "schedule":
            # The common path: schedule, post, pick roster, manage, complete.
            self.add_raid_picker(raids)
            self.add_date_picker()
            self.add_time_picker()
            self.add_instance_picker(1)
            self.add_instance_picker(2)
            self.add_announcement_picker()
        else:
            # Rare configuration should not crowd routine raid scheduling.
            self.add_end_time_picker()
            self.add_instance_picker(3)
            self.add_destination_picker()
            self.add_ping_role_picker()
            self.add_visibility_picker()
            self.add_wowsims_picker()
        self.add_controls()
        self.add_command_footer()

    def authorised(self, interaction):
        return self.cog.allowed(interaction) and interaction.user.id == self.owner_id

    def description(self):
        d = self.draft
        event = self.saved_event
        if event and event.get("archived_at"):
            state = "Completed / archived"
        elif event and event.get("roster_published"):
            state = "Roster published (sign-ups open)"
        elif event and event.get("panel_message_id"):
            state = "Collecting sign-ups"
        else:
            state = "Not posted"
        try:
            raid_at, announce = poster_schedule(d)
            raid_date = raid_panel_when(
                parse_uk_datetime(raid_at).isoformat(), poster_end_at(d)
            )
            confirm_date = (
                format_discord_date(
                    parse_uk_datetime(announce).isoformat(), style="R",
                    with_relative=False,
                ) if announce else "Not scheduled"
            )
        except ValueError:
            raid_date = f'{d.get("day") or "Choose date"} · {d["time"]} UK'
            confirm_date = ANNOUNCEMENTS.get(d["announce"], "Choose date")
        name = f"<Hyped> Raid #{d['number']}"
        title = " ".join((d.get("custom_title") or "").split())
        if title:
            name += " - " + discord.utils.escape_mentions(
                discord.utils.escape_markdown(title)
            )
        channel = (
            f"<#{d['channel_id']}>" if d.get("channel_id")
            else "Choose destination"
        )
        common = (
            f"## Raid Poster · {name}\n"
            f"**Status:** {state}\n"
            f"**Raid:** {raid_date}\n"
            f"**Instances:** {discord.utils.escape_markdown(d['instance_1'] or 'Choose instance')}"
            + (f" · {discord.utils.escape_markdown(d['instance_2'])}"
               if d.get("instance_2") else "")
            + (f" · {discord.utils.escape_markdown(d['instance_3'])}"
               if d.get("instance_3") else "")
            + f"\n**Roster confirmation:** {confirm_date}\n"
            f"**Destination:** {channel}\n"
        )
        if self.page == "schedule":
            return (
                common
                + f"**WoWSims:** {WOWSIMS_POLICIES[d.get('wowsims_policy', 'missing')]}\n\n"
                "**Workflow:** Post raid → Collect sign-ups → Build teams in Sheets "
                "→ Publish roster → Manage attendance → Complete raid.\n"
                "-# Roster confirmation never closes Attend or WoWSims. "
                "Only completion closes the raid. No automatic roster pings.\n"
                "-# Select **Advanced settings** for links, visibility, pings, "
                "extra instances and WoWSims rules."
            )
        return (
            common + "\n**Advanced settings**\n"
            f"**End time:** {d.get('end_time') or 'Default: 3 hours'}\n"
            f"**Check message:** {'[View message](' + d['check_message_url'] + ')' if d.get('check_message_url') else 'Not set (optional)'}\n"
            f"**SoftRes:** {'Set' if d.get('softres_url') else 'Not set (optional)'}\n"
            f"**Bottom banner:** {'Custom image' if d.get('banner_url') else 'None (optional)'}\n"
            f"**Show on Discord:** {'Yes' if d.get('default_show_discord', True) else 'No'}\n"
            f"**WoWSims:** {WOWSIMS_POLICIES[d.get('wowsims_policy', 'missing')]}\n"
            f"**Ping roles (new panel only):** "
            f"{', '.join('<@&' + str(r) + '>' for r in d.get('ping_role_ids', [])) or 'None'}\n\n"
            "-# Save using **Post / Update Raid**. Edits to an existing "
            "panel are silent; role pings only occur on a new post."
        )

    def _select(self, label, options, callback):
        select = discord.ui.Select(
            placeholder=label, options=options, min_values=1, max_values=1
        )
        select.callback = callback
        self.add_item(discord.ui.ActionRow(select))
        return select

    async def changed(self, interaction, **updates):
        if not self.authorised(interaction):
            await interaction.response.send_message(
                "Only Ogre's owner can edit raids.", ephemeral=True
            )
            return
        draft = {**self.draft, **updates}
        await interaction.response.edit_message(
            view=RaidPosterView(self.cog, self.owner_id, draft, page=self.page)
        )

    def add_raid_picker(self, raids):
        options = [discord.SelectOption(
            label=f"New Raid #{(raids[0]['raid_id'] + 1) if raids else 1}",
            value="new",
        )]
        options += [
            discord.SelectOption(label=f"Edit Raid #{r['raid_id']}", value=str(r["raid_id"]))
            for r in raids
        ]
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            selection = picker.values[0]
            d = draft_from_event(
                None if selection == "new" else read_raid(
                    self.cog.raid_db_path(), int(selection)
                ), raids,
            )
            await interaction.response.edit_message(
                view=RaidPosterView(self.cog, self.owner_id, d, page="schedule")
            )
        picker = self._select("Choose new or existing raid", options, choose)

    def add_destination_picker(self):
        picker = discord.ui.ChannelSelect(
            placeholder="Choose destination channel",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await self.changed(interaction, channel_id=picker.values[0].id)
        picker.callback = choose
        self.add_item(discord.ui.ActionRow(picker))

    def add_ping_role_picker(self):
        picker = discord.ui.RoleSelect(
            placeholder="Roles to ping (optional, up to 10)",
            min_values=0, max_values=10,
        )
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await self.changed(interaction, ping_role_ids=[role.id for role in picker.values])
        picker.callback = choose
        self.add_item(discord.ui.ActionRow(picker))

    def add_visibility_picker(self):
        async def choose(interaction):
            await self.changed(
                interaction, default_show_discord=(picker.values[0] == "yes")
            )
        picker = self._select("Show selected players by default?", [
            discord.SelectOption(
                label="Yes", value="yes",
                description="Show selected players on confirmed roster",
                default=bool(self.draft.get("default_show_discord", True)),
            ),
            discord.SelectOption(
                label="No", value="no",
                description="Keep players hidden until individually enabled",
                default=not bool(self.draft.get("default_show_discord", True)),
            ),
        ], choose)

    def add_wowsims_picker(self):
        async def choose(interaction):
            await self.changed(interaction, wowsims_policy=picker.values[0])
        picker = self._select("WoWSims requirement", [
            discord.SelectOption(
                label="Required if missing (default)", value="missing",
                description="Saved exports OK; new characters must paste one",
                default=self.draft.get("wowsims_policy", "missing") == "missing",
            ),
            discord.SelectOption(
                label="Require fresh export", value="fresh",
                description="Each new sign-up needs an export after enabling",
                default=self.draft.get("wowsims_policy") == "fresh",
            ),
            discord.SelectOption(
                label="Optional / off", value="optional",
                description="Allow Attend without a WoWSims export",
                default=self.draft.get("wowsims_policy") == "optional",
            ),
        ], choose)

    def add_date_picker(self):
        today = datetime.now(UK).date()
        dates = [today + timedelta(days=i) for i in range(24)]
        chosen = self.draft.get("day")
        if chosen and chosen not in (d.isoformat() for d in dates):
            dates = [date.fromisoformat(chosen)] + dates[:23]
        options = [
            discord.SelectOption(
                label=d.strftime("%a %d %b %Y"),
                value=d.isoformat(), default=d.isoformat() == chosen,
            ) for d in dates
        ]
        options.append(discord.SelectOption(label="Other date...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id, "day",
                    "Choose raid date", "YYYY-MM-DD", "2026-11-05",
                ))
            else:
                await self.changed(interaction, day=picker.values[0])
        picker = self._select("Choose raid date (UK)", options, choose)

    def add_time_picker(self):
        choices = list(TIMES)
        if self.draft["time"] not in choices:
            choices.append(self.draft["time"])
        options = [
            discord.SelectOption(label=f"{t} UK", value=t, default=t == self.draft["time"])
            for t in choices
        ]
        options.append(discord.SelectOption(label="Other start time (HH:MM)...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "time", "Choose raid start time", "Start time HH:MM (UK)",
                    "21:00",
                ))
            else:
                await self.changed(interaction, time=picker.values[0])
        picker = self._select("Choose start time (UK)", options, choose)

    def add_end_time_picker(self):
        d = self.draft
        day = d["day"] or datetime.now(UK).date().isoformat()
        try:
            start = parse_uk_datetime(f'{day} {d["time"]}')
        except ValueError:
            start = datetime.now(UK)
        # Convenient options around the raid start; custom HH:MM is also supported.
        suggested = []
        for half_hours in range(2, 13):
            end = (start.astimezone(timezone.utc) +
                   timedelta(minutes=30 * half_hours)).astimezone(UK)
            value = end.strftime("%H:%M")
            if value not in suggested:
                suggested.append(value)
        auto_end = (start.astimezone(timezone.utc) + timedelta(hours=3)).astimezone(UK)
        current = d.get("end_time")
        options = [discord.SelectOption(
            label=f"Default: +3h ({auto_end:%H:%M} UK)",
            value="auto", default=current is None,
        )]
        for value in suggested:
            options.append(discord.SelectOption(
                label=f"{value} UK" + (" (next day)" if value <= d["time"] else ""),
                value=value, default=value == current,
            ))
        if current and current not in suggested:
            options.append(discord.SelectOption(
                label=f"{current} UK (custom)",
                value=current, default=True,
            ))
        options.append(discord.SelectOption(label="Other time (HH:MM)...", value="custom"))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            selection = picker.values[0]
            if selection == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "end_time", "Choose raid end time", "End time HH:MM (UK)",
                    "00:00",
                ))
            else:
                await self.changed(interaction, end_time=None if selection == "auto" else selection)
        picker = self._select("Choose end time (UK)", options, choose)

    def add_instance_picker(self, number):
        field = f"instance_{number}"
        current = self.draft[field]
        choices = [
            discord.SelectOption(
                label="None", value="none", default=current is None,
            )
        ] if number > 1 else []
        for label in INSTANCES:
            choices.append(discord.SelectOption(
                label=label,
                value="custom" if label == "Other / Custom" else label,
                default=label == current,
            ))
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            value = picker.values[0]
            if value == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id, field,
                    f"Instance #{number}", "Instance name", "Raid instance name",
                ))
            else:
                updates = {field: None if value == "none" else value}
                if number == 2 and value == "none":
                    updates["instance_3"] = None
                await self.changed(interaction, **updates)
        picker = self._select(f"Raid instance #{number}", choices, choose)

    def add_announcement_picker(self):
        options = [
            discord.SelectOption(
                label=label, value=key, default=key == self.draft["announce"]
            ) for key, label in ANNOUNCEMENTS.items()
        ]
        async def choose(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if picker.values[0] == "custom":
                await interaction.response.send_modal(PosterTextModal(
                    self, self.owner_id, interaction.message.id,
                    "custom_announce", "Roster announcement",
                    "YYYY-MM-DD HH:MM (UK)", "2026-11-04 20:00",
                ))
            else:
                await self.changed(interaction, announce=picker.values[0])
        picker = self._select("When to announce the roster?", options, choose)

    def add_controls(self):
        row = discord.ui.ActionRow()
        title = discord.ui.Button(label="Optional Raid Title",
                                  style=discord.ButtonStyle.secondary)
        banner = discord.ui.Button(label="Bottom Banner",
                                   style=discord.ButtonStyle.secondary)
        softres = discord.ui.Button(label="Optional SoftRes", style=discord.ButtonStyle.secondary)
        clear = discord.ui.Button(label="Remove SoftRes", style=discord.ButtonStyle.secondary,
                                  disabled=not bool(self.draft["softres_url"]))
        check_link = discord.ui.Button(label="Check Message Link",
                                       style=discord.ButtonStyle.secondary)
        clear_check = discord.ui.Button(label="Remove Check Link",
                                        style=discord.ButtonStyle.secondary,
                                        disabled=not bool(self.draft.get("check_message_url")))
        publish = discord.ui.Button(label="Post / Update Raid", style=discord.ButtonStyle.success)
        closed = bool(self.saved_event and self.saved_event.get("archived_at"))
        publish.disabled = closed
        confirm_roster = discord.ui.Button(
            label="Preview / Confirm Roster", style=discord.ButtonStyle.primary,
            disabled=self.saved_event is None or closed
        )
        manage = discord.ui.Button(
            label="Manage Raid", style=discord.ButtonStyle.primary,
            disabled=not bool(self.saved_event and
                              self.saved_event.get("roster_published")) or closed
        )
        delete = discord.ui.Button(
            label="Delete Raid", style=discord.ButtonStyle.danger,
            disabled=self.saved_event is None
        )
        advanced = discord.ui.Button(
            label="Advanced settings", style=discord.ButtonStyle.secondary
        )
        back = discord.ui.Button(
            label="Back to schedule", style=discord.ButtonStyle.secondary
        )

        async def set_title(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "custom_title", "Optional raid title", "Text after Raid #",
                "e.g. First clear / Progression", required=False,
            ))

        async def set_banner(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "banner_url", "Bottom banner image", "HTTPS image URL",
                "https://i.vgy.me/yourimage.png", required=False,
            ))

        async def set_link(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "softres_url", "Optional SoftRes link", "SoftRes raid URL",
                "https://softres.it/raid/...", required=False,
            ))

        async def remove_link(interaction):
            await self.changed(interaction, softres_url=None)

        async def set_check_link(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            await interaction.response.send_modal(PosterTextModal(
                self, self.owner_id, interaction.message.id,
                "check_message_url", "Optional Check message",
                "Discord message URL",
                "https://discord.com/channels/server/channel/message",
                required=False,
            ))

        async def remove_check_link(interaction):
            await self.changed(interaction, check_message_url=None)

        async def post(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            try:
                raid_at, announce = poster_schedule(self.draft)
                end_at = poster_end_at(self.draft)
                event = save_raid(
                    self.cog.raid_db_path(), self.draft["number"], raid_at,
                    self.draft["instance_1"], self.draft["instance_2"],
                    self.draft["instance_3"], announce, self.draft["softres_url"],
                    custom_title=self.draft.get("custom_title") or "",
                    banner_url=self.draft.get("banner_url") or "",
                    end_at=end_at,
                    check_message_url=self.draft.get("check_message_url"),
                    wowsims_policy=self.draft.get("wowsims_policy", "missing"),
                )
                from cogs.raid_schedule import database
                with database(self.cog.raid_db_path()) as con:
                    con.execute("UPDATE raids SET default_show_discord=? WHERE raid_id=?",
                                (int(self.draft.get("default_show_discord", True)), event["raid_id"]))
                event = read_raid(self.cog.raid_db_path(), event["raid_id"])
                if self.draft["softres_url"] is None:
                    event = set_softres_url(
                        self.cog.raid_db_path(), self.draft["number"], None
                    )
            except (ValueError, sqlite3.Error) as exc:
                # Configuration errors should never publish a partial raid.
                await interaction.response.send_message(
                    f"Check raid details before posting: {exc}", ephemeral=True
                )
                return
            await self.cog.post_panel(interaction, event, destination_id=self.draft.get("channel_id"), ping_role_ids=self.draft.get("ping_role_ids", []))

        async def ask_delete(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            if self.draft["number"] not in self.saved_raid_ids:
                await interaction.response.send_message(
                    "This raid is not saved yet.", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=RaidDeleteConfirmation(
                self.cog, self.owner_id, self.draft["number"]
            ))

        async def preview_roster(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            event = read_raid(self.cog.raid_db_path(), self.draft["number"])
            if not event or event.get("archived_at"):
                await interaction.response.send_message(
                    "Choose an unconfirmed, active raid.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            from cogs import raid_sheets_v3
            try:
                await self.cog.sync_raid_sheets()
                selected = await __import__("asyncio").to_thread(
                    raid_sheets_v3.read_selected, self.cog.raid_db_path(),
                    event["raid_id"])
            except (ValueError, RuntimeError, sqlite3.Error, discord.HTTPException) as exc:
                await interaction.followup.send(f"Roster preview: {exc}", ephemeral=True)
                return
            if not selected:
                await interaction.followup.send(
                    "No visible selected players found. Check Selection, Show on Discord and instance assignments in the Sheet.",
                    ephemeral=True)
                return
            lines = [f"**{i}. {discord.utils.escape_markdown(entry[1])}** — {discord.utils.escape_markdown(entry[2])}"
                     for i, entry in enumerate(selected, 1)]
            preview = f"**Raid #{event['raid_id']} · Confirm roster?**\n" + "\n".join(lines)
            if len(preview) > 1900:
                preview = preview[:1850] + "\n…"
            from cogs import raid_acknowledgements as raid_ack
            announced = bool(raid_ack.announcement(self.cog.raid_db_path(),
                                                    event["raid_id"]))
            note = ("Ogre already announced this raid; extra pings disabled."
                    if announced else
                    "Publish without a ping, or ping each selected guild member once. "
                    "They react ✅ to acknowledge; Ogre removes them from the outstanding list.")
            await interaction.followup.send(
                preview + "\n\n" + note +
                "\nSign-ups stay open and Sheet selections remain editable.",
                view=RaidRosterPreview(self.cog, self.owner_id, event["raid_id"],
                                       selected, already_announced=announced),
                ephemeral=True)

        confirm_roster.callback = preview_roster
        async def open_manage(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message("Only owner.", ephemeral=True)
                return
            event = read_raid(self.cog.raid_db_path(), self.draft["number"])
            if not event:
                await interaction.response.send_message("Save the raid first.", ephemeral=True)
                return
            if not event.get("roster_published"):
                await interaction.response.send_message(
                    "Confirm the roster before managing attendance.", ephemeral=True)
                return
            from cogs.raid_signup import RaidManageView
            await interaction.response.send_message(
                f"Raid #{event['raid_id']} management", ephemeral=True,
                view=RaidManageView(self.cog, event["raid_id"]))

        async def open_advanced(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message(
                    "Only owner.", ephemeral=True
                )
                return
            await interaction.response.edit_message(
                view=RaidPosterView(
                    self.cog, self.owner_id, self.draft, page="advanced"
                )
            )

        async def back_to_schedule(interaction):
            if not self.authorised(interaction):
                await interaction.response.send_message(
                    "Only owner.", ephemeral=True
                )
                return
            await interaction.response.edit_message(
                view=RaidPosterView(
                    self.cog, self.owner_id, self.draft, page="schedule"
                )
            )

        manage.callback = open_manage
        title.callback = set_title
        banner.callback = set_banner
        softres.callback = set_link
        clear.callback = remove_link
        publish.callback = post
        check_link.callback = set_check_link
        clear_check.callback = remove_check_link
        delete.callback = ask_delete
        advanced.callback = open_advanced
        back.callback = back_to_schedule
        if self.page == "schedule":
            self.add_item(discord.ui.ActionRow(
                publish, confirm_roster, manage
            ))
            self.add_item(discord.ui.ActionRow(advanced))
        else:
            row.add_item(title)
            row.add_item(banner)
            row.add_item(softres)
            row.add_item(clear)
            self.add_item(row)
            self.add_item(discord.ui.ActionRow(check_link, clear_check))
            self.add_item(discord.ui.ActionRow(publish, back, delete))

    def add_command_footer(self):
        """Visible command reference at the very bottom of either poster page."""
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "**Raid commands**\n"
                "`/raid poster` Open setup · `/raid sync` Update Sheets · "
                "`/raid panel` Refresh panel\n"
                "`/raid confirm` Publish silently · "
                "`/raid reopen` Unpublish · `/raid archive` Complete/archive\n"
                "-# Extras: `/raid setup` direct setup · "
                "`/raid softres` link · `/raid gear-review` screenshot"
            )
        ))
