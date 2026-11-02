# Hyped Ogre deployment

Updates to `main` deploy automatically to the existing `hyped-diceman` Fly app.
The app keeps its `diceman_data` volume at `/data` and its persistent SQLite databases.
The boot command remains `python rollogre.py`; the bot briefly restarts per release.

## Normal update

1. Commit and push a change to `main`, or merge a reviewed PR into `main`.
2. Open **GitHub > Actions > Deploy Hyped Ogre**.
3. The workflow runs the shared Linux/Windows tests, builds Docker and checks
   the actual runtime. Checks run once for this main commit, before deployment.
4. It finds the existing Machine and attached `diceman_data` volume, records
   the current image for rollback and selects a completed snapshot no older
   than one hour. Rapid test pushes reuse that recent snapshot. If needed it
   requests a new snapshot and waits up to three minutes for completion.
5. It runs `flyctl deploy -a hyped-diceman --remote-only --ha=false`, then
   verifies that the same Machine is started with the same volume mounted.

The workflow uses the existing `FLY_API_TOKEN` secret in the `production`
environment/repository. Never put the token into source or chat. A missing
secret, failed test, unexpected mount/database path or unavailable completed
backup stops the release. Jobs are serialised, and an in-progress deployment
is not cancelled by a newer push. A queued superseded push may be replaced
by the newest queued push.

The historical workflow filename is still `deploy-manual.yml`, keeping saved
links valid. The workflow title is **Deploy Hyped Ogre**. Use **Run workflow**
with `main` for a manual retry; there are no five input fields. Starting it on
another branch skips deployment. PR checks and ordinary feature-branch pushes
do not deploy. `git pull` only updates your PC's working copy.

The workflow summary records the source commit, Machine/volume evidence,
completed snapshot and previous image. Fly status is not proof that every
Discord interaction works; test a harmless command after a release.

## Verify the live bot

```powershell
fly status -a hyped-diceman
fly volumes list -a hyped-diceman
fly releases --image -a hyped-diceman
fly logs -a hyped-diceman
```

Confirm one intended bot Machine is started, the expected volume remains
attached and the bot responds in Discord. Test `/stats` and existing panels
without spending real gamble coins or creating duplicate announcements.
Check the next scheduled reset, raid reminder and Twitch refresh.

## Persistent data and backups

The preserved paths are `/data/bot.db`, `/data/raid_signup.db`,
`/data/lfg_board.db`, `/data/countdown.db`, `/data/streams.db` and `/data/polls.db`.
Deployment never creates, deletes, restores or replaces the volume.
Automatic backup selection happens in `scripts/fly_release.py` using Fly's
JSON output, not text pipelines that can cause SIGPIPE/exit 141.

Fly snapshots are block-level recovery points with separate retention. For
long-term recovery also keep a tested off-platform copy made with SQLite's
online backup API, including committed WAL transactions. GitHub code is not
a backup of the bot's live databases. Before schema/data migrations, prepare
and test a migration-specific backup and recovery plan.

To request an extra snapshot manually:

```powershell
fly volumes snapshots create <volume-id> -a hyped-diceman
fly volumes snapshots list <volume-id> -a hyped-diceman
```

Wait for the requested snapshot to show `created` before relying on it.

## Failure and rollback

If checks or the backup guard fail, the deployment step is skipped and the
existing bot stays on its current image. Fix the cause, push/merge the change,
and the new main commit starts a fresh run automatically. Rerunning an old
workflow uses its old commit, so prefer the new run for code fixes.

If deployment itself fails, inspect its logs and the Fly state before making
more changes. The run summary contains the previous working image. To restore
that code while retaining live data:

```powershell
fly deploy -a hyped-diceman --image <previous-working-image> --ha=false
```

Code rollback does not undo database writes or schema changes. Do not restore
a volume snapshot casually over new valid data. No automatic database restore
or automatic code rollback is performed by this workflow.

References: [Fly volume snapshots](https://fly.io/docs/volumes/snapshots/),
[Fly GitHub Actions](https://fly.io/docs/launch/continuous-deployment-with-github-actions/).
