# Hyped Ogre operational safety

## Current deployment

- GitHub repository: `AdamHawthorne/hyped-ogre`
- Fly.io application: `hyped-diceman`
- Working Docker entry point: `python rollf.py`
- Database mount: `diceman_data` at `/data`
- Timezone: `Europe/London`

**No production deployments should run automatically during the code restructuring.** Pushing a feature branch or merging a pull request is not a request to restart the bot.

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
