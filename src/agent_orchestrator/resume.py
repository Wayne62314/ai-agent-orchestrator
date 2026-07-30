"""Resume Package construction from verified checkpoint and current facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ValidationError
from .models import CheckpointRecord, Task
from .security import SensitiveDataRedactor
from .workspace import DriftKind, DriftReport, WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class ResumePackage:
    task_id: str
    checkpoint_id: str
    thread_id: str | None
    drift: DriftReport
    prompt: str
    structured: Mapping[str, Any]


class ResumePackageBuilder:
    BLOCKING_DRIFT = {
        DriftKind.CONFLICT,
        DriftKind.BRANCH_CHANGED,
        DriftKind.HEAD_CHANGED,
        DriftKind.REPOSITORY_CHANGED,
    }

    def __init__(self, *, redactor: SensitiveDataRedactor | None = None):
        self.redactor = redactor or SensitiveDataRedactor()

    def build(
        self,
        *,
        task: Task,
        checkpoint: CheckpointRecord,
        payload: Mapping[str, Any],
        current_workspace: WorkspaceSnapshot,
        drift: DriftReport,
        thread_id: str | None,
    ) -> ResumePackage:
        if drift.kind in self.BLOCKING_DRIFT:
            raise ValidationError(
                f"Resume is blocked by workspace drift: {drift.summary}"
            )
        task_section = self._object(payload, "task")
        progress = self._object(payload, "progress")
        current_block = self._object(payload, "current_block")
        next_action = self._object(payload, "next_action")
        permissions = self._object(payload, "permissions")
        verification = self._object(payload, "verification")
        structured = self.redactor.redact({
            "task_contract": task_section,
            "current_workspace": current_workspace.to_dict(),
            "completed": progress.get("completed", []),
            "in_progress": progress.get("in_progress", []),
            "pending": progress.get("pending", []),
            "pause_reason": current_block,
            "drift": {
                "kind": drift.kind.value,
                "summary": drift.summary,
                "changed_paths": list(drift.changed_paths),
            },
            "next_action": next_action,
            "permissions": permissions,
            "verification": verification,
            "output_protocol": {
                "required_fields": [
                    "completed",
                    "modified_files",
                    "checks",
                    "unresolved",
                    "recommended_state",
                ]
            },
        })
        prompt = self._render(task, checkpoint, thread_id, structured)
        prompt = self.redactor.redact_text(prompt)
        return ResumePackage(
            task_id=task.task_id,
            checkpoint_id=checkpoint.checkpoint_id,
            thread_id=thread_id,
            drift=drift,
            prompt=prompt,
            structured=structured,
        )

    @staticmethod
    def _render(
        task: Task,
        checkpoint: CheckpointRecord,
        thread_id: str | None,
        value: Mapping[str, Any],
    ) -> str:
        workspace = value["current_workspace"]
        drift = value["drift"]
        return "\n".join(
            (
                f"You are resuming task {task.task_id} from checkpoint "
                f"{checkpoint.checkpoint_id}.",
                "",
                "TASK CONTRACT",
                f"Objective: {task.objective}",
                f"Acceptance policy: {value['task_contract'].get('acceptance_criteria', {})}",
                "",
                "CURRENT WORKSPACE FACTS",
                f"Path: {workspace.get('path')}",
                f"Branch: {workspace.get('branch')}",
                f"HEAD: {workspace.get('head')}",
                f"Drift: {drift.get('kind')} — {drift.get('summary')}",
                "",
                "VERIFIED PROGRESS",
                f"Completed: {value['completed']}",
                f"In progress: {value['in_progress']}",
                f"Pending: {value['pending']}",
                "",
                "WHY THE PREVIOUS RUN STOPPED",
                f"{value['pause_reason']}",
                "",
                "NEXT ACTION",
                f"{value['next_action']}",
                "",
                "PERMISSION BOUNDARY",
                f"{value['permissions']}",
                "",
                "OUTPUT CONTRACT",
                "Return: completed, modified_files, checks, unresolved, "
                "recommended_state.",
                f"Previous Codex thread: {thread_id or 'none'}",
            )
        )

    @staticmethod
    def _object(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise ValidationError(f"Checkpoint section {key!r} must be an object.")
        return value
