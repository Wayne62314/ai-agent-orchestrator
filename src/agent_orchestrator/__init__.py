"""Persistent, event-driven orchestration primitives."""

from .models import AuditEntry, Event, EventResult, Task, TaskState
from .checkpoint import CheckpointService
from .execution import ExecutionCoordinator
from .resume import ResumePackage, ResumePackageBuilder
from .service import OrchestratorService
from .store import SQLiteStore
from .verification import (
    ConstrainedCommandExecutor,
    ExecutionRepairAction,
    LimitedRepairLoop,
    VerificationCoordinator,
    VerificationPolicy,
)
from .workspace import DriftKind, WorkspaceInspector

__all__ = [
    "AuditEntry",
    "CheckpointService",
    "ConstrainedCommandExecutor",
    "DriftKind",
    "Event",
    "EventResult",
    "ExecutionCoordinator",
    "ExecutionRepairAction",
    "LimitedRepairLoop",
    "OrchestratorService",
    "ResumePackage",
    "ResumePackageBuilder",
    "SQLiteStore",
    "Task",
    "TaskState",
    "VerificationCoordinator",
    "VerificationPolicy",
    "WorkspaceInspector",
]

__version__ = "0.3.0"
