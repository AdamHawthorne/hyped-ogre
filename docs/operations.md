# Hyped Ogre operational safety

## Current deployment

- GitHub repository: `AdamHawthorne/hyped-ogre`
- Fly.io application: `hyped-diceman`
- New Docker entry point after the next release: `python rollogre.py`
- Database mount: `diceman_data` at `/data`
- Timezone: `Europe/London`

**Deployment is manual only.** Merging the entrypoint rename does not restart the bot. The running Fly machine continues using its previous image until an explicitly authorised release.

## Inspect production (read-only)

From PowerShell, after installing and authenticating the Fly CLI:

```powershell
cd "C:\Users\Adam\hyped-ogre"
fly status -a hyped-diceman
fly logs -a hyped-diceman
```

These commands inspect status and logs; they do not deploy. Verify the bot responds normally in Discord and that important buttons remain usable.

## Before any deployment

1. Confirm the branch and the exact commit to deploy.
2. Ensure automated checks, including Docker build, pass.
3. Make and verify a restorable backup of the live Fly volume databases; a Git repository is **not** a backup of production SQLite data.
4. Record the previous working image/release for rollback.
5. Confirm Fly secrets, the `/data` mount and expected app configuration are unchanged.
6. Schedule the cutover away from UK midnight, planned raids and other sensitive guild activity.
7. Deploy only after authorisation, then verify status, logs, Discord interactions and existing records.

## Rollback

If a structural release breaks the bot, restore the previous working application image while **retaining the live `/data` volume**. Do not casually revert the database if new valid data was written. If a migration changed the schema, use the tested rollback or forward-repair procedure for that migration.


## Rollogre release procedure

The executable is renamed to `rollogre.py`, but the complete bot and its
Discord commands remain in `hyped_ogre/bot.py`. The Docker `COPY` and
`CMD`, safety workflow, tests, docs and local commands change together.

**Unchanged:** Fly app `hyped-diceman`, region `lhr`, volume
`diceman_data` at `/data`, the five existing SQLite paths, Discord bot
identity, slash commands, message/component IDs, background jobs and secrets.
Legacy RollF licensing, static CSS and historical log filenames are not part
of the executable rename.

A code merge alone does not deploy anything.

### Prepare a manual GitHub deployment

1. Use `fly auth login` to sign into your existing Fly account. Generate an
   app-scoped, short-lived deploy token (from your project directory) with
   `fly tokens create deploy -a hyped-diceman -x 720h`. **Never commit,
   paste into chat, or display this secret in a workflow log.**
2. Add the token as `FLY_API_TOKEN` in GitHub repository **Settings >
   Secrets and variables > Actions**, or as a secret of the `production`
   environment. Configure environment protection and required reviewers
   where supported by your account.
3. Verify the **main** branch safety workflow is green and copy the full
   40-character reviewed commit SHA. For this first migration deployment,
   schedule a quiet time away from UK midnight, a raid and other timed
   announcements. Check there is only one intended bot Machine.
4. Inspect production without changing it:

```powershell
fly status -a hyped-diceman
fly volumes list -a hyped-diceman
fly releases --image -a hyped-diceman
fly secrets list -a hyped-diceman
```

Record the exact volume ID (`vol_...`) for **diceman_data** and the
previous working release image from `fly releases --image`. Ensure the
volume is attached and the existing five SQLite files are present.

5. **Deliberately request a fresh Fly snapshot** of that volume, then
   verify the snapshot reaches `created` status. Fly snapshots are
   asynchronous, so recording a scheduled snapshot is not enough.

```powershell
fly volumes snapshots create <volume-id> -a hyped-diceman
fly volumes snapshots list <volume-id> -a hyped-diceman
```

Record the completed `vs_...` snapshot ID. Fly snapshots are block-level
recovery points. For long-term recovery, also maintain a tested off-platform
copy using SQLite's consistent backup API; do not copy a live SQLite database
file without accounting for WAL transactions. The workflow will **not**
create, restore or delete the persistent volume.

6. Open **GitHub > Actions > Deploy Hyped Ogre (manual only) > Run workflow**.
   Select `main`; provide the full commit SHA, volume ID, completed snapshot
   ID, previous working image and the exact phrase `DEPLOY HYPED OGRE`.
   It fails closed if the SHA, image, volume or completed snapshot checks fail.

The workflow reruns Python regressions and Docker launcher smoke checks
before calling `flyctl deploy -a hyped-diceman --remote-only --ha=false`.
GitHub Actions installs the CLI executable as `flyctl`; the shorter `fly`
command used in a local terminal may be an alias and is not available there.
Volume, snapshot and release listings are saved to temporary files before
matching. This prevents `grep -q` from closing a streaming Fly command early
under `pipefail` (exit 141), while preserving CLI failures. Missing backup
evidence or a failed Fly command still stops the workflow before deployment.
It is not triggered by a PR, a push or a merge. Do not run a second
production bot with the same Discord token.

### Verify the release

Check `fly status -a hyped-diceman`, `fly logs -a hyped-diceman`,
`fly volumes list -a hyped-diceman` and
`fly releases --image -a hyped-diceman`. Confirm the same persistent volume
is attached and one bot instance is connected to Discord. Test `/stats`
and harmless interactions on existing roll, raid, LFG, Forever, countdown,
stream and suggestions panels. Avoid spending real gamble coins or posting
duplicate announcements just to test the bot. Then check the next reminder,
Twitch refresh and UK midnight roll settlement.

If the new bot fails, preserve the logs. You can restore the **old code**
with the recorded working Fly image:

```powershell
fly deploy -a hyped-diceman --image <previous-working-image> --ha=false
```

Do not delete `/data`. Code rollback is not a database rollback and does
not automatically revert schema changes or recently written data. Restoring
the snapshot is a separate, potentially destructive decision and should
never be done casually.

References: [Fly volume snapshots](https://fly.io/docs/volumes/snapshots/),
[Fly release images](https://fly.io/docs/blueprints/using-the-fly-docker-registry/)
and [Fly GitHub Actions setup](https://fly.io/docs/launch/continuous-deployment-with-github-actions/).
