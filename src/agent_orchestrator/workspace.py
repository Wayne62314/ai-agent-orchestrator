"""Git-backed workspace facts and drift classification."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import ValidationError
from .store import canonical_json, utc_now


class DriftKind(StrEnum):
    NONE = "NONE"
    NON_CONFLICTING = "NON_CONFLICTING"
    CONFLICT = "CONFLICT"
    BRANCH_CHANGED = "BRANCH_CHANGED"
    HEAD_CHANGED = "HEAD_CHANGED"
    REPOSITORY_CHANGED = "REPOSITORY_CHANGED"


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    path: str
    observed_at: str
    vcs_type: str | None
    branch: str | None
    head: str | None
    files: dict[str, dict[str, str | None]] = field(default_factory=dict)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "WorkspaceSnapshot":
        files = value.get("files") or {}
        if not isinstance(files, dict):
            raise ValidationError("Workspace snapshot files must be an object.")
        return cls(
            path=str(value["path"]),
            observed_at=str(value.get("observed_at") or ""),
            vcs_type=(
                str(value["vcs_type"]) if value.get("vcs_type") is not None else None
            ),
            branch=str(value["branch"]) if value.get("branch") is not None else None,
            head=str(value["head"]) if value.get("head") is not None else None,
            files={
                str(path): {
                    "status": (
                        str(metadata.get("status"))
                        if isinstance(metadata, dict)
                        and metadata.get("status") is not None
                        else None
                    ),
                    "hash": (
                        str(metadata.get("hash"))
                        if isinstance(metadata, dict)
                        and metadata.get("hash") is not None
                        else None
                    ),
                }
                for path, metadata in files.items()
            },
            fingerprint=str(value.get("fingerprint") or ""),
        )


@dataclass(frozen=True, slots=True)
class DriftReport:
    kind: DriftKind
    changed_paths: tuple[str, ...]
    conflicting_paths: tuple[str, ...]
    summary: str


class WorkspaceInspector:
    def __init__(self, git_binary: str | Path | None = None):
        resolved = str(git_binary) if git_binary else shutil.which("git")
        self.git_binary = Path(resolved).resolve() if resolved else None

    def snapshot(self, workspace_path: str | Path) -> WorkspaceSnapshot:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.is_dir():
            raise ValidationError(f"Workspace does not exist: {workspace}")
        if self.git_binary is None or not self.git_binary.is_file():
            return self._plain_snapshot(workspace)
        inside = self._git(
            workspace,
            "rev-parse",
            "--is-inside-work-tree",
            check=False,
        )
        if inside.returncode != 0 or inside.stdout.strip() != b"true":
            return self._plain_snapshot(workspace)

        root = Path(
            self._git_text(workspace, "rev-parse", "--show-toplevel")
        ).resolve()
        branch = self._git_text(
            root,
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        )
        head = self._git_text(root, "rev-parse", "HEAD")
        status_output = self._git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        statuses = self._parse_porcelain_z(status_output)
        files: dict[str, dict[str, str | None]] = {}
        for relative_path, status in sorted(statuses.items()):
            absolute = root / relative_path
            files[relative_path] = {
                "status": status,
                "hash": self._hash_file(absolute) if absolute.is_file() else None,
            }
        observed_at = utc_now()
        fingerprint = self._fingerprint(
            vcs_type="git",
            branch=branch,
            head=head,
            files=files,
        )
        return WorkspaceSnapshot(
            path=str(root),
            observed_at=observed_at,
            vcs_type="git",
            branch=branch,
            head=head,
            files=files,
            fingerprint=fingerprint,
        )

    def compare(
        self,
        baseline: WorkspaceSnapshot,
        current: WorkspaceSnapshot,
        *,
        relevant_files: tuple[str, ...] = (),
    ) -> DriftReport:
        if baseline.vcs_type != current.vcs_type:
            return DriftReport(
                kind=DriftKind.REPOSITORY_CHANGED,
                changed_paths=(),
                conflicting_paths=(),
                summary="Workspace version-control type changed.",
            )
        if Path(baseline.path).resolve() != Path(current.path).resolve():
            return DriftReport(
                kind=DriftKind.REPOSITORY_CHANGED,
                changed_paths=(),
                conflicting_paths=(),
                summary="Workspace root changed.",
            )
        if baseline.branch != current.branch:
            return DriftReport(
                kind=DriftKind.BRANCH_CHANGED,
                changed_paths=(),
                conflicting_paths=(),
                summary=f"Branch changed from {baseline.branch!r} to {current.branch!r}.",
            )
        if baseline.head != current.head:
            return DriftReport(
                kind=DriftKind.HEAD_CHANGED,
                changed_paths=(),
                conflicting_paths=(),
                summary=f"HEAD changed from {baseline.head!r} to {current.head!r}.",
            )

        all_paths = set(baseline.files) | set(current.files)
        changed = tuple(
            sorted(
                path
                for path in all_paths
                if baseline.files.get(path) != current.files.get(path)
            )
        )
        if not changed:
            return DriftReport(
                kind=DriftKind.NONE,
                changed_paths=(),
                conflicting_paths=(),
                summary="Workspace matches the checkpoint.",
            )

        relevant = {self._normalize_relative(path) for path in relevant_files}
        conflicting = tuple(path for path in changed if path in relevant)
        if conflicting:
            return DriftReport(
                kind=DriftKind.CONFLICT,
                changed_paths=changed,
                conflicting_paths=conflicting,
                summary="Workspace drift overlaps files relevant to the resumed task.",
            )
        return DriftReport(
            kind=DriftKind.NON_CONFLICTING,
            changed_paths=changed,
            conflicting_paths=(),
            summary="Workspace changed only outside the checkpoint's relevant files.",
        )

    def _plain_snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        observed_at = utc_now()
        fingerprint = self._fingerprint(
            vcs_type=None,
            branch=None,
            head=None,
            files={},
        )
        return WorkspaceSnapshot(
            path=str(workspace),
            observed_at=observed_at,
            vcs_type=None,
            branch=None,
            head=None,
            files={},
            fingerprint=fingerprint,
        )

    def _git_text(self, cwd: Path, *arguments: str) -> str:
        return self._git(cwd, *arguments).stdout.decode("utf-8").strip()

    def _git(
        self,
        cwd: Path,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if self.git_binary is None:
            raise ValidationError("Git is not available.")
        result = subprocess.run(
            [str(self.git_binary), *arguments],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check and result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(f"Git command failed: {message}")
        return result

    @staticmethod
    def _parse_porcelain_z(output: bytes) -> dict[str, str]:
        records = output.split(b"\0")
        statuses: dict[str, str] = {}
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            text = record.decode("utf-8", errors="surrogateescape")
            if len(text) < 4:
                continue
            status = text[:2]
            path = text[3:]
            statuses[WorkspaceInspector._normalize_relative(path)] = status
            if "R" in status or "C" in status:
                index += 1
        return statuses

    @staticmethod
    def _normalize_relative(path: str) -> str:
        normalized = path.replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fingerprint(
        *,
        vcs_type: str | None,
        branch: str | None,
        head: str | None,
        files: dict[str, dict[str, str | None]],
    ) -> str:
        encoded = canonical_json(
            {
                "branch": branch,
                "files": files,
                "head": head,
                "vcs_type": vcs_type,
            }
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
