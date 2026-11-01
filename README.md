# Hyped Ogre

**Hyped Ogre** is an independent Python Discord bot for the Hyped World of Warcraft guild. It combines raid organisation, character and group tools, daily rolls and coins, Twitch notifications, and community utilities.

## Features

- **Daily rolls:** one free roll per UK day, a gold-coin ledger, Sunday double-coin awards, a once-per-day coin gamble, statistics and a High Roller role
- **Raids:** signups, saved characters, roster management, attendance, Warcraft Logs links, and Google Sheets integration
- **Characters and equipment:** saved characters, class/role handling and WoWSims export support
- **LFG/LFM:** dungeon and group-planning panels
- **Streams:** Twitch registration and stream notifications
- **Guild utilities:** Forever role panels, countdowns, suggestions, flameboard and Ogre messaging

Feature behaviour is covered by regression tests in `tests/`.

## Runtime and persistence

The **current production entry point is `rollf.py`** for backwards compatibility during the structural migration. The Dockerfile still launches `python rollf.py`. The project is being reorganised into feature modules incrementally. Do not change the Docker entry point without verifying the whole application.

The Fly.io application is `hyped-diceman` (configured in `fly.toml`). Its persistent SQLite data is stored on the mounted `/data` volume:

| Data | Fly path |
| --- | --- |
| Rolls and coin ledger | `/data/bot.db` |
| Raid signups and characters | `/data/raid_signup.db` |
| LFG board | `/data/lfg_board.db` |
| Countdown data | `/data/countdown.db` |
| Stream registrations | `/data/streams.db` |

Never delete, overwrite, move, or reinitialise these live databases as part of a code refactor. Keep the Fly volume mounted at `/data`. Database migrations must be additive and tested before deployment.

## Local setup

Use Python 3.13. Create a local `.env` from `.env.example`, supply your own credentials, and install dependencies:

```powershell
cd "C:\Users\Adam\hyped-ogre"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rollf.py
```

**Do not run a second local production bot using the same Discord token while Fly.io is serving the guild.** For ordinary development, run offline tests instead:

```powershell
python -m unittest discover -s tests -v
```

## Development and deployment

- `main` is intended to be the stable, reviewed code baseline.
- Use short-lived `feature/*`, `fix/*` or `refactor/*` branches for changes; features are **folders/modules**, not permanent branches.
- Run syntax checks, unit tests and Docker build checks before merging.
- A merge does **not** automatically deploy to Fly.io unless an explicit deployment workflow is configured.
- Don't deploy structural migrations without a verified database backup, health checks and a rollback plan.

See [architecture](docs/architecture.md) and [operational safety](docs/operations.md).

## Licence and provenance

Hyped Ogre is an **independent GitHub repository** and is not part of the RollF fork network. Some original code derives from [RollF](https://github.com/hegernat/rollf-bot), by hegernat/plutonium87. Attribution and the original MIT licence notice are retained in [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). Independence does not remove inherited licence obligations.
