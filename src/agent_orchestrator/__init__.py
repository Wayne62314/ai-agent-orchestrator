"""Persistent, event-driven orchestration primitives."""

from .models import AuditEntry, Event, EventResult, Task, TaskState
from .checkpoint import CheckpointService
from .execution import ExecutionCoordinator
from .resume import ResumePackage, ResumePackageBuilder
from .service import OrchestratorService
from .store import SQLiteStore
from .workspace import DriftKind, WorkspaceInspector

__all__ = [
    "AuditEntry",
    "CheckpointService",
    "DriftKind",
    "Event",
    "EventResult",
    "ExecutionCoordinator",
    "OrchestratorService",
    "ResumePackage",
    "ResumePackageBuilder",
    "SQLiteStore",
    "Task",
    "TaskState",
    "WorkspaceInspector",
]

__version__ = "0.2.0"
