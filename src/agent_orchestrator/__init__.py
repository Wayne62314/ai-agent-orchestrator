"""Persistent, event-driven orchestration primitives."""

from .authorization import (
    ActionRequest,
    ApprovalService,
    PermissionDecision,
    PermissionPolicy,
    SideEffectCoordinator,
)
from .checkpoint import CheckpointService
from .demo import CiDemoEvidence, CiDemoPreparation, CiWebhookDemo
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
from .webhook_server import GitHubWebhookApplication, build_webhook_server
from .worker import RecoveryWorker, WorkerTickResult
from .workspace import DriftKind, WorkspaceInspector

__all__ = [
    "ActionRequest",
    "ApprovalService",
    "AuditEntry",
    "CheckpointService",
    "CiDemoEvidence",
    "CiDemoPreparation",
    "CiWebhookDemo",
    "ConstrainedCommandExecutor",
    "DriftKind",
    "Event",
    "EventResult",
    "ExecutionCoordinator",
    "ExecutionRepairAction",
    "ExternalEventKind",
    "ExternalEventRecord",
    "HmacSha256Authenticator",
    "GitHubWebhookApplication",
    "LimitedRepairLoop",
    "OrchestratorService",
    "PermissionDecision",
    "PermissionPolicy",
    "ResumePackage",
    "ResumePackageBuilder",
    "RecoveryWorker",
    "SQLiteStore",
    "SensitiveDataRedactor",
    "SideEffectCoordinator",
    "SignalWaitRecord",
    "Task",
    "TaskState",
    "TrustedEventService",
    "VerificationCoordinator",
    "VerificationPolicy",
    "WorkerTickResult",
    "WorkspaceInspector",
    "build_webhook_server",
]

__version__ = "0.8.0"
