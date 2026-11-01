"""Offline checks that Fly deployments remain manual and data-safe.

Do not import the live bot or access any Fly or Discord credentials.
"""

from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
