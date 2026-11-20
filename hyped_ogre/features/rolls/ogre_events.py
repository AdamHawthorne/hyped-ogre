"""Durable Ogre rest days and one-coin reaction drops."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
DROP_EMOJI_NAME = "ogrecoin"


def previous_day_ogre_won(con, now):
    """The same highest-first, earliest-first tie rule as daily coin awards."""
    day = now.astimezone(UK).date() - timedelta(days=1)
    start = int(datetime.combine(day, time.min, UK).timestamp())
    end = int(datetime.combine(day + timedelta(days=1), time.min, UK).timestamp())
    winner = con.execute(
        """SELECT user_id FROM rolls
           WHERE actor_type IN ('user', 'gamble', 'bot')
             AND value BETWEEN 1 AND 100
             AND rolled_at >= ? AND rolled_at < ?
           ORDER BY value DESC, rolled_at ASC, rowid ASC LIMIT 1""",
        (start, end),
    ).fetchone()
    return winner is not None and winner[0] == 0


def reserve_ogre_event(con, guild_id, day, kind):
    """At most one rest/drop event per guild-day, including after restart."""
    result = con.execute(
        """INSERT OR IGNORE INTO roll_ogre_events (guild_id, roll_date, kind)
           VALUES (?, ?, ?)""",
        (guild_id, day, kind),
    )
    return result.rowcount == 1


def register_drop(con, guild_id, day, message_id):
    """Reserve one existing Ogre coin before anyone can claim the drop.

    The drop row is the debit, including while the coin is still unclaimed.
    Never overwrite a claim or allow a negative Ogre balance.
    """
    from hyped_ogre.features.rolls.coins import coin_total

    con.execute("SAVEPOINT ogre_coin_drop")
    try:
        if coin_total(con, guild_id, 0) < 1:
            con.execute("ROLLBACK TO ogre_coin_drop")
            con.execute("RELEASE ogre_coin_drop")
            return False
        inserted = con.execute(
            """INSERT OR IGNORE INTO roll_coin_drops
               (guild_id, roll_date, message_id) VALUES (?, ?, ?)""",
            (guild_id, day, message_id),
        ).rowcount == 1
        con.execute("RELEASE ogre_coin_drop")
        return inserted
    except Exception:
        con.execute("ROLLBACK TO ogre_coin_drop")
        con.execute("RELEASE ogre_coin_drop")
        raise


def claim_drop(con, guild_id, message_id, user_id, username, now=None):
    """Only the first eligible reaction wins. Atomic even on repeated events."""
    if user_id <= 0:
        return False
    now = now or datetime.now(UK)
    result = con.execute(
        """UPDATE roll_coin_drops
           SET claimed_by=?, claimed_name=?, claimed_at=?
           WHERE guild_id=? AND message_id=? AND claimed_by IS NULL""",
        (user_id, username, int(now.timestamp()), guild_id, message_id),
    )
    return result.rowcount == 1


def is_unclaimed_drop(con, guild_id, message_id):
    return con.execute(
        """SELECT 1 FROM roll_coin_drops
           WHERE guild_id=? AND message_id=? AND claimed_by IS NULL""",
        (guild_id, message_id),
    ).fetchone() is not None
