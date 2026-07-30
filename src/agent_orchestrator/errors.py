"""Domain-specific errors."""


class OrchestratorError(Exception):
    """Base class for orchestrator failures."""


class NotFoundError(OrchestratorError):
    """Raised when an entity does not exist."""


class InvalidTransitionError(OrchestratorError):
    """Raised when an event is not legal for the current task state."""


class ConcurrencyError(OrchestratorError):
    """Raised when optimistic concurrency detects a stale task version."""


class ValidationError(OrchestratorError):
    """Raised when a command or model violates a domain invariant."""


class AdapterUnavailableError(OrchestratorError):
    """Raised when an optional execution adapter is not installed or configured."""


class AdapterNotImplementedError(OrchestratorError):
    """Raised for a deliberately deferred adapter operation."""


class AuthorizationDeniedError(OrchestratorError):
    """Raised when an action lacks an explicit policy grant or valid approval."""


class SideEffectUncertainError(OrchestratorError):
    """Raised when an external side effect cannot be safely retried."""


class SourceAuthenticationError(OrchestratorError):
    """Raised when an external signal cannot prove its configured source."""
