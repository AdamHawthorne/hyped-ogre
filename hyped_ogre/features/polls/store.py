"""Transactional poll state. Ballots contain no Discord IDs or names."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


class PollError(ValueError):
    """A safe message that can be shown privately to the requester."""


def options_from_text(text):
    choices = [line.strip() for line in text.splitlines() if line.strip()]
    if not 2 <= len(choices) <= 10 or any(len(v) > 80 for v in choices):
        raise PollError("Use 2–10 options, one per line, up to 80 characters each.")
    if len({v.casefold() for v in choices}) != len(choices):
        raise PollError("Each option needs a different name.")
    return choices


def deadline_from_text(text, zone="Europe/London", *, now=None):
    if not text.strip():
        return None
    now = datetime.now(UTC) if now is None else now
    relative = re.fullmatch(r"([1-9][0-9]*)\s*([mhd])", text.strip().lower())
    if relative:
        seconds = int(relative[1]) * {"m": 60, "h": 3600, "d": 86400}[relative[2]]
        if not 60 <= seconds <= 365 * 86400:
            raise PollError(
                "Choose a deadline at least one minute and at most one year ahead."
            )
        value = now + timedelta(seconds=seconds)
    else:
        try:
            value = datetime.fromisoformat(text.strip())
        except ValueError as exc:
            raise PollError(
                "Use 30m, 2h, 7d or YYYY-MM-DD HH:MM (UK time). Blank means manual close."
            ) from exc
        if value.tzinfo is None:
            local = ZoneInfo(zone)
            candidates = [value.replace(tzinfo=local, fold=f) for f in (0, 1)]
            valid = [
                v
                for v in candidates
                if v.astimezone(UTC).astimezone(local).replace(tzinfo=None) == value
            ]
            if not valid:
                raise PollError(
                    "That local time does not exist because the clocks change."
                )
            if len({v.utcoffset() for v in valid}) > 1:
                raise PollError(
                    "That time occurs twice. Include +00:00 or +01:00 to choose the hour."
                )
            value = valid[0]
    if not 60 <= (value - now).total_seconds() <= 365 * 86400:
        raise PollError(
            "Choose a deadline at least one minute and at most one year ahead."
        )
    return int(value.timestamp())


SCHEMA = """
CREATE TABLE polls (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
 creator_id INTEGER NOT NULL, admin_channel_id INTEGER NOT NULL, admin_message_id INTEGER,
 public_channel_id INTEGER, public_message_id INTEGER, results_message_id INTEGER,
 question TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', options TEXT NOT NULL,
 visibility TEXT NOT NULL DEFAULT 'hidden' CHECK(visibility IN ('hidden','participation','live')),
 multiple INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','open','closed','cancelled')),
 created_at INTEGER NOT NULL, opened_at INTEGER, closes_at INTEGER, closed_at INTEGER,
 voter_secret TEXT, final_counts TEXT, final_voters INTEGER,
 public_dirty INTEGER NOT NULL DEFAULT 0, admin_dirty INTEGER NOT NULL DEFAULT 1,
 revision INTEGER NOT NULL DEFAULT 0, UNIQUE(guild_id,id)
);
CREATE TABLE ballots (
 guild_id INTEGER NOT NULL, poll_id INTEGER NOT NULL,
 voter_key TEXT NOT NULL, option_id INTEGER NOT NULL,
 PRIMARY KEY(guild_id,poll_id,voter_key,option_id),
 FOREIGN KEY(guild_id,poll_id) REFERENCES polls(guild_id,id) ON DELETE CASCADE
);
CREATE INDEX poll_deadlines ON polls(state,closes_at);
"""


class PollStore:
    def __init__(self, path):
        self.path = Path(path)

    def initialise(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise PollError("Poll database needs a newer bot release.")
            if (
                version == 1
                and not con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ballots'"
                ).fetchone()
            ):
                raise PollError("This is not a poll database. Refusing to change it.")
            if version == 0:
                if con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone():
                    raise PollError(
                        "POLL_DB_PATH points to an existing non-poll database. Refusing to change it."
                    )
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        con.execute(statement)
                con.execute("PRAGMA user_version=1")
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connection(self, *, write=False):
        con = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            if not write:
                con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield con
            if write:
                con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _row(con, guild_id, poll_id):
        row = con.execute(
            "SELECT * FROM polls WHERE guild_id=? AND id=?", (guild_id, poll_id)
        ).fetchone()
        if not row:
            raise PollError("Ogre cannot find that poll in this server.")
        return dict(row)

    @staticmethod
    def _key(row, user_id):
        return hmac.new(
            bytes.fromhex(row["voter_secret"]),
            f"{row['guild_id']}:{user_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _content(question, options, description):
        if not 1 <= len(question.strip()) <= 256 or len(description) > 1000:
            raise PollError(
                "Use a question of 1–256 characters and details up to 1000 characters."
            )
        return question.strip(), options_from_text(options), description.strip()

    def create(
        self,
        guild_id,
        creator_id,
        admin_channel_id,
        question,
        options="Yes\nNo",
        description="",
        closes_at=None,
    ):
        question, choices, description = self._content(question, options, description)
        if closes_at is not None and closes_at <= int(time.time()):
            raise PollError("The deadline has already passed.")
        with self.connection(write=True) as con:
            return con.execute(
                "INSERT INTO polls(guild_id,creator_id,admin_channel_id,question,description,options,created_at,closes_at,voter_secret) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    guild_id,
                    creator_id,
                    admin_channel_id,
                    question,
                    description,
                    json.dumps(choices),
                    int(time.time()),
                    closes_at,
                    secrets.token_hex(32),
                ),
            ).lastrowid

    def get(self, guild_id, poll_id):
        with self.connection() as con:
            row = self._row(con, guild_id, poll_id)
            row["options"] = json.loads(row["options"])
            if row["state"] == "closed":
                row["counts"] = json.loads(row["final_counts"])
                row["voters"] = row["final_voters"]
            else:
                row["counts"] = [0] * len(row["options"])
                for option, count in con.execute(
                    "SELECT option_id,COUNT(*) FROM ballots WHERE guild_id=? AND poll_id=? GROUP BY option_id",
                    (guild_id, poll_id),
                ):
                    row["counts"][option] = count
                row["voters"] = con.execute(
                    "SELECT COUNT(DISTINCT voter_key) FROM ballots WHERE guild_id=? AND poll_id=?",
                    (guild_id, poll_id),
                ).fetchone()[0]
            # Never pass the ballot key material to the UI or logs.
            row.pop("voter_secret")
            return row

    def edit(self, guild_id, poll_id, question, options, description, closes_at):
        question, choices, description = self._content(question, options, description)
        with self.connection(write=True) as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] != "draft":
                raise PollError(
                    "Questions and options are locked after posting. Create a new poll to change them."
                )
            con.execute(
                "UPDATE polls SET question=?,options=?,description=?,closes_at=?,revision=revision+1,admin_dirty=1 WHERE guild_id=? AND id=?",
                (
                    question,
                    json.dumps(choices),
                    description,
                    closes_at,
                    guild_id,
                    poll_id,
                ),
            )

    def configure(self, guild_id, poll_id, key, value):
        if key not in {"public_channel_id", "visibility", "multiple", "closes_at"}:
            raise PollError("Unknown poll setting.")
        if key == "visibility" and value not in {"hidden", "participation", "live"}:
            raise PollError("Choose hidden, participation or live results.")
        if key == "multiple" and type(value) is not bool:
            raise PollError("Choose single or multiple voting.")
        if key == "public_channel_id" and (type(value) is not int or value <= 0):
            raise PollError("Choose a destination channel.")
        now = int(time.time())
        with self.connection(write=True) as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] not in {"draft", "open"} or (
                row["state"] == "open" and row["closes_at"] and row["closes_at"] <= now
            ):
                raise PollError("That poll has closed.")
            if row["state"] != "draft" and key in {"public_channel_id", "multiple"}:
                raise PollError(
                    "The destination and voting type are locked after posting."
                )
            if key == "closes_at" and value is not None and value <= now:
                raise PollError("Choose a future deadline or close the poll manually.")
            con.execute(
                f"UPDATE polls SET {key}=?,revision=revision+1,admin_dirty=1,public_dirty=1 WHERE guild_id=? AND id=?",
                (value, guild_id, poll_id),
            )

    def open(self, guild_id, poll_id, *, now=None):
        now = int(time.time()) if now is None else now
        with self.connection(write=True) as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] != "draft" or not row["public_channel_id"]:
                raise PollError("Choose a destination for this draft first.")
            if row["closes_at"] and row["closes_at"] <= now:
                raise PollError("The deadline has already passed.")
            con.execute(
                "UPDATE polls SET state='open',opened_at=?,revision=revision+1,admin_dirty=1,public_dirty=1 WHERE guild_id=? AND id=?",
                (now, guild_id, poll_id),
            )

    def vote(self, guild_id, poll_id, user_id, option=None, *, remove=False, now=None):
        if type(user_id) is not int or user_id <= 0:
            raise PollError("Only a Discord member can vote.")
        with self.connection(write=True) as con:
            now = int(time.time()) if now is None else now
            row = self._row(con, guild_id, poll_id)
            if row["state"] != "open" or (
                row["closes_at"] is not None and now >= row["closes_at"]
            ):
                raise PollError("That poll is closed. Ogre not taking more votes.")
            choices = json.loads(row["options"])
            if not remove and (
                type(option) is not int or not 0 <= option < len(choices)
            ):
                raise PollError("That voting option is not valid.")
            key = self._key(row, user_id)
            if remove:
                con.execute(
                    "DELETE FROM ballots WHERE guild_id=? AND poll_id=? AND voter_key=?",
                    (guild_id, poll_id, key),
                )
            elif row["multiple"]:
                deleted = con.execute(
                    "DELETE FROM ballots WHERE guild_id=? AND poll_id=? AND voter_key=? AND option_id=?",
                    (guild_id, poll_id, key, option),
                ).rowcount
                if not deleted:
                    con.execute(
                        "INSERT INTO ballots VALUES(?,?,?,?)",
                        (guild_id, poll_id, key, option),
                    )
            else:
                con.execute(
                    "DELETE FROM ballots WHERE guild_id=? AND poll_id=? AND voter_key=?",
                    (guild_id, poll_id, key),
                )
                con.execute(
                    "INSERT INTO ballots VALUES(?,?,?,?)",
                    (guild_id, poll_id, key, option),
                )
            con.execute(
                "UPDATE polls SET revision=revision+1,public_dirty=CASE WHEN visibility='hidden' THEN public_dirty ELSE 1 END,admin_dirty=CASE WHEN visibility='hidden' THEN admin_dirty ELSE 1 END WHERE guild_id=? AND id=?",
                (guild_id, poll_id),
            )
            return [
                r[0]
                for r in con.execute(
                    "SELECT option_id FROM ballots WHERE guild_id=? AND poll_id=? AND voter_key=? ORDER BY option_id",
                    (guild_id, poll_id, key),
                )
            ]

    def mine(self, guild_id, poll_id, user_id):
        with self.connection() as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] != "open":
                raise PollError(
                    "The poll is closed; individual ballot records have been cleared."
                )
            return [
                r[0]
                for r in con.execute(
                    "SELECT option_id FROM ballots WHERE guild_id=? AND poll_id=? AND voter_key=? ORDER BY option_id",
                    (guild_id, poll_id, self._key(row, user_id)),
                )
            ]

    def close(self, guild_id, poll_id, *, due_only=False, now=None):
        now = int(time.time()) if now is None else now
        with self.connection(write=True) as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] == "closed":
                return False
            if row["state"] != "open":
                raise PollError("Only an open poll can be closed.")
            if due_only and (row["closes_at"] is None or row["closes_at"] > now):
                return False
            counts = [0] * len(json.loads(row["options"]))
            for option, count in con.execute(
                "SELECT option_id,COUNT(*) FROM ballots WHERE guild_id=? AND poll_id=? GROUP BY option_id",
                (guild_id, poll_id),
            ):
                counts[option] = count
            voters = con.execute(
                "SELECT COUNT(DISTINCT voter_key) FROM ballots WHERE guild_id=? AND poll_id=?",
                (guild_id, poll_id),
            ).fetchone()[0]
            con.execute(
                "UPDATE polls SET state='closed',closed_at=?,final_counts=?,final_voters=?,voter_secret=NULL,revision=revision+1,public_dirty=1,admin_dirty=1 WHERE guild_id=? AND id=?",
                (now, json.dumps(counts), voters, guild_id, poll_id),
            )
            con.execute(
                "DELETE FROM ballots WHERE guild_id=? AND poll_id=?",
                (guild_id, poll_id),
            )
            return True

    def cancel(self, guild_id, poll_id):
        with self.connection(write=True) as con:
            row = self._row(con, guild_id, poll_id)
            if row["state"] != "draft":
                raise PollError("Use Close poll for a poll that has been posted.")
            con.execute(
                "UPDATE polls SET state='cancelled',voter_secret=NULL,revision=revision+1,admin_dirty=1 WHERE guild_id=? AND id=?",
                (guild_id, poll_id),
            )

    def message(self, guild_id, poll_id, kind, message_id):
        column = {
            "admin": "admin_message_id",
            "public": "public_message_id",
            "results": "results_message_id",
        }[kind]
        with self.connection(write=True) as con:
            self._row(con, guild_id, poll_id)
            con.execute(
                f"UPDATE polls SET {column}=? WHERE guild_id=? AND id=?",
                (message_id, guild_id, poll_id),
            )
            if kind == "results":
                con.execute(
                    "UPDATE polls SET public_dirty=1,admin_dirty=1,revision=revision+1 WHERE guild_id=? AND id=?",
                    (guild_id, poll_id),
                )

    def clean(self, guild_id, poll_id, kind, revision):
        column = {"admin": "admin_dirty", "public": "public_dirty"}[kind]
        with self.connection(write=True) as con:
            con.execute(
                f"UPDATE polls SET {column}=0 WHERE guild_id=? AND id=? AND revision=?",
                (guild_id, poll_id, revision),
            )

    def pending(self, guild_id, *, now=None):
        now = int(time.time()) if now is None else now
        with self.connection() as con:
            return [
                r[0]
                for r in con.execute(
                    "SELECT id FROM polls WHERE guild_id=? AND (admin_dirty=1 OR public_dirty=1 OR (state='open' AND closes_at<=?)) ORDER BY id LIMIT 100",
                    (guild_id, now),
                )
            ]

    def recent(self, guild_id):
        with self.connection() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT id,question,state,public_channel_id,closes_at FROM polls WHERE guild_id=? ORDER BY id DESC LIMIT 20",
                    (guild_id,),
                )
            ]
