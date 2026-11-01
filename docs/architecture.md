# Hyped Ogre architecture and refactoring plan

## Current state

The production Discord bot starts from `rollf.py`, which still coordinates configuration, roll processing and startup. Most other functionality lives in top-level `cogs/` modules. The Dockerfile copies `rollf.py`, `cogs/`, `hyped_ogre/`, `assets/` and `web/` and launches `python rollf.py`.

**Migrated foundations:** the Ogre voice bank lives in `hyped_ogre/features/voice/messages.py`, and shared Discord emoji/role helpers live in `hyped_ogre/core/emojis.py`. The old `cogs/ogre_voice.py` and `cogs/emojis.py` paths remain compatibility wrappers exporting the same functions and shared values. This preserves existing commands, posted Discord component identifiers, and import paths. **LFG helper migration:** sorting and pagination implementations now live in `hyped_ogre/features/lfg/`. The legacy `cogs/lfg_sort.py` and `cogs/lfg_pagination.py` modules remain compatibility wrappers, so the live LFG board imports, component limits, saved group ordering and database behaviour remain unchanged. **Character and raid references:** shared Forever race/class/starting-zone data lives in `hyped_ogre/features/characters/races.py`, and conservative raid-buff coverage logic lives in `hyped_ogre/features/raids/buffs.py`. The original `cogs/forever_races.py` and `cogs/forever_raid_buffs.py` modules remain compatibility wrappers, preserving existing LFG restrictions, Sheets coverage, group assignment and imports. Other folders below remain targets for later changes.

## Intended package layout

```text
hyped_ogre/
  __init__.py
  bot.py                   # eventual application startup
  config.py                # environment parsing
  core/
    database.py            # shared persistence primitives
    discord.py             # common Discord utilities
  features/
    rolls/                 # roll panels, coins, gamble, reactions
    raids/                 # signup, schedules, posters, sheets, attendance
    characters/            # shared character and gear data
    lfg/                   # LFG and dungeon boards
    streams/               # Twitch monitoring
    roles/                 # Forever role management
    countdowns/
    suggestions/
    flameboard/
  integrations/
    twitch.py
    sheets.py
    wowsims.py
tests/
docs/
assets/
```

This is a **target**, not a description of files already migrated. Keep stable application interfaces while moving one feature at a time. There is no need for one Git branch per cog: modules organise features; short-lived branches organise changes.

## Migration invariants

1. **No production interruption:** GitHub branches and tests must not touch the running Fly.io machine or its volume. No deployment workflow should trigger on a structural PR without an explicit release decision.
2. **Persistent data:** preserve `/data/bot.db`, `/data/raid_signup.db`, `/data/lfg_board.db`, `/data/countdown.db` and `/data/streams.db`. Do not recreate tables or rewrite existing records.
3. **Discord compatibility:** preserve registered persistent component `custom_id` values, existing commands, permissions and guild/channel IDs. Old messages and buttons should continue working after a deploy.
4. **Scheduled tasks:** keep midnight UK coin settlement, Sunday bonuses, raid reminders, Twitch monitoring, and periodic recovery behaviour idempotent; avoid duplicate loops or posts.
5. **Third-party integrations:** preserve working Google Sheets, Warcraft Logs, Twitch and WoWSims flows, credentials and resource identifiers.
6. **Licensing:** retain MIT attribution and the original copyright notice while separating Hyped Ogre modules from remaining inherited RollF code.

## Order of work

- **Baseline:** preserve a snapshot, correct stale tests, add continuous checks on `main`, and review a fast-forward promotion of the latest code. Do not automatically deploy.
- **Foundation:** introduce package metadata and application abstractions without changing the Docker entry point.
- **Small modules first:** move utilities with clear interfaces and test coverage.
- **Complex features next:** move LFG, streams, raids, and finally the large roll coordinator incrementally.
- **Entry-point cutover:** change Docker/startup only after end-to-end smoke tests and a verified backup, with rollback ready.

## Tests and release controls

Run `python -m compileall -q rollf.py cogs hyped_ogre tests` and `python -m unittest discover -s tests -v`. GitHub Actions also builds the current Docker image and exercises representative Discord views. A green status verifies the tested code path but does **not** establish that the production Fly service is online. Verify Fly machine health separately before and after any release.
