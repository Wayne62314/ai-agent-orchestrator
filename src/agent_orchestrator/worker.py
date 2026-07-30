"""Periodic recovery work for durable waits and uncertain side effects."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .external_events import TrustedEventService
from .service import OrchestratorService
from .store import SQLiteStore, utc_now

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerTickResult:
    observed_at: str
    expired_waits: int
    recovered_side_effects: int


class RecoveryWorker:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        service: OrchestratorService,
        interval_seconds: float = 5.0,
        stale_effect_seconds: int = 300,
    ):
        if interval_seconds <= 0:
            raise ValueError("Worker interval must be positive.")
        if stale_effect_seconds < 1:
            raise ValueError("Stale side-effect age must be positive.")
        self.store = store
        self.events = TrustedEventService(store=store, service=service)
        self.interval_seconds = interval_seconds
        self.stale_effect_seconds = stale_effect_seconds

    def tick(self, *, observed_at: str | None = None) -> WorkerTickResult:
        timestamp = observed_at or utc_now()
        expired = self.events.expire_waits(observed_at=timestamp)
        recovered = self.store.mark_stale_side_effects_unknown(
            older_than_seconds=self.stale_effect_seconds
        )
        return WorkerTickResult(
            observed_at=timestamp,
            expired_waits=len(expired),
            recovered_side_effects=len(recovered),
        )

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                LOGGER.exception("Recovery worker tick failed.")
            stop_event.wait(self.interval_seconds)
