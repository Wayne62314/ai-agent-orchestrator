from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_orchestrator.workspace import (
    DriftKind,
    WorkspaceInspector,
    WorkspaceSnapshot,
)


class WorkspaceTests(unittest.TestCase):
    def baseline(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            path=str(Path.cwd().resolve()),
            observed_at="now",
            vcs_type="git",
            branch="main",
            head="abc123",
            files={"src/app.py": {"status": " M", "hash": "one"}},
            fingerprint="baseline",
        )

    def test_identical_snapshot_has_no_drift(self) -> None:
        baseline = self.baseline()
        report = WorkspaceInspector().compare(baseline, baseline)
        self.assertEqual(report.kind, DriftKind.NONE)

    def test_relevant_file_change_is_conflict(self) -> None:
        baseline = self.baseline()
        current = replace(
            baseline,
            files={"src/app.py": {"status": " M", "hash": "two"}},
        )
        report = WorkspaceInspector().compare(
            baseline,
            current,
            relevant_files=("src/app.py",),
        )
        self.assertEqual(report.kind, DriftKind.CONFLICT)
        self.assertEqual(report.conflicting_paths, ("src/app.py",))

    def test_unrelated_file_change_is_non_conflicting(self) -> None:
        baseline = self.baseline()
        current = replace(
            baseline,
            files={
                **baseline.files,
                "notes.txt": {"status": "??", "hash": "new"},
            },
        )
        report = WorkspaceInspector().compare(
            baseline,
            current,
            relevant_files=("src/app.py",),
        )
        self.assertEqual(report.kind, DriftKind.NON_CONFLICTING)

    def test_branch_and_head_changes_block_resume(self) -> None:
        baseline = self.baseline()
        branch = WorkspaceInspector().compare(
            baseline,
            replace(baseline, branch="feature"),
        )
        head = WorkspaceInspector().compare(
            baseline,
            replace(baseline, head="def456"),
        )
        self.assertEqual(branch.kind, DriftKind.BRANCH_CHANGED)
        self.assertEqual(head.kind, DriftKind.HEAD_CHANGED)

    def test_real_git_snapshot_tracks_dirty_file(self) -> None:
        git = os.environ.get("TEST_GIT_BINARY") or shutil.which("git")
        if not git:
            self.skipTest("Git is not available.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([git, "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                [git, "config", "user.email", "stage2@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                [git, "config", "user.name", "Stage Two Test"],
                cwd=root,
                check=True,
            )
            target = root / "app.py"
            target.write_text("print('one')\n", encoding="utf-8")
            subprocess.run([git, "add", "app.py"], cwd=root, check=True)
            subprocess.run(
                [git, "commit", "-m", "initial"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            clean = WorkspaceInspector(git).snapshot(root)
            self.assertEqual(clean.vcs_type, "git")
            self.assertEqual(clean.files, {})

            target.write_text("print('two')\n", encoding="utf-8")
            dirty = WorkspaceInspector(git).snapshot(root)
            self.assertIn("app.py", dirty.files)
            self.assertNotEqual(clean.fingerprint, dirty.fingerprint)


if __name__ == "__main__":
    unittest.main()

