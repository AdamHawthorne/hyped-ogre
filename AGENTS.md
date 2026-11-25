# Hyped Ogre: mandatory agent instructions

Read this file at the beginning of every Hyped Ogre coding or release task, including resumed work in a new chat. Follow the user's latest explicit instruction over older feature decisions. Use the current repository and actual workflow files for implementation facts. Do not rely on chat memory for release state.

## Identity and scope

- Repository: `AdamHawthorne/hyped-ogre`; stable branch: `main`.
- Production: Fly.io app `hyped-diceman`, London (`lhr`), existing single bot Machine, existing `diceman_data` volume mounted at `/data`. Boot: `python rollogre.py`. Python 3.13; dependencies in `requirements.txt`.
- Local Windows checkout (when actually available): `C:\Users\Adam\hyped-ogre`, PowerShell, `.venv`. Never claim access to the user's PC, terminal, Fly credentials or Discord unless the current tools provide it.
- Hyped Ogre is not the addon, companion app, or separate ForeverOgre rebuild. Do not change other repositories without a request.
- Keep changes small, focused and reversible. No unrelated refactoring, dependency upgrades, visual redesign, database resets or broad codebase scans for a small fix.

## Start and resume efficiently

1. Establish the exact request and whether deployment was authorised. "Deploy", "merge and deploy", or "continue until deployed" authorises the normal reviewed release for that change, not destructive actions or skipping checks.
2. Check current main and relevant open PR/branch, exact head SHA, CI status and release status before editing. Resume the same PR instead of creating duplicates. Read only relevant source, tests and current workflow files; consult [agent workflow](docs/agent-workflow.md) for details.
3. If terminal/repository tools are genuinely available, prefer a single local checkout with focused tests and a batched commit. Otherwise use the GitHub connector: fetch source and tests at the intended ref, check file SHAs, batch related changes into one commit when practical, and verify each write. Do not pretend a connector is a terminal.
4. Run focused regression tests before pushing where possible; update affected test fixtures for intentional behaviour changes, without weakening assertions. If unable to run locally, state that and use CI. Preserve existing semantics elsewhere. One complete patch is preferable to many commits that repeatedly cancel CI.
5. Create/update one short-lived PR to main. Inspect its diff. Wait for all configured safety checks on the exact final head to pass; do not merge red, cancelled or unknown checks. Do not change branch protection, skip checks, or force-push as a workaround.
6. Squash-merge the verified head only when authorised. Main automatically triggers `.github/workflows/deploy-manual.yml` (Deploy Hyped Ogre). **The full test suite runs on the PR, not again after merge** in the current optimised workflow. Inspect actual YAML if this changes.
7. Verify deployment on the merged SHA using the Actions run and its backup/machine/volume checks, then confirm live Discord behaviour if access exists. Report the precise highest verified stage, not an assumed success.

## Release discipline

- "Deployed" means the production release succeeded, not a file edit, commit, PR, green PR checks, or merge. A successful Fly release is separate from a verified Discord interaction.
- Use `gh pr checks <PR> --watch` and `gh run watch <RUN_ID> --exit-status` only when an authenticated terminal is available. See the detailed workflow for connector alternatives.
- Do not trigger new commits while checks are progressing unless there is a diagnosed fault. CI cancels superseded PR runs. Inspect the first failed step/log before changing code; retry only failed/cancelled jobs when appropriate.
- Do not invent background work. If unable to finish in the current turn, report the PR, exact head, run ID, blocker and next action. Use an explicitly authorised automation if available for subsequent checks; avoid duplicate tasks. New chat: read this file and resume from GitHub, not from guesses.
- Never describe a cancelled job as passed. Do not assume an empty connector result means no deployment. Distinguish access limitations from actual failures.
- Keep replies concise, show confirmed result and links, and avoid asking for information already available.

## Non-negotiable production safeguards

- Preserve all live SQLite databases, especially rolls/coins and raid attendance. No deleting, resetting, moving, replacing or restoring production data unless the owner explicitly authorises a separate, specific operation with a verified backup and recovery plan.
- Preserve existing Fly app, Machine, volume and paths. Use the normal release workflow, which records a completed snapshot and rollback image, deploys with `--ha=false`, then verifies the Machine and mounted volume. Never bypass it with a direct deploy for convenience.
- Additive, idempotent database migrations only by default. Use transactional writes, explicit connection closure (SQLite connection context managers alone do not close), parameterised SQL and offline tests with temporary DBs. No production DB or token in tests.
- Keep secrets in configured secret stores, never in code, logs, PRs, chats or artifacts. Do not run a second production Discord bot.
- Preserve canonical `cogs.*` extension names, slash commands, persistent custom IDs, public message IDs, permissions, stored character/raid identities and legacy imports. Do not register private extension_source modules as extra Cogs. Do not import `hyped_ogre.bot` in offline tests (startup side effects).

## Feature contracts

- SQLite is authoritative for registration, raid signups, attendance and roll data. Discord is the normal edit interface; Sheets is a controlled raid-management view, not a general database editor.
- Characters board: Blizzard is authoritative for level, average iLvl (equipped fallback) and professions. Keep last verified values for the same character on incomplete/failed responses; never substitute WoWSims public stats or character level for item level. Use WoWSims for detailed raid gear/enchant review. Missing values may be N/A only when never verified. Do not leak data across renamed/deleted identities. These are intended requirements; verify current implementation, especially outstanding fixes.
- Preserve manual raid selection/assignments, leadership notes and visibility when syncing Sheets. New signups require explicit selection; counts reflect selected signed rows, not total signups. Absent is not selected. Never silently change historical records.
- Keep roll/coin logic independent of unrelated features; preserve original free roll, tie priority, Sunday bonus, gambling rules and UK-day timing.
- Keep member actions private where intended; preserve Leadership permissions and avoid unrequested pings or duplicate panels.
- See [feature and release reference](docs/agent-workflow.md) before changing affected behaviour.

## Source priority and maintenance

1. Latest explicit user request; this file for enduring operating constraints.
2. Current GitHub code, tests, exact PR head, workflow YAML and deployment evidence.
3. Feature docs and historical chat decisions where still applicable.

Older README/development/operations descriptions may still mention repeated post-merge tests, or old roster behaviour. Treat executable workflows as authoritative for what actually runs, and distinguish desired contracts from currently deployed behaviour. Update this instruction set when enduring decisions change, not for every transient release.
