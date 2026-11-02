"""Offline release checks. No live Fly or Discord credentials are used."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fly_release", ROOT / "scripts/fly_release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
NOW = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
VOLUME = {"id": "vol_test", "name": "diceman_data", "state": "created"}
MACHINE = {
    "id": "machine1",
    "state": "started",
    "config": {
        "image": "registry.fly.io/hyped-diceman:working",
        "mounts": [{"volume": "vol_test", "path": "/data"}],
    },
}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


def snapshot(ident="vs_ready", *, status="created", age=60):
    return {
        "id": ident,
        "status": status,
        "created_at": (NOW - timedelta(seconds=age)).isoformat(),
    }


class FlyReleaseTests(unittest.TestCase):
    def test_only_a_recent_completed_snapshot_is_usable(self):
        invalid = [
            snapshot(status="waiting"),
            snapshot(age=3601),
            snapshot(age=-1),
            {"id": "vs_bad", "status": "created", "created_at": ""},
        ]
        self.assertIsNone(release.recent_snapshot(invalid, NOW))
        self.assertEqual(
            release.recent_snapshot(invalid + [snapshot()], NOW), "vs_ready"
        )
        self.assertEqual(
            release.recent_snapshot(
                [snapshot("vs_old", age=80), snapshot("vs_new", age=10)], NOW
            ),
            "vs_new",
        )

    def test_recent_backup_is_reused_without_creating_or_waiting(self):
        with (
            patch.object(release, "datetime", Clock),
            patch.object(release, "fly", return_value=[snapshot()]) as fly,
            patch.object(release.time, "sleep") as sleep,
        ):
            self.assertEqual(release.ensure_snapshot("vol_test"), "vs_ready")
            self.assertEqual(fly.call_count, 1)
            sleep.assert_not_called()

    def test_new_backup_waits_until_created(self):
        replies = [[], "Scheduled\n", [snapshot(status="waiting")], [snapshot()]]
        with (
            patch.object(release, "datetime", Clock),
            patch.object(release, "fly", side_effect=replies) as fly,
            patch.object(release.time, "sleep") as sleep,
        ):
            self.assertEqual(release.ensure_snapshot("vol_test"), "vs_ready")
            self.assertEqual(
                fly.call_args_list[1].args,
                ("volumes", "snapshots", "create", "vol_test"),
            )
            self.assertEqual(sleep.call_count, 2)

    def test_backup_timeout_refuses_to_continue(self):
        def fly(*args, **kwargs):
            return "Scheduled\n" if "create" in args else [snapshot(status="waiting")]

        with (
            patch.object(release, "fly", side_effect=fly),
            patch.object(release.time, "sleep"),
        ):
            with self.assertRaisesRegex(ValueError, "deployment has not started"):
                release.ensure_snapshot("vol_test")

    def test_cli_failure_is_preserved_and_output_is_consumed(self):
        with patch.object(
            release.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(73, "flyctl"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                release.fly("volumes", "list", json_output=True)
        large = [snapshot()] + [{"filler": "x" * 8192}] * 30
        with patch.object(
            release.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(large)),
        ) as run:
            self.assertEqual(release.fly("volumes", "list", json_output=True), large)
            self.assertTrue(run.call_args.kwargs["capture_output"])
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(run.call_args.args[0][0], "flyctl")

    def test_missing_or_mismatched_volume_and_extra_machine_are_rejected(self):
        cases = [
            ([], [MACHINE]),
            ([VOLUME], [MACHINE, MACHINE]),
            ([{**VOLUME, "name": "other_data"}], [MACHINE]),
        ]
        wrong = copy.deepcopy(MACHINE)
        wrong["config"]["mounts"][0]["volume"] = "vol_other"
        cases.append(([VOLUME], [wrong]))
        cases.append(([VOLUME], [{**MACHINE, "state": "stopped"}]))
        for volumes, machines in cases:
            with (
                self.subTest(volumes=volumes, machines=machines),
                patch.object(release, "fly", side_effect=[volumes, machines]),
            ):
                with self.assertRaises(ValueError):
                    release.inspect_live()

    def test_prepare_records_rollback_and_verify_preserves_machine_and_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            record, summary = Path(tmp) / "record.json", Path(tmp) / "summary.md"
            with (
                patch.dict(
                    os.environ,
                    {
                        "FLY_API_TOKEN": "offline-token",
                        "GITHUB_STEP_SUMMARY": str(summary),
                    },
                    clear=True,
                ),
                patch.object(release, "inspect_live", return_value=(VOLUME, MACHINE)),
                patch.object(release, "ensure_snapshot", return_value="vs_ready"),
            ):
                release.prepare(record)
                release.verify(record)
            data = json.loads(record.read_text())
            self.assertEqual(data["rollback_image"], MACHINE["config"]["image"])
            self.assertEqual(data["volume"], "vol_test")
            self.assertIn(
                "--image registry.fly.io/hyped-diceman:working --ha=false",
                summary.read_text(),
            )
            self.assertNotIn("offline-token", summary.read_text())
            with patch.object(
                release,
                "inspect_live",
                return_value=({**VOLUME, "id": "vol_other"}, MACHINE),
            ):
                with self.assertRaisesRegex(ValueError, "changed unexpectedly"):
                    release.verify(record)

    def test_missing_token_or_wrong_config_stops_before_fly(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(release, "fly") as fly:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "FLY_API_TOKEN"):
                    release.prepare(Path(tmp) / "record.json")
            with patch.object(
                release.tomllib, "loads", return_value={"app": "another-app"}
            ):
                with self.assertRaisesRegex(ValueError, "Unexpected Fly app"):
                    release.prepare(Path(tmp) / "record.json")
            fly.assert_not_called()


class DeploymentWiringTests(unittest.TestCase):
    def test_main_push_and_manual_fallback_require_the_shared_checks(self):
        workflow = (ROOT / ".github/workflows/deploy-manual.yml").read_text()
        safety = (ROOT / ".github/workflows/safety.yml").read_text()
        self.assertIn("  push:\n    branches: [main]", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("inputs:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("uses: ./.github/workflows/safety.yml", workflow)
        self.assertIn("needs: checks", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("environment: production", workflow)
        self.assertIn("  workflow_call:", safety)
        self.assertIn("  pull_request:", safety)
        self.assertNotIn(
            "  push:", safety
        )  # main runs them through the deployment caller
        self.assertIn("verify-windows:", safety)
        self.assertLess(
            workflow.index("fly_release.py prepare"), workflow.index("flyctl deploy")
        )
        self.assertLess(
            workflow.index("flyctl deploy"), workflow.index("fly_release.py verify")
        )

    def test_existing_runtime_paths_and_launcher_are_retained(self):
        config = release.tomllib.loads((ROOT / "fly.toml").read_text())
        self.assertEqual(config["app"], release.APP)
        self.assertEqual(
            config["mounts"], [{"source": "diceman_data", "destination": "/data"}]
        )
        for key, value in release.DATA_PATHS.items():
            self.assertEqual(config["env"][key], value)
        self.assertIn(
            'CMD ["python", "rollogre.py"]', (ROOT / "Dockerfile").read_text()
        )


if __name__ == "__main__":
    unittest.main()
