# Repository guidance

## Scope

This repository implements a resumable, event-driven orchestrator for AI
development tasks.

## Invariants

- Persisted task state changes only through `OrchestratorService.process_event`.
- Every applied or rejected state-changing event must produce an audit entry.
- Event retries must be idempotent by both event id and dedupe key.
- Provider-specific imports stay under `agent_orchestrator.adapters`.
- The persistence and state-machine core must run without Codex SDK installed.
- Credentials, tokens, and raw authentication records must never enter SQLite,
  checkpoints, audit payloads, fixtures, or test output.
- Live Codex execution defaults to read-only; workspace-write must be explicit.
- Remote App Server WebSocket transport is out of scope; use local stdio.
- High-risk external actions require an unexpired approval bound to the exact
  normalized action hash.
- External side effects must pass through the idempotent side-effect ledger;
  `PENDING` or `UNKNOWN` effects are never blindly replayed.
- Human-readable and durable artifacts must pass through the shared
  sensitive-data redactor before persistence.

## Verification

Run:

```text
python -m unittest discover -s tests -v
```

Before changing the SQLite schema, add a forward-only migration and a restart
test. Before adding a state transition, update both the documented state table
and state-machine tests.
