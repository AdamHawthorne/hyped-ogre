# Hyped Ogre architecture and refactoring plan

## Current state

The production Discord bot now starts via the renamed `python rollogre.py` command. That file is now a minimal executable compatibility entrypoint using Python's `runpy` to execute the original, unchanged bot coordinator from `hyped_ogre/bot.py` as `__main__`. The coordinator still owns configuration, roll processing and startup. The remaining Discord feature implementations now live in `hyped_ogre/features/`; only their identity-preserving extension entrypoints remain under `cogs/`. The Dockerfile copies `rollogre.py`, `cogs/`, `hyped_ogre/`, `assets/` and `web/` and launches `python rollogre.py`.

## Migrated modules

The current application still imports the old `cogs/` paths. These thin compatibility modules export the canonical implementations without changing existing runtime contracts:

| Feature | Canonical module | Backwards-compatible import |
| --- | --- | --- |
| Ogre messaging | `hyped_ogre/features/voice/messages.py` | `cogs/ogre_voice.py` |
| Discord emojis and visual roles | `hyped_ogre/core/emojis.py` | `cogs/emojis.py` |
| LFG sorting | `hyped_ogre/features/lfg/sorting.py` | `cogs/lfg_sort.py` |
| LFG pagination | `hyped_ogre/features/lfg/pagination.py` | `cogs/lfg_pagination.py` |
| LFG launch preview data | `hyped_ogre/features/lfg/demo.py` | `cogs/lfg_demo.py` |
| Dungeon catalogue and requests | `hyped_ogre/features/lfg/dungeons.py` | `cogs/lfg_dungeons.py` |
| Forever race and class data | `hyped_ogre/features/characters/races.py` | `cogs/forever_races.py` |
| Raid class and spec coverage | `hyped_ogre/features/raids/buffs.py` | `cogs/forever_raid_buffs.py` |
| Raid attendance history | `hyped_ogre/features/raids/history.py` | `cogs/raid_history.py` |
| Raid schedule, public roster and V2 panel | `hyped_ogre/features/raids/schedule.py` | `cogs/raid_schedule.py` |
| Leadership raid poster editor and roster preview | `hyped_ogre/features/raids/poster.py` | `cogs/raid_poster.py` |
| Roster announcement acknowledgement storage | `hyped_ogre/features/raids/acknowledgements.py` | `cogs/raid_acknowledgements.py` |
| Roster announcement posting and reactions | `hyped_ogre/features/raids/announcer.py` | `cogs/raid_announcer.py` |
| Google Sheets API, credentials and legacy RM-tab bridge | `hyped_ogre/integrations/sheets.py` | `cogs/raid_sheets.py` |
| Per-raid Google Sheets dashboard and roster selections | `hyped_ogre/features/raids/sheets_dashboard.py` | `cogs/raid_sheets_v3.py` |
| Forever item metadata | `hyped_ogre/features/characters/items.py` | `cogs/forever_item_data.py` |
| WoWSims equipment parser and sheet rows | `hyped_ogre/features/characters/equipment.py` | `cogs/wowsims_gear.py` |
| Countdown clock, DST and reminder calculations | `hyped_ogre/features/countdowns/core.py` | `cogs/countdown_core.py` |
| Lifetime coin awards and Sunday prizes | `hyped_ogre/features/rolls/coins.py` | `cogs/roll_coins.py` |
| Atomic coin staking and gamble | `hyped_ogre/features/rolls/gamble.py` | `cogs/roll_gamble.py` |
| Daily roll panel and coin leaderboard V2 | `hyped_ogre/features/rolls/panels.py` | `cogs/roll_panels.py` |
| Daily roll reaction reconciliation | `hyped_ogre/features/rolls/reactions.py` | `cogs/roll_reactions.py` |
| Flameboard reaction listeners | `hyped_ogre/features/flameboard/events.py` | `cogs/flameboard.py` |
| Forever role and leadership application panels | `hyped_ogre/features/forever/roles.py` | `cogs/forever_role.py` |

The Forever item metadata path is a full Python module alias rather than simple function re-exports, preserving the same in-memory cache, patchable download helper, offline cache behaviour and `.forever_items.json` files adjacent to the raid database. The WoWSims equipment module also uses a same-object alias, retaining its exact parsing, query and sheet-generation functions. Saved WoWSims exports remain in the existing raid database. The countdown calculations now share one module object across both import paths. Their UK time parsing, DST safeguards, timer rendering and reminder de-duplication remain unchanged; the countdown extension and stored reminder database have not moved. LFG dungeon-request storage and demo helpers retain their original imports through same-module aliases; the LFG SQLite schema, existing launch plans, character IDs, dungeon choices and persistent Discord views are unchanged. Raid announcements and acknowledgement functions also use same-module aliases, preserving the existing one-post-per-raid guard, acknowledgement de-duplication, retraction history, retry behaviour and posted Discord message IDs without changing stored raid data. The existing raid scheduling and public roster module also remains available through a same-module alias, preserving the existing `_UNCHANGED` sentinel, SQL schema, raid lifecycle, roster ordering, and persistent Discord V2 button IDs. This is a whole-module relocation only, not a behaviour rewrite. The leadership raid poster editor retains its original import path via the same-module alias, preserving edit permissions, schedule and advanced views, explicit confirmation, raid defaults, and the existing database and Discord interfaces. The opt-in Google Sheets API integration has also been moved under `hyped_ogre/integrations/sheets.py` with a same-object legacy import alias. Its service-account credential handling, existing RM-tab read/write ranges and leadership-controlled selection columns are unchanged. The per-raid `raid_sheets_v3` dashboard now lives in the raid feature package and retains its old same-module import path. Its A1:H5 summary layout, WCL and raid-note edits, selection/show controls, guest rows, class styling, WoWSims gear and history views, and mutable formatting caches are not rewritten. Remaining features are to be migrated individually with compatibility tests. Do not rename the live Discord extensions, slash commands, persistent interaction IDs or database files as part of a module move.

## Raid signup internals (first stage)

The `cogs/raid_signup.py` file is an active Discord extension loaded by `rollogre.py`. The LFG extension imports it **before** Discord calls `load_extension("cogs.raid_signup")`. It therefore remains a real `cogs.raid_signup` module, **not** a same-object import alias, so extension discovery, commands, listeners, and unload/reload ownership remain intact.

- `hyped_ogre/features/raids/signup_schema.py` now contains the unmodified SQL and legacy character migration operations. The original `create_schema(path=RAID_DB)` remains the entry point and supplies the original SQLite connection and path.
- `hyped_ogre/features/raids/signup_reference.py` now owns the unchanged class/spec/role choices, attendance statuses and legacy sheet-column definitions, imported directly into the existing cog.
- The registered `RaidPrototype`, its `setup(bot)`, permissions, component IDs, signups, character forms, background tasks and public commands remain in `cogs/raid_signup.py`.

Any further extraction must preserve import order and the existing extension module's ownership of its active Discord cog.

## Read-only character and signup storage (second stage)

The active `cogs.raid_signup` Discord extension keeps the original commands,
database paths, transaction management, permission checks, write methods and
registered Discord cog. Its original `list_characters`, `member_characters`,
`character_race`, `main_character_ids` and `has_wowsims_export` APIs
now call SQL-only helpers in `hyped_ogre/features/characters/queries.py`.
The original `signup_note_details`, `my_signup` and `my_absence` APIs
call `hyped_ogre/features/raids/signup_queries.py`.

Each new helper accepts an existing SQLite connection. The original
`cogs.raid_signup` wrapper still opens and closes the connection under
the original `path=RAID_DB` default. No separate database or duplicated
Discord cog is created. Character and raid signup writes are not moved.

## Character registration and profile mutations (third stage)

The original `cogs.raid_signup` extension still handles public Discord
commands, character forms and reactions, database paths, the SQLite
connection/transaction lifecycle, runtime ownership and data validation.
Its existing `save_character`, `set_character_race`, `set_main_character`
and `update_character_profile` functions remain importable with the same
signatures. They now delegate SQL changes to connection-taking helpers in
`hyped_ogre/features/characters/mutations.py`.

The immediate write lock around name lookup and insert, existing character
IDs, Main/Alt controls, class/race compatibility, return strings, and all
gear, raid signup and attendance references are preserved. The destructive
`delete_character` path remains unchanged in the original extension: it
must continue safeguarding both raid history and LFG requests.

## Raid signup and absence writes (fourth stage)

The active `cogs.raid_signup` extension continues to expose `save_signup`
and `save_absence` with the same signatures, optional note validation,
`path=RAID_DB` defaults and SQLite transaction lifecycle. The new
`hyped_ogre/features/raids/signup_mutations.py` contains the original
SQL and guard ordering, accepting the existing connection rather than
opening another database.

The unchanged WoWSims policy function is passed from the original cog,
so callers and tests that patch `wowsims_requirement_for_signup` keep
working. The same member/night upsert preserves notes and original
signup timestamps where appropriate; restoring a signup deletes an
absence record. Marking someone absent retains their character and
original signup note, and does not require a character to be registered.

No changes were made to the original Discord GroupCog, command registration,
raid state management, character deletion, attendance history, scheduled
Sheets synchronisation or production database schema.

## Roll feature helpers

The four roll helper modules are now canonical under `hyped_ogre/features/rolls/` with same-module `cogs` aliases. The canonical `hyped_ogre/bot.py` coordinator, midnight reset and database connection remain unchanged. The immutable history of daily rolls, coin-award migrations, once-a-day gamble writes, returned stakes, original free-roll tie priority, V2 button IDs and reaction recovery are preserved. The coordinator now lives in `hyped_ogre/bot.py`; the `rollogre.py` entrypoint is a thin launcher.

## Bot coordinator entrypoint migration

The original RollF-derived coordinator was moved unchanged into
`hyped_ogre/bot.py`. The renamed `rollogre.py` wrapper runs it as
`__main__` with `runpy.run_module`. This retains `python rollogre.py`,
the Dockerfile, `sys.argv`, existing Discord extension import strings,
all command and handler registrations, bot startup tasks, RollF licence
attribution and the current `bot.run(TOKEN)` behaviour. The coordinator
has no `__file__`-relative resources or `sys.argv` parsing.

**Do not import `hyped_ogre.bot` in offline tests:** its startup runs
at module scope. Static source/AST regression tests now target the
canonical module, and the compatibility wrapper is checked independently.

Merging this repository change does not deploy or restart the Fly.io bot.

## Live Discord extension source relocation

The active `cogs.countdown`, `cogs.lfg_preview`, `cogs.raid_signup`,
`cogs.streams` and `cogs.suggestions` implementations have been physically
moved unchanged into their corresponding feature folders as
`extension_source.py` (or `signup_extension_source.py` for raids).

**Discord registration still uses the original module names.**
`hyped_ogre/core/legacy_source.py` uses an explicit allow-list and
Python's standard-library `SourceFileLoader.exec_module` to execute the
trusted package source in its original `cogs.*` module namespace.
This is intentional: Discord's extension unload/reload mechanism identifies
registered Cogs, commands and listeners by the original `__module__`.
It also preserves relative imports inside LFG/countdown and shared mutable
module globals used by other cogs and existing tests. The feature source
files are private runtime implementation files; do not directly import them
as an additional Cog or instantiate a second bot.

Unlike simple aliases, this loading pattern supports Discord
`load_extension("cogs.suggestions")` and `unload_extension` without
leaving an externally-namespaced class registered. The five original entrypoint
files are now short, explicit compatibility wrappers. The source code,
SQL queries, persistent Discord controls, background tasks, Google Sheets
synchronisation, setup order and production data paths are unchanged.

The final switch to loading feature extensions directly should only be
considered after a separate integration test for Discord reloads and
a planned rollout; it is **not** part of this repository-only migration.

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
- **Deployment cutover:** change to `rollogre.py` only after Docker smoke tests, a verified volume snapshot and a recorded rollback image.

## Tests and release controls

Run `python -m compileall -q rollogre.py cogs hyped_ogre tests` and `python -m unittest discover -s tests -v`. GitHub Actions also builds the current Docker image and exercises representative Discord views. A green status verifies the tested code path but does **not** establish that the production Fly service is online. Verify Fly machine health separately before and after any release.
