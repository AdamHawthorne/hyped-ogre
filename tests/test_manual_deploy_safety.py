"""Offline checks that Fly deployments remain manual and data-safe.

Do not import the live bot or access any Fly or Discord credentials.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ManualFlyDeploymentSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            ROOT / ".github/workflows/deploy-manual.yml"
        ).read_text(encoding="utf-8")
        cls.operations = (
            ROOT / "docs/operations.md"
        ).read_text(encoding="utf-8")

    def test_workflow_is_only_manual_and_requires_main_and_full_sha(self):
        text = self.workflow
        self.assertIn("  workflow_dispatch:", text)
        for forbidden in ("\n  push:", "\n  pull_request:",
                          "\n  schedule:", "\n  release:"):
            self.assertNotIn(forbidden, text)
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn('git rev-parse HEAD', text)
        self.assertIn('^\x5b0-9a-f\x5d{40}$', text)
        self.assertIn("test \"$CONFIRMATION\" = \"DEPLOY HYPED OGRE\"", text)
        self.assertIn("environment: production", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_volume_and_rollback_guards_precede_deployment(self):
        text = self.workflow
        checks = (
            "flyctl volumes list -a hyped-diceman",
            'flyctl volumes snapshots list "$VOLUME_ID"',
            'created([[:space:]]|$)',
            "flyctl releases --image -a hyped-diceman",
            "flyctl deploy -a hyped-diceman --remote-only --ha=false",
        )
        positions = []
        for part in checks:
            with self.subTest(check=part):
                self.assertIn(part, text)
                positions.append(text.index(part))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("secrets.FLY_API_TOKEN", text)
        self.assertIn("command -v flyctl", text)
        self.assertIn("flyctl version", text)
        self.assertNotRegex(text, r"\bfly (?:volumes|releases|status|deploy)\b")
        self.assertNotIn("flyctl volumes delete", text)
        self.assertNotIn("flyctl volumes create", text)
        self.assertNotIn("fly scale count", text)
        self.assertNotIn("fly secrets set", text)

    def test_launcher_and_original_persistent_fly_paths_stay_unchanged(self):
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        fly = (ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "rollogre.py"]', docker)
        self.assertIn('source = "diceman_data"', fly)
        self.assertIn('destination = "/data"', fly)
        for path in (
            "/data/bot.db", "/data/raid_signup.db", "/data/lfg_board.db",
            "/data/countdown.db", "/data/streams.db",
        ):
            with self.subTest(path=path):
                self.assertIn(path, fly)
        self.assertIn("fly volumes snapshots create", self.operations)
        self.assertIn("fly releases --image", self.operations)
        self.assertIn("previous-working-image", self.operations)


BASH = shutil.which("bash")

# Only this executable is placed on PATH ahead of standard system utilities.
# Tests run without inheriting any live Fly or Discord credentials.
FAKE_FLYCTL = r'''
import os
import signal
import sys

signal.signal(signal.SIGPIPE, signal.SIG_DFL)
args = sys.argv[1:]
case = os.environ["MOCK_FLY_CASE"]
with open(os.environ["MOCK_FLY_CALLS"], "a", encoding="utf-8") as calls:
    calls.write(" ".join(args) + "\n")

if args == ["version"]:
    print("offline Fly CLI")
elif args == ["volumes", "list", "-a", "hyped-diceman"]:
    if case != "volume_missing":
        ident = "vol_other" if case == "volume_id_mismatch" else "vol_test"
        name = "other_data" if case == "volume_name_mismatch" else "diceman_data"
        print(ident, name, "lhr")
    if case == "volumes_failure":
        sys.exit(73)
elif args == ["volumes", "snapshots", "list", "vol_test", "-a", "hyped-diceman"]:
    if case != "snapshot_missing":
        ident = "vs_other" if case == "snapshot_id_mismatch" else "vs_test"
        state = "pending" if case == "snapshot_pending" else "created"
        print(ident, state)
    if case == "snapshots_failure":
        sys.exit(73)
elif args == ["releases", "--image", "-a", "hyped-diceman"]:
    if case != "rollback_missing":
        print("v228 registry.fly.io/hyped-diceman:working", flush=True)
    if case == "large_output":
        # More than a pipe buffer after an early match reproduces the real
        # Fly producer's SIGPIPE with the old `flyctl ... | grep -Fq` guard.
        for _ in range(256):
            sys.stdout.write("other release " + "x" * 8192 + "\n")
    if case == "releases_failure":
        sys.exit(73)
elif args == ["status", "-a", "hyped-diceman"]:
    if case == "status_failure":
        sys.exit(73)
    print("offline status check passed")
else:
    print("Unexpected Fly command in offline test", file=sys.stderr)
    sys.exit(99)
'''


@unittest.skipUnless(BASH, "Bash is required to execute GitHub runner guards")
class ManualFlyPreflightExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (ROOT / ".github/workflows/deploy-manual.yml").read_text(
            encoding="utf-8"
        )
        step = workflow.split(
            "      - name: Check previously working image and completed volume snapshot\n",
            1,
        )[1].split("\n      - name:", 1)[0]
        cls.preflight = textwrap.dedent(step.split("        run: |\n", 1)[1])

    def run_preflight(self, case, *, token="offline-test-token"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "flyctl"
            executable.write_text(
                f"#!{sys.executable}\n" + FAKE_FLYCTL, encoding="utf-8"
            )
            executable.chmod(0o755)
            calls_path = root / "calls.txt"
            # Do not copy os.environ: production credentials must never be used.
            env = {
                "PATH": str(root) + os.pathsep + os.defpath,
                "FLY_API_TOKEN": token,
                "VOLUME_ID": "vol_test",
                "SNAPSHOT_ID": "vs_test",
                "ROLLBACK_IMAGE": "registry.fly.io/hyped-diceman:working",
                "MOCK_FLY_CASE": case,
                "MOCK_FLY_CALLS": str(calls_path),
                "LC_ALL": "C",
                "TMPDIR": str(root),
            }
            result = subprocess.run(
                [BASH, "-c", self.preflight],
                env=env,
                text=True,
                capture_output=True,
                timeout=15,
            )
            calls = (
                calls_path.read_text(encoding="utf-8").splitlines()
                if calls_path.exists() else []
            )
            # The workflow removes its listing files on success and failure.
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             sorted(["flyctl"] + (["calls.txt"] if calls else [])))
            return result, calls

    def test_valid_backup_checks_pass_with_large_release_output(self):
        result, calls = self.run_preflight("large_output")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Persistent volume, completed snapshot and rollback image verified",
                      result.stdout)
        self.assertEqual(calls, [
            "version",
            "volumes list -a hyped-diceman",
            "volumes snapshots list vol_test -a hyped-diceman",
            "releases --image -a hyped-diceman",
            "status -a hyped-diceman",
        ])

    def test_missing_or_invalid_backup_evidence_stops_before_status(self):
        cases = (
            ("volume_missing", "Expected diceman_data volume not found"),
            ("volume_id_mismatch", "Expected diceman_data volume not found"),
            ("volume_name_mismatch", "Expected diceman_data volume not found"),
            ("snapshot_missing", "Verified completed Fly snapshot not found"),
            ("snapshot_id_mismatch", "Verified completed Fly snapshot not found"),
            ("snapshot_pending", "Verified completed Fly snapshot not found"),
            ("rollback_missing", "Previously working rollback image not found"),
        )
        for case, message in cases:
            with self.subTest(case=case):
                result, calls = self.run_preflight(case)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout)
                self.assertNotIn("status -a hyped-diceman", calls)

    def test_cli_errors_are_preserved_even_after_printing_matching_evidence(self):
        cases = (
            ("volumes_failure", "volumes list -a hyped-diceman"),
            ("snapshots_failure", "volumes snapshots list vol_test -a hyped-diceman"),
            ("releases_failure", "releases --image -a hyped-diceman"),
            ("status_failure", "status -a hyped-diceman"),
        )
        for case, command in cases:
            with self.subTest(case=case):
                result, calls = self.run_preflight(case)
                self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
                self.assertEqual(calls[-1], command)
                self.assertNotIn("rollback image verified", result.stdout)

    def test_missing_token_stops_before_calling_fly(self):
        result, calls = self.run_preflight("large_output", token="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FLY_API_TOKEN secret is missing", result.stdout)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
