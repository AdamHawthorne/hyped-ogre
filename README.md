# Hyped Ogre

**Hyped Ogre** is an independent Python Discord bot for the Hyped World of Warcraft guild. It combines raid organisation, character and group tools, daily rolls and coins, Twitch notifications, and community utilities.

## Features

- **Daily rolls:** one free roll per UK day, a gold-coin ledger, Sunday double-coin awards, a once-per-day coin gamble, statistics and a High Roller role
- **Raids:** signups, saved characters, roster management, attendance, Warcraft Logs links, and Google Sheets integration
- **Characters and equipment:** saved characters, class/role handling and WoWSims export support
- **Character hub:** private `/characters` editing anywhere, a persistent `/postcharacters` board, and main-spec/off-spec classroom access
- **Raid activity:** private Leadership alerts for absence and note changes, with a durable retry queue
- **Classrooms:** private class and role chats with direct member access from registered mains, alts and off-specs. See the [classroom setup guide](docs/classrooms.md).
- **LFG/LFM:** dungeon and group-planning panels
- **Streams:** Twitch registration and stream notifications
- **Polls:** private `/poll create` setup, anonymous single/multiple-choice votes, optional live counts, deadlines, private results and deliberate publication. See the [poll guide](docs/polls.md).
- **Guild utilities:** Forever role panels, countdowns, suggestions, flameboard and Ogre messaging
- **Guild sync:** optional signed sync from the Hyped Guild Companion for main-character levels and guild-bank activity. See [guild sync setup](docs/guild-sync.md).
- **Companion controls:** optional signed raid draft requests to the private owner-only poster. See [guild companion control setup](docs/guild-companion-control.md).

Feature behaviour is covered by regression tests in `tests/`.

See [release tools](docs/release-tools.md) for the character hub, live LFG mode,
Leadership notifications, attendance mirroring and live verification steps.

## Runtime and persistence

The **production boot command is `python rollogre.py`**. The original bot coordinator is now at `hyped_ogre/bot.py`, while `rollogre.py` is the small executable launcher. Existing Discord extensions are still loaded under their original `cogs.*` module names, so command registration, extension unload/reload, permissions and persistent Discord button IDs are preserved.

The implementation files are now grouped by feature under `hyped_ogre/features/`:

```text
hyped_ogre/
  bot.py                  # complete roll coordinator and startup
  core/                   # shared emojis and legacy extension source loader
  features/
    rolls/                # coins, gambling, panels, reactions
    flameboard/           # event listeners
    forever/              # guild role and leadership panels
    raids/                # signup, schedule, roster, Google Sheets dashboard
    characters/           # shared character and WoWSims data
    lfg/                  # LFG board, dungeon requests, launch plans
    countdowns/           # reminders, schedules and Discord controls
    streams/              # Twitch notifications
    suggestions/          # feedback panels
    polls/                # anonymous polls, private setup and durable deadlines
    voice/                # Ogre dialogue
  integrations/           # Google Sheets API bridge
cogs/                     # compatibility imports and live extension identities
rollogre.py               # production executable launcher
```

The active Discord extension source is loaded into its original `cogs.*` module via a checked-in allow-list and the standard-library `SourceFileLoader`. This deliberately retains Discord's correct Cog ownership during unload/reload. Do not directly import or register the private `extension_source.py` files as additional Cogs. See [architecture](docs/architecture.md) for the compatibility design.

The Fly.io application is `hyped-diceman` (configured in `fly.toml`). Its persistent SQLite data is stored on the mounted `/data` volume:

| Data | Fly path |
| --- | --- |
| Rolls and coin ledger | `/data/bot.db` |
| Raid signups and characters | `/data/raid_signup.db` |
| LFG board | `/data/lfg_board.db` |
| Countdown data | `/data/countdown.db` |
| Stream registrations | `/data/streams.db` |
| Anonymous polls | `/data/polls.db` |

Never delete, overwrite, move, or reinitialise these live databases as part of a code refactor. Keep the Fly volume mounted at `/data`. Database migrations must be additive and tested before deployment.

## Local setup

Use Python 3.13 and the project virtual environment. Ordinary development and
offline tests do not need a Discord token or a `.env` file:

```powershell
cd "C:\Users\Adam\hyped-ogre"
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open the repository folder in VS Code, select the `.venv` interpreter and
install the recommended extensions. Press **Ctrl+Shift+B** to run dependency,
syntax and test checks. Individual tests are available in the Testing view.
The deployment guard is covered by offline tests on both Windows and Linux.

See the [VS Code and GitHub workflow guide](docs/development.md) for setup,
branching, pull requests and deployment. **Do not run a second local
production bot using the same Discord token while Fly.io is serving the guild.**

## Development and deployment

- `main` is intended to be the stable, reviewed code baseline.
- Use short-lived `feature/*`, `fix/*` or `refactor/*` branches for changes; features are **folders/modules**, not permanent branches.
- Run syntax checks, unit tests and Docker build checks before merging.
- GitHub checks PRs, changes merged to `main`, and manually requested safety runs. The same PR update no longer starts both a branch-push safety run and a PR safety run.
- Every push or merged PR to `main` runs the shared Linux/Windows checks and Docker smoke tests, then automatically deploys to the existing Fly app. Feature-branch pushes and PR checks do not deploy.
- **Actions > Deploy Hyped Ogre > Run workflow**, using `main`, provides a manual retry with no input fields. The workflow records a recent completed snapshot and the previous image automatically.
- Don't deploy structural migrations without a verified database backup, health checks and a rollback plan.

See [architecture](docs/architecture.md) and [operational safety](docs/operations.md).

## Licence and provenance

Hyped Ogre is an **independent GitHub repository** and is not part of the RollF fork network. Some original code derives from [RollF](https://github.com/hegernat/rollf-bot), by hegernat/plutonium87. Attribution and the original MIT licence notice are retained in [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). Independence does not remove inherited licence obligations.
