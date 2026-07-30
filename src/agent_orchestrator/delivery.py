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
            "## Verification evidence",
            "",
        ]
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
            lines.extend(["No verification records were produced.", ""])
        lines.extend(
            [
                "## Outcome",
                "",
                (
                    "All required acceptance checks passed."
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
