"""Persistent, event-driven orchestration primitives."""

from .authorization import (
    ActionRequest,
    ApprovalService,
    PermissionDecision,
    PermissionPolicy,
    SideEffectCoordinator,
)
from .checkpoint import CheckpointService
from .execution import ExecutionCoordinator
from .external_events import HmacSha256Authenticator, TrustedEventService
from .models import (
    AuditEntry,
    Event,
    EventResult,
    ExternalEventKind,
    ExternalEventRecord,
    SignalWaitRecord,
    Task,
    TaskState,
)
from .resume import ResumePackage, ResumePackageBuilder
from .security import SensitiveDataRedactor
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
    "ActionRequest",
    "ApprovalService",
    "AuditEntry",
    "CheckpointService",
    "ConstrainedCommandExecutor",
    "DriftKind",
    "Event",
    "EventResult",
    "ExecutionCoordinator",
    "ExecutionRepairAction",
    "ExternalEventKind",
    "ExternalEventRecord",
    "HmacSha256Authenticator",
    "LimitedRepairLoop",
    "OrchestratorService",
    "PermissionDecision",
    "PermissionPolicy",
    "ResumePackage",
    "ResumePackageBuilder",
    "SQLiteStore",
    "SensitiveDataRedactor",
    "SideEffectCoordinator",
    "SignalWaitRecord",
    "Task",
    "TaskState",
    "TrustedEventService",
    "VerificationCoordinator",
    "VerificationPolicy",
    "WorkspaceInspector",
]

__version__ = "0.5.0"
