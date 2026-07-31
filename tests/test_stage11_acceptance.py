from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY / "packaging" / "stage11_acceptance.py"
SPEC = importlib.util.spec_from_file_location("stage11_acceptance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


def complete_report(target: str = "windows-11") -> dict:
    return {
        "schemaVersion": 2,
        "target": target,
        "candidate": {
            "version": "0.11.0",
            "commit": "a" * 40,
            "installerFile": "AI-Agent-Orchestrator_0.11.0_x64-setup.exe",
            "installerSha256": "b" * 64,
        },
        "environment": {
            "productName": "Windows 11 Pro",
            "displayVersion": "24H2",
            "build": "26100.1",
            "architecture": "X64",
        },
        "checks": [
            {"id": check_id, "status": "passed", "notes": "observed"}
            for check_id in sorted(acceptance.REQUIRED_CHECKS)
        ],
    }


class Stage11AcceptanceTests(unittest.TestCase):
    def test_complete_report_passes(self) -> None:
        self.assertEqual(acceptance.validate_report(complete_report()), [])

    def test_not_tested_check_cannot_complete_acceptance(self) -> None:
        report = complete_report()
        report["checks"][0]["status"] = "not-tested"
        with self.assertRaisesRegex(
            acceptance.AcceptanceError, "acceptance is incomplete"
        ):
            acceptance.validate_report(report, require_complete=True)

    def test_failure_requires_notes(self) -> None:
        report = complete_report()
        report["checks"][0].update(status="failed", notes="")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "require"):
            acceptance.validate_report(report)

    def test_credentials_are_rejected(self) -> None:
        report = complete_report()
        report["checks"][0]["notes"] = "api_key=do-not-store-this-secret"
        with self.assertRaisesRegex(acceptance.AcceptanceError, "credential"):
            acceptance.validate_report(report)

    def test_matrix_requires_both_client_targets(self) -> None:
        reports = [complete_report()]
        with self.assertRaisesRegex(acceptance.AcceptanceError, "missing targets"):
            self._validate_loaded_matrix(reports)

    def test_matrix_requires_identical_candidate(self) -> None:
        windows_11 = complete_report()
        windows_10 = complete_report("windows-10-22h2")
        windows_10["candidate"]["installerSha256"] = "c" * 64
        with self.assertRaisesRegex(acceptance.AcceptanceError, "same version"):
            self._validate_loaded_matrix([windows_11, windows_10])

    def test_windows11_recovery_accepts_only_complete_windows11(self) -> None:
        original = acceptance.load_report
        try:
            acceptance.load_report = lambda _path: complete_report()
            acceptance.validate_windows11_recovery(Path("windows11.json"))
            acceptance.load_report = lambda _path: complete_report(
                "windows-10-22h2"
            )
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "requires a windows-11",
            ):
                acceptance.validate_windows11_recovery(Path("windows10.json"))
        finally:
            acceptance.load_report = original

    def test_powershell_collector_starts_every_check_unverified(self) -> None:
        collector = (
            REPOSITORY / "packaging" / "new-windows-client-acceptance.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('status = "not-tested"', collector)
        self.assertIn("schemaVersion = 2", collector)
        self.assertIn("Get-FileHash", collector)
        self.assertIn("$buildNumber -eq 19045", collector)
        self.assertIn("$buildNumber -ge 22000", collector)
        self.assertIn("windows-10-22h2", collector)
        self.assertIn("windows-11", collector)
        self.assertNotIn('status = "passed"', collector)

    def test_guided_windows11_runner_is_hash_bound_and_resumable(self) -> None:
        runner = (
            REPOSITORY / "packaging" / "run-windows11-golden-journey.ps1"
        ).read_text(encoding="utf-8")
        launcher = (
            REPOSITORY / "packaging" / "START-WINDOWS11-ACCEPTANCE.cmd"
        ).read_text(encoding="utf-8")

        self.assertIn("Get-FileHash", runner)
        self.assertIn("$manifest.sourceCommit", runner)
        self.assertIn("windows11-acceptance.json", runner)
        self.assertIn("Save-Evidence -Report $report", runner)
        for check_id in acceptance.REQUIRED_CHECKS:
            self.assertIn(f'id = "{check_id}"', runner)
        self.assertIn("-ExecutionPolicy Bypass", launcher)

    def _validate_loaded_matrix(self, reports: list[dict]) -> None:
        original = acceptance.load_report
        acceptance.load_report = lambda path: reports[int(path.name)]
        try:
            acceptance.validate_matrix(
                [Path(str(index)) for index in range(len(reports))]
            )
        finally:
            acceptance.load_report = original


if __name__ == "__main__":
    unittest.main()
