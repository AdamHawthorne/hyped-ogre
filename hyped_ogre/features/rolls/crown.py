"""Select the daily Lucky Gamer role holder without excluding Ogre's roll.

The daily coin remains awarded by its own ledger logic. The role is only for
a player who actually beats Ogre (or wins an earlier equal roll).
"""

def eligible_lucky_gamer(con, start, end):
    """First chronological highest roll, including Ogre; return a human or None."""
    row = con.execute(
        """SELECT user_id, value FROM rolls
           WHERE actor_type IN ('user', 'gamble', 'bot')
             AND rolled_at >= ? AND rolled_at < ?
           ORDER BY value DESC, rolled_at ASC, rowid ASC LIMIT 1""",
        (start, end),
    ).fetchone()
    if row is None or row[0] == 0:
        return None
    return {"user_id": row[0], "value": row[1]}
