"""Rebuild competitive scores from immutable, already-saved roll history.

A player has one free roll per UK day. An optional gamble contributes only
when higher, replacing that day's effective score rather than adding a roll.
This is deliberately idempotent: a redeploy/restart can recover an already
saved gamble without consuming another coin or rewriting the wager ledger.
"""


def reconcile_scores(con):
    """Repair cached daily, lifetime and best scores, including today's gambles.

    The canonical rolls table is not modified. Daily roll count remains one
    even when a gamble raises its score. Historical coin-award rows stay intact.
    """
    con.execute("""
        INSERT INTO daily_scores (user_id, roll_date, score, rolls)
        SELECT base.user_id, base.roll_date,
               MAX(base.value, COALESCE(extra.value, base.value)), 1
        FROM rolls AS base
        LEFT JOIN rolls AS extra
          ON extra.user_id=base.user_id
         AND extra.roll_date=base.roll_date
         AND extra.actor_type='gamble'
        WHERE base.actor_type='user' AND base.roll_date IS NOT NULL
        ON CONFLICT(user_id, roll_date) DO UPDATE SET
          score=excluded.score, rolls=excluded.rolls
    """)
    con.execute("""
        INSERT INTO user_scores (user_id, score, rolls, best)
        SELECT user_id, SUM(score), SUM(rolls), MAX(score)
        FROM daily_scores
        WHERE rolls > 0
        GROUP BY user_id
        ON CONFLICT(user_id) DO UPDATE SET
          score=excluded.score, rolls=excluded.rolls, best=excluded.best
    """)
