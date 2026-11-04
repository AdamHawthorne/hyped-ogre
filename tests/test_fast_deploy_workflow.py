"""Fast production deploy keeps release safeguards without replaying PR tests."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy-manual.yml"
SAFETY = ROOT / ".github" / "workflows" / "safety.yml"


class FastDeployWorkflowTests(unittest.TestCase):
    def test_main_deploy_skips_repeated_linux_and_windows_test_jobs(self):
        workflow = DEPLOY.read_text(encoding="utf-8")
        self.assertIn("push:\n    branches: [main]", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("\n  deploy:\n", workflow)
        self.assertNotIn("\n  checks:\n", workflow)
        self.assertNotIn("needs: checks", workflow)
        self.assertNotIn("uses: ./.github/workflows/safety.yml", workflow)
        self.assertNotIn("Run offline tests on Windows", workflow)
        self.assertNotIn("python -m unittest", workflow)

    def test_backup_rollback_data_and_serial_deployment_are_preserved(self):
        workflow = DEPLOY.read_text(encoding="utf-8")
        for required in (
            "group: hyped-ogre-production-deploy",
            "cancel-in-progress: false",
            "environment: production",
            "python scripts/fly_release.py prepare",
            "flyctl deploy -a hyped-diceman --remote-only --ha=false",
            "python scripts/fly_release.py verify",
            "FLY_API_TOKEN:",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

    def test_full_safety_checks_still_run_on_pull_requests(self):
        workflow = SAFETY.read_text(encoding="utf-8")
        for required in (
            "pull_request:",
            "branches: [main]",
            "Run migration regression guards",
            "docker build -t hyped-ogre-test .",
            "verify-windows:",
            "Run offline tests on Windows",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)


if __name__ == "__main__":
    unittest.main()
