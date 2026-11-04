"""Post the leader invite instructions once, 15 minutes before each raid.

Runs independently of Google Sheets and checks the existing approved roster.
The durable claim is made *before* Discord send to avoid duplicate invitations
on restarts or ambiguous HTTP failures. No Discord user/role pings are sent.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord

from cogs.raid_schedule import active_panels, database, read_public_roster
from hyped_ogre.features.raids.announcer import leader_invite_commands

LOGGER = logging.getLogger(__name__)
INVITE_LEAD_MINUTES = 15


def invite_text(raid_id, commands):
    """Plain Discord message: copyable lines, never a ping or an embed."""
    return "Raid starting, good luck.\n\n" + "\n".join(commands)


def due_invites(path, *, now=None):
    """Eligible raids between their T-15 mark and scheduled start (UTC safe)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for event in active_panels(path):
        if (not event.get("roster_published") or event.get("archived_at")
                or not event.get("panel_channel_id")
                or not event.get("raid_at")):
            continue
        start = datetime.fromisoformat(event["raid_at"]).astimezone(timezone.utc)
        if not start - timedelta(minutes=INVITE_LEAD_MINUTES) <= now < start:
            continue
        # Post only after Ogre has actually issued the separate V1 roster
        # confirmation. A selected draft is not enough.
        with database(path) as con:
            announcement = con.execute(
                "SELECT 1 FROM raid_ack_announcements "
                "WHERE raid_id=? AND retracted_at IS NULL AND deleted_at IS NULL",
                (event["raid_id"],),
            ).fetchone()
        if not announcement:
            continue
        selected = [
            (member, name, assignment, klass, role, spec)
            for name, assignment, klass, role, spec, member
            in read_public_roster(path, event["raid_id"], include_identity=True)
        ]
        commands = leader_invite_commands(path, event, selected)
        if commands:
            yield event, commands


def claim_invite(path, event, now):
    """Exactly one durable send attempt for each raid, including after restart."""
    with database(path) as con:
        row = con.execute(
            """
            INSERT OR IGNORE INTO raid_invite_posts(raid_id, claimed_at)
            SELECT raid_id, ? FROM raids
            WHERE raid_id=? AND roster_published=1 AND archived_at IS NULL
              AND panel_channel_id=?
              AND EXISTS (
                SELECT 1 FROM raid_ack_announcements
                WHERE raid_ack_announcements.raid_id=raids.raid_id
                  AND retracted_at IS NULL AND deleted_at IS NULL
              )
            """,
            (now.astimezone(timezone.utc).isoformat(), event["raid_id"],
             event["panel_channel_id"]),
        )
        return row.rowcount == 1


async def send_due_invites(bot, path, guild_id, *, now=None):
    """Called by a lightweight scheduler, independent of Sheets availability."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sent = []
    for event, commands in due_invites(path, now=now):
        channel_id = int(event["panel_channel_id"])
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
                LOGGER.warning("Raid #%s invite channel is outside this guild",
                               event["raid_id"])
                continue
            me = channel.guild.me
            if me is None or not channel.permissions_for(me).send_messages:
                LOGGER.warning("Raid #%s invites missing channel permission",
                               event["raid_id"])
                continue
            if not claim_invite(path, event, now):
                continue
            # Keep the claim even if the HTTP response is lost; Discord might
            # have delivered the message, so a retry could post duplicates.
            message = await channel.send(
                content=invite_text(event["raid_id"], commands),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            with database(path) as con:
                con.execute(
                    "UPDATE raid_invite_posts SET posted_at=?, message_id=? "
                    "WHERE raid_id=?",
                    (datetime.now(timezone.utc).isoformat(), message.id,
                     event["raid_id"]),
                )
            sent.append(event["raid_id"])
        except (discord.HTTPException, ValueError) as exc:
            LOGGER.warning("Raid #%s invite announcement failed: %s",
                           event["raid_id"], exc)
    return sent
