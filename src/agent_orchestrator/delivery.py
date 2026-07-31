"""Evidence-focused final delivery reports."""

from __future__ import annotations

import os
from pathlib import Path

from .store import SQLiteStore, utc_now


class DeliveryReportBuilder:
    def __init__(self, store: SQLiteStore, *, report_root: str | Path | None = None):
        self.store = store
        self.report_root = Path(report_root).resolve() if report_root else None

    def write(self, task_id: str) -> Path:
        task = self.store.get_task(task_id)
        records = self.store.list_verifications(task_id)
        audit = self.store.list_audit(task_id=task_id, limit=5000)
        ai_reviews = [
            entry for entry in audit if entry.kind == "AI_VERIFICATION_RECORDED"
        ]
        confirmations = [
            entry
            for entry in audit
            if entry.kind == "MANUAL_CONFIRMATION_RECORDED"
            and (
                not ai_reviews
                or entry.sequence > ai_reviews[-1].sequence
            )
        ]
        root = self.report_root or Path(task.workspace_path) / ".orchestrator" / "reports"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{task_id}.md"
        attempts = sorted({record.attempt for record in records})
        lines = [
            f"# Delivery report: {task.title}",
            "",
            f"- Task: `{task.task_id}`",
            f"- Final state: `{task.state.value}`",
            f"- Generated: `{utc_now()}`",
            f"- Audit chain valid: `{str(self.store.verify_audit_chain(task_id)).lower()}`",
            "",
            "## Objective",
            "",
            task.objective,
            "",
            "## AI assessment",
            "",
        ]
        if ai_reviews:
            review = ai_reviews[-1]
            lines.extend(
                [
                    f"- Status: `{review.payload.get('status', 'UNKNOWN')}`",
                    f"- Source: `{review.payload.get('source', 'codex-self-review')}`",
                    "- Independence: `false` (the executing AI reviewed its own work)",
                    f"- Summary: {review.payload.get('summary', '')}",
                    "",
                ]
            )
        else:
            lines.extend(["No AI assessment was recorded.", ""])
        lines.extend(["## Project command results", ""])
        for attempt in attempts:
            lines.extend([f"### Attempt {attempt}", ""])
            for record in (item for item in records if item.attempt == attempt):
                requirement = "required" if record.required else "optional"
                timeout = ", timed out" if record.timed_out else ""
                lines.append(
                    f"- **{record.check_name}**: `{record.status}` "
                    f"({requirement}, exit={record.exit_code}, "
                    f"{record.duration_ms} ms{timeout}) — `{record.log_path}`"
                )
            lines.append("")
        if not records:
            lines.extend(
                [
                    "No project commands were configured or executed. "
                    "This does not mean that tests passed.",
                    "",
                ]
            )
        lines.extend(["## Manual confirmation", ""])
        if confirmations:
            confirmation = confirmations[-1]
            lines.extend(
                [
                    f"- Status: `{confirmation.payload.get('status', 'UNKNOWN')}`",
                    f"- Source: `{confirmation.payload.get('source', 'desktop-user')}`",
                    "",
                ]
            )
        elif _manual_confirmation_required(task.acceptance_policy):
            lines.extend(["Manual confirmation is required and still pending.", ""])
        else:
            lines.extend(["Manual confirmation was not required.", ""])
        lines.extend(
            [
                "## Outcome",
                "",
                (
                    "The selected acceptance requirements were satisfied. "
                    "AI assessment, command results, and manual confirmation "
                    "remain separate evidence types."
                    if task.state.value == "SUCCEEDED"
                    else "The repair budget was exhausted; human attention is required."
                ),
                "",
            ]
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(lines), encoding="utf-8")
        os.replace(temporary, path)
        self.store.append_audit(
            task_id=task_id,
            run_id=None,
            kind="DELIVERY_REPORT_WRITTEN",
            payload={"path": str(path), "state": task.state.value},
        )
        return path


def _manual_confirmation_required(policy: object) -> bool:
    if not isinstance(policy, dict):
        return False
    value = policy.get("manual_confirmation", False)
    if isinstance(value, bool):
        return value
    return isinstance(value, dict) and value.get("required") is True
