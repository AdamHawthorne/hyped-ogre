# Hyped Ogre agent workflow and feature reference

Companion to [AGENTS.md](../AGENTS.md). Read the root instructions first; consult only the sections needed. This is a living operational reference, not a changelog or a substitute for current code. Historical decisions were reconstructed from accessible Hyped conversations and the repository on 25 November 2026.

## 1. Fast, safe release path

The owner prefers small, efficient updates, with automatic production deployment after a reviewed merge. A brief bot restart is acceptable. Do not repeat a full test suite after merge merely to reassure the user. The 4 November optimisation (PR #109) separated PR safety checks from deployment; the current `.github/workflows/deploy-manual.yml` confirms this. Some older README/operations text is stale. Do not revert to the old manual-only or duplicate-check workflow.

### Terminal path (only if an authenticated terminal and checkout are available)

From the existing checkout, inspect current branch and changes before switching. Do not discard the user's edits or pull into a dirty tree.

```powershell
cd C:\Users\Adam\hyped-ogre
git status --short --branch
git fetch origin
git switch main
git pull --ff-only
git switch -c fix/<short-description>
```

If resuming, use the existing branch/PR instead of the last two branch-creation commands. Use the existing Python 3.13 venv, or the equivalent Linux interpreter in an actual connected checkout. No production token required.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_<affected_feature>.py" -v
.\.venv\Scripts\python.exe -m compileall -q <changed_python_paths>
git diff --check
git diff
```

Use multiple focused test files if contracts cross feature boundaries. Run the full suite locally for substantial/risky changes, not repeatedly for every small edit. VS Code Ctrl+Shift+B runs the full local checks when needed. Install dependencies only if missing or changed. Commit related implementation and tests together; no unrelated files, secrets or databases.

```powershell
git add <specific_files>
git commit -m "<specific change>"
git push -u origin HEAD
gh pr create --base main --title "<title>" --body "<scope, tests, risks>"
gh pr view <PR> --json state,headRefOid,mergeable,statusCheckRollup
gh pr checks <PR> --watch
```

Review diff and successful checks for the final head. If the user authorised release, and the checks all pass:

```powershell
gh pr merge <PR> --squash --match-head-commit <checked-head-sha>
gh pr view <PR> --json state,mergedAt,mergeCommit
gh run list --workflow deploy-manual.yml --branch main --commit <merge-sha> --limit 5
gh run watch <deployment-run-id> --exit-status
gh run view <deployment-run-id> --log
```

Branch deletion is optional after verified merge; do not delete an active branch. Confirm the workflow summary includes completed backup, previous rollback image and the existing started Machine with the same attached volume. Where access exists:

```powershell
fly status -a hyped-diceman
fly volumes list -a hyped-diceman
fly releases --image -a hyped-diceman
fly logs -a hyped-diceman
```

Test a harmless Discord interaction such as /stats or a relevant read-only board. Do not spend coins, create duplicate posts, alter attendance or test against another guild. If live Discord is inaccessible, explicitly say so. Sync local main with git pull --ff-only after a successful release when the checkout is available and clean; pulling alone does not deploy.

### GitHub connector path (when no terminal)

Use connector tools rather than public web search for private repository state. Discover exact actions/schemas as needed. Fetch current PR metadata, head SHA, relevant source/tests at that ref and current YAML. Use small, verified patches with correct content SHA; prefer one atomic commit of the related changes if supported. Confirm the head after writing. Do not assume a previous tool call succeeded, especially after an internal error.

For the existing PR, get metadata with get_pr_info (repository_full_name, pr_number), fetch_commit_workflow_runs (repo_full_name, commit_sha), and fetch_workflow_run_jobs (repo_full_name, run_id). Inspect job logs with fetch_workflow_job_logs (repo_full_name, job_id); filter the first actual failure, not incidental warnings. Check all jobs on the exact final head; an empty combined commit status is not proof of success. Some connector views do not surface push-triggered deployment runs, and running logs may be inaccessible. If so, give the actual repository Actions URL and distinguish the merge from unverified deployment.

For an authorised, verified green merge use merge_pull_request with squash and expected_head_sha. Re-read PR metadata afterwards. Main push triggers production, not feature-branch push. Do not directly push unreviewed code to main. Do not assume mergeable means checks passed. Auto-merge requires repository support and correctly enforced required checks; if disabled, do not change repository settings without explicit authorisation.

### When the process stalls

- Keep one PR and one final head. New commits cancel older safety runs; never push while a healthy final run is progressing. A cancelled run is not a failed assertion or proof of a chat-related problem.
- First locate the blocker: GitHub write failure, actual test failure, queue, timeout, cancellation, merge, backup guard, Fly build, or verification. Report the evidence.
- Fix actual causes in source or intentional test fixtures. Avoid merely changing assertions to hide regressions. If a specific job was cancelled for a transient reason, retry that job rather than all successful jobs, if the connector grants permission.
- A SQLite connection context manager commits/rolls back but does not close the connection. Use contextlib.closing or explicit finally/close; keep transaction semantics. Windows temporary database deletion failures (WinError 32) often expose leaked handles. Trace the connection owner; fix the leak, not by ignoring cleanup errors.
- Do not use repeated full-suite Windows checks as a post-merge release gate. Current PR workflow runs Linux/Docker and Windows checks. Changing required checks, test scope or repository protection requires separate explicit approval. A longer timeout fixes a time limit, not a failing test.
- Prefer the existing terminal/CI watcher if available, otherwise finish all actions possible within the current turn and be precise about what remains. Only an actual supported automation can make progress after a reply; do not imply ongoing execution without it. Avoid duplicate monitoring tasks, and stop a release monitor when verified or when user intervention is required.

### Deployment evidence and recovery

Production workflow: `.github/workflows/deploy-manual.yml`; safety: `.github/workflows/safety.yml`; guard: `scripts/fly_release.py`. Release job records existing Machine/volume, completed recent snapshot and previous image, runs `flyctl deploy -a hyped-diceman --remote-only --ha=false`, and verifies same Machine/volume. GitHub Actions is the normal release route. Deployment jobs are serialised; a newer push must not interrupt a running production deployment.

Preserve /data/bot.db, /data/raid_signup.db, /data/lfg_board.db, /data/countdown.db, /data/streams.db, /data/polls.db and any additional configured databases. A previous known Machine ID is 874deea0e11368; discover the current ID instead of hardcoding it. Use existing volume diceman_data; never recreate it. Snapshot selection and rollback are in the guard. Code rollback does not undo database writes. Any recovery involving live data requires explicit authorisation and separate verification; see [operations](operations.md).

### Session handover (short, update PR description or user-visible record only when useful)

```text
Request / scope:
Repository / PR / branch:
Exact head SHA:
Changed files and focused tests:
CI run ID / per-job results:
Merged SHA (if any):
Deployment run / result:
Verified live state:
Blocker / next concrete action:
```

Do not store credentials, personal data or a stale live status in AGENTS.md. A new chat must re-read GitHub state instead of treating this handover as fresh evidence.

## 2. Feature contracts and lookup map

The following records durable user decisions; implementation and newer user directions take precedence for details. Review relevant tests, not the entire suite, when modifying a feature.

| Area | Canonical location / contract |
| --- | --- |
| Startup and Discord | rollogre.py, hyped_ogre/bot.py, cogs/, hyped_ogre/core/legacy_source.py. Preserve original extension identities, persistent IDs, setup/reload behaviour and compatibility aliases. |
| Roll and coins | hyped_ogre/features/rolls/. One free 1–100 roll per UK day; first roll wins a tie; Ogre can roll/win once. Daily highest gets one coin, two on Sunday. Gamble stakes one coin after free roll; higher returns stake, equal/lower loses it. Original free roll determines daily rank. Ledger sorted by coins then oldest; no winner ping; /stats private, leaderboard public. |
| Shared characters | hyped_ogre/features/characters/, cogs/raid_signup.py. One canonical character identity linked to Discord and referenced by raids/LFG. Main/alt and off-spec distinct. Names verified on import; WSE first-name-only export must not create duplicates. Discord self-service/private leadership actions preserve history. LFG-only records stay off public roster until explicitly registered or used for raids. |
| Character board | characters/roster.py, armory.py, hub.py. Main-only V1 board: Tanks, Healers, Melee, Ranged; three aligned columns (character, Level (iLvl), Professions). Current main pins Big Snore within role, sorts others class then iLvl descending, unknown last. Keep consistent link fallback and embed limits, no routine pings, reuse tracked message. Blizzard level/professions and preferred iLvl; last verified values survive missing fields, 404/timeouts and incomplete professions. When Blizzard lacks iLvl, use latest member-linked WoWSims export only if every equipped item has verified local catalog level metadata; otherwise leave the display visually blank, not (N/A), and never invent equipment levels. Maintain aligned one-line-per-character columns. Distinguish verified empty from unavailable; honour confirmed unlearning. WoWSims remains the source for detailed raid gear and enchant review. Verify same full name/ID; do not copy cache across identities. Retry missing data fairly with bounded calls. |
| Professions | characters/crafting.py and relevant profession feature modules. Known recipes must have verified ownership; skill alone is not proof a recipe is learned. Missing API fields do not prove absent ownership. Craft priority shortlist includes Cooking/Fishing and omits gathering professions per latest request; verify catalogue/command before changing. |
| Raid signup and roster | features/raids/, cogs/raid_signup.py. Preserve registration, attendance, notes, archived raids, public confirmation/acknowledgement and message IDs. Survival Hunter melee; BM/MM ranged. Owner/default leadership semantics preserved. No double leadership for two splits of same instance; multiple distinct instances allowed. |
| Sheets | raids/sheets_dashboard.py, integrations/sheets.py, legacy cogs/raid_sheets_v3.py. One tab per raid; preserve manual selections keyed by IDs, Show on Discord, assignment, leadership notes, guest entries. New and returning signups default Pending, absent not selected. Selected total is a live formula counting selected signed populated character rows; verify actual displayed column coordinates after layout changes. Buff coverage/approval count selected signed players only. Use explicit selections in test fixtures. Permanent roster/attendance/history tabs and canonical SQLite history must remain consistent. |
| Attendance | raids/history.py, schedule.py and signup helpers. Account-level last four completed raids; bench credited as attended; Late/AFK credits as configured; preserve audit history. Absence remains private and does not erase signup notes or historical attendance. |
| LFG | features/lfg/. Shared character DB, visible board: LF Dungeons, Other, Characters, Edit. Independently edit/remove dungeon and other listings. Preserve ownership, saved plans and message IDs. No duplicate character creation or unrequested public roster registration. |
| Classrooms | classroom modules, [classrooms](classrooms.md). Access from registered class/primary role/off-spec; remove only grants no other registered character qualifies for. Preserve explicit denies. Use /classrooms setup once, /classrooms sync for refresh; do not sync category permissions over individual grants. |
| Polls | features/polls/, [polls](polls.md). Private management; anonymous default; explicit publication; deadlines enforced when voting; preserve ballots, dynamic IDs, and separate DB. No identity leakage or duplicate publication. |
| Discord presentation | Existing assets, Ogre voice, class icons, role layout and permissions. Keep compact V1/V2 design as requested; do not replace artwork, change commands, add pings, or redesign unrelated boards. |

Raid schedule and recruitment details evolve; use current user request/Discord/Sheets data rather than baking dates, headcounts or temporary assignments into code. The guild is Horde EU PvP for WoW Forever; typical Sunday core raid and initial Wednesday progression are guild policy, not a hardcoded bot scheduling rule.

## 3. Release history lessons (not live status)

- 1 November: PRs #78–79 fixed the Fly rollback guard (exit 141), Windows/LFG connection handling and VS Code checks; release v229 verified on existing London Machine.
- 2–4 November: automatic main deploy established (#80); later #86/#90/#92 reported verified releases. PR #109 removed duplicated post-merge tests, especially Windows; current YAML confirms the fast release path. Do not undo that improvement accidentally.
- PR #159 introduced Blizzard-only roster stats and explicit raid selection defaults. Some old tests still expected WoWSims or auto-selected signups. Ensure all affected fixture semantics change together.
- PR #160 addresses last-known Blizzard cache preservation and retry fairness. As reviewed 25 November, Linux/Docker passed; Windows hit a 10-minute timeout, then exposed 43 temporary SQLite file cleanup errors after timeout expansion. It was still open at the time of writing. Treat this as historical, not a claim of current status. Do not merge an unchecked head or bypass the gate; inspect current PR and logs.
- Chat context loss is a continuity risk, not evidence that starting a chat causes GitHub to cancel a job. Exact runner logs establish timeouts and test failures. Use the repository and CI to reconstruct state.

## 4. Keeping instructions current

Read AGENTS.md on every new session; consult relevant sections here. Keep the root compact; move lengthy feature specs into their own docs. When an enduring requirement changes, update the pertinent instruction and tests, not unrelated documentation. Do not use the instruction branch as a production deployment branch. Once these docs are integrated into main through normal review, make main the normal source and avoid two competing versions.

Historical coverage is limited to retrieved Hyped conversations and accessible repository files; not every archived chat could be enumerated or inspected. Current implementation, tests and fresh release evidence take precedence over historical assistant claims.
