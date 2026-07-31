"""Application-managed Git worktrees for isolated Task execution."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .authorization import ActionRequest, SideEffectCoordinator
from .errors import SideEffectUncertainError, ValidationError
from .models import WorktreeRecord, WorktreeState
from .store import SQLiteStore

_INVALID_PROJECT_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class RepositoryInspection:
    repository_path: str
    branch_name: str
    head_revision: str
    dirty_paths: tuple[str, ...]

    @property
    def is_dirty(self) -> bool:
        return bool(self.dirty_paths)


class WorktreeService:
    """Creates and removes worktrees without touching the source checkout."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        side_effects: SideEffectCoordinator,
        managed_root: str | Path,
        git_binary: str | Path | None = None,
    ):
        resolved_git = str(git_binary) if git_binary else shutil.which("git")
        if not resolved_git:
            raise ValidationError("Git is not available.")
        self.git_binary = Path(resolved_git).expanduser().resolve()
        if not self.git_binary.is_file():
            raise ValidationError(f"Git executable does not exist: {self.git_binary}")
        self.store = store
        self.side_effects = side_effects
        self.managed_root = Path(managed_root).expanduser().resolve()

    def path_for_task(self, task_id: str) -> Path:
        target = (self.managed_root / task_id).resolve()
        self._require_managed_target(target)
        return target

    def new_repository_path(
        self,
        parent_path: str | Path,
        project_name: str,
    ) -> Path:
        parent = Path(parent_path).expanduser().resolve()
        name = project_name.strip()
        if not parent.is_dir():
            raise ValidationError(f"Project location does not exist: {parent}")
        if (
            not name
            or name in {".", ".."}
            or len(name) > 100
            or name.endswith((" ", "."))
            or _INVALID_PROJECT_NAME.search(name)
            or name.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            raise ValidationError("Project name contains unsupported characters.")
        target = (parent / name).resolve()
        if target.parent != parent:
            raise ValidationError("Project path escapes the selected location.")
        if target.exists():
            raise ValidationError(f"Project path already exists: {target}")
        return target

    def initialize_repository(
        self,
        *,
        task_id: str,
        parent_path: str | Path,
        project_name: str,
    ) -> RepositoryInspection:
        target = self.new_repository_path(parent_path, project_name)
        action = ActionRequest(
            action_type="git.repository.create",
            logical_step="create-local-project-repository",
            parameters={
                "project_name": project_name.strip(),
                "repository_path": str(target),
            },
            risk_summary="Creates a new local project directory and Git repository.",
            rollback_plan="Remove the newly created project directory if setup fails.",
        )
        self.side_effects.execute(
            task_id,
            action,
            lambda _action: self._initialize_repository(
                target=target,
                project_name=project_name.strip(),
            ),
        )
        return self.inspect_repository(target)

    def inspect_repository(
        self,
        repository_path: str | Path,
    ) -> RepositoryInspection:
        selected = Path(repository_path).expanduser().resolve()
        if not selected.is_dir():
            raise ValidationError(f"Repository does not exist: {selected}")
        root = Path(
            self._git_text(selected, "rev-parse", "--show-toplevel")
        ).resolve()
        is_bare = self._git_text(root, "rev-parse", "--is-bare-repository")
        if is_bare != "false":
            raise ValidationError("Bare repositories are not supported in v1.")
        branch = self._git_text(root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            raise ValidationError("Detached HEAD repositories are not supported.")
        head = self._git_text(root, "rev-parse", "HEAD")
        status = self._git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        dirty = tuple(sorted(self._parse_status_paths(status)))
        return RepositoryInspection(
            repository_path=str(root),
            branch_name=branch,
            head_revision=head,
            dirty_paths=dirty,
        )

    def prepare(
        self,
        *,
        task_id: str,
        repository: RepositoryInspection,
    ) -> WorktreeRecord:
        target = self.path_for_task(task_id)
        if target.exists():
            raise ValidationError(f"Managed worktree path already exists: {target}")
        branch = f"aiao/task-{self._short_task_id(task_id)}"
        branch_ref = f"refs/heads/{branch}"
        branch_exists = self._git(
            Path(repository.repository_path),
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        )
        if branch_exists.returncode == 0:
            raise ValidationError(f"Task branch already exists: {branch}")

        record = self.store.create_worktree(
            task_id=task_id,
            repository_path=repository.repository_path,
            worktree_path=target,
            branch_name=branch,
            base_revision=repository.head_revision,
        )
        action = ActionRequest(
            action_type="git.worktree.create",
            logical_step="create-isolated-task-worktree",
            parameters={
                "base_revision": repository.head_revision,
                "branch_name": branch,
                "repository_path": repository.repository_path,
                "worktree_path": str(target),
            },
            risk_summary="Creates a local Git branch and linked worktree.",
            rollback_plan=(
                "Remove the clean linked worktree and retain or delete the "
                "unpublished task branch after inspection."
            ),
        )
        self.managed_root.mkdir(parents=True, exist_ok=True)
        try:
            self.side_effects.execute(
                task_id,
                action,
                lambda _action: self._create_worktree(
                    repository=Path(repository.repository_path),
                    target=target,
                    branch=branch,
                    base_revision=repository.head_revision,
                ),
            )
            self._validate_expected_worktree(record)
        except BaseException:
            self._mark_attention_if_creating(task_id)
            raise
        return self.store.update_worktree_state(
            task_id=task_id,
            expected=WorktreeState.CREATING,
            target=WorktreeState.ACTIVE,
        )

    def validate(self, task_id: str) -> WorktreeRecord:
        record = self.store.get_worktree(task_id)
        if record.state not in {
            WorktreeState.ACTIVE,
            WorktreeState.RETAINED,
        }:
            raise ValidationError(
                f"Worktree {task_id!r} is {record.state.value}, not usable."
            )
        try:
            self._validate_expected_worktree(record)
        except ValidationError:
            if record.state == WorktreeState.ACTIVE:
                self.store.update_worktree_state(
                    task_id=task_id,
                    expected=WorktreeState.ACTIVE,
                    target=WorktreeState.NEEDS_ATTENTION,
                )
            raise
        return record

    def retain(self, task_id: str) -> WorktreeRecord:
        record = self.store.get_worktree(task_id)
        if record.state == WorktreeState.RETAINED:
            return record
        if record.state != WorktreeState.ACTIVE:
            raise ValidationError(
                f"Only an ACTIVE worktree can be retained; found {record.state.value}."
            )
        return self.store.update_worktree_state(
            task_id=task_id,
            expected=WorktreeState.ACTIVE,
            target=WorktreeState.RETAINED,
        )

    def cleanup(
        self,
        task_id: str,
        *,
        approval_id: str,
    ) -> WorktreeRecord:
        task = self.store.get_task(task_id)
        if not task.state.is_terminal:
            raise ValidationError("Worktree cleanup requires a terminal Task.")
        record = self.store.get_worktree(task_id)
        if record.state not in {
            WorktreeState.ACTIVE,
            WorktreeState.RETAINED,
        }:
            raise ValidationError(
                f"Worktree cannot be cleaned while {record.state.value}."
            )
        self._validate_expected_worktree(record)
        status = self._git(
            Path(record.worktree_path),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        if status:
            raise ValidationError(
                "Dirty worktrees are retained and cannot be removed."
            )
        action = self.cleanup_action(task_id)
        try:
            self.side_effects.execute(
                task_id,
                action,
                lambda _action: self._remove_worktree(record),
                approval_id=approval_id,
            )
        except SideEffectUncertainError:
            self.store.update_worktree_state(
                task_id=task_id,
                expected=record.state,
                target=WorktreeState.NEEDS_ATTENTION,
            )
            raise
        return self.store.update_worktree_state(
            task_id=task_id,
            expected=record.state,
            target=WorktreeState.REMOVED,
        )

    def cleanup_action(self, task_id: str) -> ActionRequest:
        record = self.store.get_worktree(task_id)
        return ActionRequest(
            action_type="filesystem.delete.worktree",
            logical_step="remove-clean-task-worktree",
            parameters={
                "branch_name": record.branch_name,
                "repository_path": record.repository_path,
                "worktree_path": record.worktree_path,
            },
            risk_summary="Removes the clean linked worktree directory.",
            rollback_plan=(
                "Recreate the worktree from the retained local task branch."
            ),
        )

    def _create_worktree(
        self,
        *,
        repository: Path,
        target: Path,
        branch: str,
        base_revision: str,
    ) -> str:
        self._git(
            repository,
            "worktree",
            "add",
            "-b",
            branch,
            str(target),
            base_revision,
        )
        return base_revision

    def _initialize_repository(self, *, target: Path, project_name: str) -> str:
        target.mkdir(parents=False, exist_ok=False)
        try:
            self._git(target, "init", "--initial-branch=main")
            (target / "README.md").write_text(
                f"# {project_name}\n",
                encoding="utf-8",
            )
            self._git(target, "add", "README.md")
            self._git(
                target,
                "-c",
                "user.name=AI Agent Orchestrator",
                "-c",
                "user.email=local@aiao.invalid",
                "commit",
                "-m",
                "Initialize project",
            )
            return self._git_text(target, "rev-parse", "HEAD")
        except BaseException:
            if target.is_dir():
                shutil.rmtree(target)
            raise

    def _remove_worktree(self, record: WorktreeRecord) -> str:
        self._git(
            Path(record.repository_path),
            "worktree",
            "remove",
            str(Path(record.worktree_path)),
        )
        return record.branch_name

    def _validate_expected_worktree(self, record: WorktreeRecord) -> None:
        target = Path(record.worktree_path).resolve()
        self._require_managed_target(target)
        if not target.is_dir():
            raise ValidationError("Managed worktree directory is missing.")
        root = Path(
            self._git_text(target, "rev-parse", "--show-toplevel")
        ).resolve()
        if root != target:
            raise ValidationError("Managed worktree resolves to an unexpected root.")
        branch = self._git_text(target, "rev-parse", "--abbrev-ref", "HEAD")
        if branch != record.branch_name:
            raise ValidationError(
                f"Worktree branch changed from {record.branch_name!r} to {branch!r}."
            )

    def _mark_attention_if_creating(self, task_id: str) -> None:
        try:
            current = self.store.get_worktree(task_id)
            if current.state == WorktreeState.CREATING:
                self.store.update_worktree_state(
                    task_id=task_id,
                    expected=WorktreeState.CREATING,
                    target=WorktreeState.NEEDS_ATTENTION,
                )
        except Exception:
            return

    def _require_managed_target(self, target: Path) -> None:
        try:
            target.relative_to(self.managed_root)
        except ValueError as exc:
            raise ValidationError(
                "Worktree target escapes the managed worktree root."
            ) from exc
        if target == self.managed_root:
            raise ValidationError("The managed root itself cannot be a worktree.")

    def _git(
        self,
        cwd: Path,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            [str(self.git_binary), *arguments],
            cwd=cwd,
            env=self._safe_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            creationflags=creation_flags,
        )
        if check and result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(f"Git command failed: {message}")
        return result

    def _git_text(self, cwd: Path, *arguments: str) -> str:
        return self._git(cwd, *arguments).stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
        }
        return {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed
        }

    @staticmethod
    def _parse_status_paths(output: bytes) -> set[str]:
        records = output.split(b"\0")
        paths: set[str] = set()
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            text = record.decode("utf-8", errors="surrogateescape")
            if len(text) >= 4:
                paths.add(text[3:].replace("\\", "/"))
            if len(text) >= 2 and ("R" in text[:2] or "C" in text[:2]):
                index += 1
        return paths

    @staticmethod
    def _short_task_id(task_id: str) -> str:
        compact = "".join(character for character in task_id if character.isalnum())
        return compact[-12:].lower() or "task"
