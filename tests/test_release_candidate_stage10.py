from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseCandidateStage10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.workflow = (
            cls.repository / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

    def test_baseline_is_rebuilt_from_the_approved_commit(self) -> None:
        self.assertIn("desktop-baseline:", self.workflow)
        self.assertIn(
            "ref: e27665475cd8d8c3612ab0b453a5ae1992ef2bf2",
            self.workflow,
        )
        self.assertIn(
            "ai-agent-orchestrator-windows-0.10.0-baseline",
            self.workflow,
        )

    def test_candidate_runs_on_two_fresh_windows_images(self) -> None:
        self.assertIn("candidate-validation:", self.workflow)
        self.assertIn("- windows-2022", self.workflow)
        self.assertIn("- windows-2025", self.workflow)
        self.assertIn("-LaunchApplication", self.workflow)
        self.assertIn("scan-windows-installer.ps1", self.workflow)

    def test_upgrade_uses_real_installers_and_persists_evidence(self) -> None:
        upgrade = (
            self.repository / "packaging" / "test-windows-upgrade.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("0\\.10\\.0", upgrade)
        self.assertIn("0\\.13\\.0", upgrade)
        self.assertIn('"task/create"', upgrade)
        self.assertIn("sourceDatabaseSchema -ne 6", upgrade)
        self.assertIn("targetDatabaseSchema -ne 7", upgrade)
        self.assertIn("defaultUninstallPreservedData = $true", upgrade)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", upgrade)
        self.assertIn("-RedirectStandardInput $requestPath", upgrade)
        self.assertNotIn("StandardInput.", upgrade)
        self.assertIn('set `"PYTHONUTF8=1`"', upgrade)
        self.assertIn('set `"PYTHONIOENCODING=utf-8`"', upgrade)
        self.assertIn("-FilePath $env:ComSpec", upgrade)
        self.assertIn("AllowLegacyOutputEncodingFailure", upgrade)
        self.assertIn("legacyBaselineEncodingFailureVerified", upgrade)
        self.assertIn("if: ${{ always() }}", self.workflow)
        self.assertIn("ai-agent-orchestrator-upgrade-evidence", self.workflow)

    def test_defender_scan_is_hash_bound_and_fail_closed(self) -> None:
        scan = (
            self.repository / "packaging" / "scan-windows-installer.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("MpCmdRun.exe", scan)
        self.assertIn("Get-FileHash", scan)
        self.assertIn('result = "no-threats"', scan)
        self.assertIn("$scan.ExitCode -ne 0", scan)
        self.assertIn("ai-agent-orchestrator-defender-", self.workflow)

    def test_release_gate_requires_every_candidate_job(self) -> None:
        self.assertIn("DESKTOP_BASELINE_RESULT", self.workflow)
        self.assertIn("CANDIDATE_VALIDATION_RESULT", self.workflow)
        self.assertIn("DESKTOP_UPGRADE_RESULT", self.workflow)


if __name__ == "__main__":
    unittest.main()
