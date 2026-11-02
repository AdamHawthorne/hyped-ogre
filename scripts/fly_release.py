"""Release guard for the existing Hyped app. Never creates or restores volumes."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone

APP = "hyped-diceman"
MAX_SNAPSHOT_AGE = 3600
DATA_PATHS = {
    "DB_PATH": "/data/bot.db",
    "RAID_DB_PATH": "/data/raid_signup.db",
    "LFG_DB_PATH": "/data/lfg_board.db",
    "COUNTDOWN_DB_PATH": "/data/countdown.db",
    "STREAM_DB_PATH": "/data/streams.db",
    "POLL_DB_PATH": "/data/polls.db",
}


def fly(*args, json_output=False):
    command = ["flyctl", *args, "-a", APP]
    if json_output:
        command.append("--json")
    # Consume all output; early-closing grep pipes previously caused exit 141.
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=90
    )
    return json.loads(result.stdout) if json_output else result.stdout


def inspect_live():
    volumes = fly("volumes", "list", json_output=True)
    volumes = [
        v
        for v in volumes
        if v.get("name") == "diceman_data" and v.get("state") == "created"
    ]
    machines = [
        m
        for m in fly("machine", "list", json_output=True)
        if m.get("state") != "destroyed"
    ]
    if len(volumes) != 1 or len(machines) != 1:
        raise ValueError("Expected one diceman_data volume and one bot Machine.")
    volume, machine = volumes[0], machines[0]
    mounts = machine.get("config", {}).get("mounts", [])
    if not any(
        m.get("volume") == volume["id"] and m.get("path") == "/data" for m in mounts
    ):
        raise ValueError(
            "The bot Machine is not attached to the expected /data volume."
        )
    if machine.get("state") != "started":
        raise ValueError("The existing bot Machine is not started.")
    return volume, machine


def recent_snapshot(rows, now):
    ready = []
    for row in rows:
        if row.get("status") != "created" or not re.fullmatch(
            r"vs_[A-Za-z0-9]+", row.get("id", "")
        ):
            continue
        try:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            age = (now - created).total_seconds()
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if 0 <= age <= MAX_SNAPSHOT_AGE:
            ready.append((created, row["id"]))
    return max(ready)[1] if ready else None


def ensure_snapshot(volume_id):
    def ready():
        return recent_snapshot(
            fly("volumes", "snapshots", "list", volume_id, json_output=True),
            datetime.now(timezone.utc),
        )

    snapshot = ready()
    if snapshot:
        return snapshot
    print(fly("volumes", "snapshots", "create", volume_id), end="")
    for _ in range(18):
        time.sleep(10)
        snapshot = ready()
        if snapshot:
            return snapshot
    raise ValueError(
        "Snapshot not ready after three minutes; deployment has not started."
    )


def prepare(destination):
    config = tomllib.loads(Path("fly.toml").read_text(encoding="utf-8"))
    if config.get("app") != APP or config.get("mounts") != [
        {"source": "diceman_data", "destination": "/data"}
    ]:
        raise ValueError("Unexpected Fly app or persistent mount configuration.")
    if any(
        config.get("env", {}).get(key) != value for key, value in DATA_PATHS.items()
    ):
        raise ValueError(
            "Persistent database paths changed; review the migration before deploying."
        )
    if not os.environ.get("FLY_API_TOKEN"):
        raise ValueError("FLY_API_TOKEN secret is missing.")
    volume, machine = inspect_live()
    image = machine.get("config", {}).get("image", "")
    if (
        not re.fullmatch(
            r"registry\.fly\.io/hyped-diceman(?::[A-Za-z0-9_.-]+)?(?:@sha256:[0-9a-f]{64})?",
            image,
        )
        or image == "registry.fly.io/hyped-diceman"
    ):
        raise ValueError("Could not identify the current rollback image.")
    snapshot = ensure_snapshot(volume["id"])
    record = {
        "volume": volume["id"],
        "machine": machine["id"],
        "rollback_image": image,
        "snapshot": snapshot,
    }
    destination.write_text(json.dumps(record), encoding="utf-8")
    summary = (
        f"## Hyped Ogre release\n\nCommit: `{os.environ.get('GITHUB_SHA', 'local')}`\n\n"
        f"Volume: `{volume['id']}`\n\nCompleted snapshot: `{snapshot}`\n\n"
        f"Rollback image: `{image}`\n\n"
        f"Code rollback, retaining data: `flyctl deploy -a {APP} --image {image} --ha=false`\n"
    )
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary)


def verify(source):
    previous = json.loads(source.read_text(encoding="utf-8"))
    volume, machine = inspect_live()
    if volume["id"] != previous["volume"] or machine["id"] != previous["machine"]:
        raise ValueError(
            "Machine or data volume changed unexpectedly; inspect Fly before further changes."
        )
    print(f"Machine {machine['id']} started with the same /data volume {volume['id']}.")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(
                "\nDeployment completed; the same Machine is started with the same data volume. Check a harmless Discord interaction next.\n"
            )


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"prepare", "verify"}:
        raise ValueError("Use fly_release.py prepare|verify RECORD_PATH.")
    (prepare if args[0] == "prepare" else verify)(Path(args[1]))


if __name__ == "__main__":
    main()
