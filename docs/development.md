# VS Code and GitHub workflow

Use VS Code to edit and check code, GitHub pull requests to review changes,
GitHub Actions to verify them, and the manual Fly workflow to release them.

## Set up VS Code on Windows

Open your existing checkout folder, normally `C:\Users\Adam\hyped-ogre`,
using **File > Open Folder**. The folder should contain `rollogre.py`,
`requirements.txt` and `.github/`.

Use Python 3.13 to match GitHub Actions and the runtime image. If `.venv`
already exists, check its version with
`.\.venv\Scripts\python.exe --version` and keep it if it is Python 3.13.
For a new environment, run this in VS Code's PowerShell terminal:

```powershell
py -3.13 -m venv .venv
```

Install the current requirements into that environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

These commands use the environment directly; activating it or changing
PowerShell's execution policy is unnecessary. If `py -3.13` cannot find
Python, install Python 3.13 first. On macOS/Linux, create the environment with
`python3.13 -m venv .venv` and use `.venv/bin/python` instead.

In VS Code:

1. Open Extensions and search `@recommended`. Install Python, GitHub Pull
   Requests and GitHub Actions from the recommended list.
2. Press **Ctrl+Shift+P**, choose **Python: Select Interpreter**, and select
   `.venv\Scripts\python.exe`. An earlier interpreter choice can override
   the shared default until you select it explicitly.
3. Sign into your GitHub account when the GitHub extension prompts you.
4. Press **Ctrl+Shift+B** to run **Hyped Ogre: local checks**.

The local checks use `.venv` explicitly. They check installed dependencies,
compile Python files and run the offline tests in sequence. They do not
start the Discord bot or deploy to Fly. No `.env` or production token is
needed for these checks.

For individual tasks, use **Terminal > Run Task**. For individual tests,
open VS Code's Testing view and refresh discovery after adding or renaming
tests. Discovery on each save is disabled to avoid repeatedly scanning the
suite while editing.

The existing `requirements.lock` predates the current runtime requirements.
Use `requirements.txt`, as CI and Docker do. Refreshing and validating a
production dependency lock is a separate dependency change.

## Make and review a change

1. Check Source Control for any uncommitted work before switching branches.
2. Update `main` using **Git: Pull** or, from a clean working tree:

```powershell
git switch main
git pull --ff-only
```

3. Use **Git: Create Branch** and a short-lived name such as
   `fix/raid-note-display` or `feature/stream-panel`.
4. Edit the relevant feature under `hyped_ogre/features/` and run
   **Hyped Ogre: local checks**.
5. Review the diff in Source Control, commit the intended files, and choose
   **Publish Branch** or **Push**.
6. Use **GitHub Pull Requests: Create Pull Request** to target `main`.
   The PR starts the safety workflow automatically.
7. Review the diff and wait for the Linux, Windows and security checks to
   pass before merging.

Safety checks run on PRs and on pushes to `main`. A branch without a PR
does not automatically run this workflow; open a draft PR early or run
**Hyped Ogre safety checks > Run workflow** on that branch. New commits to
the same PR cancel its older safety run. The Linux job includes Docker and
runtime checks; the Windows job checks the ordinary development commands.
The POSIX-only Fly shell fixtures run on Linux and are skipped on Windows.

## Release a reviewed change

After merging, check that `main`'s safety checks pass. Merging updates the
repository; the bot continues running the previously deployed release.

Use **Actions > Deploy Hyped Ogre (manual only) > Run workflow** when you
are ready to release. That workflow requires the exact reviewed `main`
commit SHA, the live volume ID, a completed snapshot, a working rollback
image and the confirmation phrase. Follow [operations](operations.md) for
the backup, release and live verification steps.

Local tasks and PR checks do not use `FLY_API_TOKEN`. Keep that deployment
secret in GitHub Actions. Keep `.env`, databases and private credentials
out of commits. Only the three shared VS Code files are tracked; personal
launch configurations remain ignored.

References: [VS Code Python settings](https://code.visualstudio.com/docs/python/settings-reference),
[Python testing](https://code.visualstudio.com/docs/python/testing),
[VS Code tasks](https://code.visualstudio.com/docs/debugtest/tasks), and
[GitHub workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
