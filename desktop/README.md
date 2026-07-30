# Desktop UI

This directory contains the stage 9 React and TypeScript desktop interface.
During browser development it uses the in-memory fake transport. Inside Tauri,
the same typed bridge calls the private `sidecar_request` command instead.

## Local development

Prerequisites:

- Node.js 24;
- pnpm 11.9.0.

Run:

```text
pnpm install
pnpm dev
```

Then open `http://127.0.0.1:1420`.

## Verification

```text
pnpm test
pnpm build
```

The fake transport exists only for deterministic UI development and tests. It
does not write SQLite. Production state continues to come from the Python
application service through the versioned, bounded JSONL protocol.

## Current boundary

This first stage 9 increment includes:

- first-run guidance;
- dashboard and stable task state labels;
- task creation wizard;
- pause, resume and cancel controls;
- task details, approvals and settings views;
- a strict read-only Python RPC boundary;
- automated browser-level component journeys.

The next increment adds the native Tauri command, packaged Python sidecar and
real application-service mappings for state-changing operations.

## Native development

The Tauri 2 shell is in `src-tauri`. It exposes one command,
`sidecar_request`, and validates the protocol, method allowlist, request size,
request id and response timeout before forwarding anything to Python.

Development mode starts Python with:

```text
python -m agent_orchestrator.desktop_rpc
```

Set `AIAO_PYTHON` only when a different trusted Python executable is required.
`AIAO_SIDECAR_PATH` is reserved for testing the self-contained executable that
will be produced during stage 10.

Native prerequisites:

- Rust stable;
- Microsoft C++ Build Tools and a Windows SDK;
- WebView2.

Run:

```text
pnpm tauri:dev
```

The stage 9 build does not create an installer. Bundling remains disabled until
the stage 10 packaging and upgrade design is implemented.

## Account and repository onboarding

The native application reads account state from the same official Codex SDK
client used for execution. Browser, device-code and API-key login are delegated
to Codex; the desktop database never stores credentials.

Repository selection uses the Tauri system folder dialog. The selected path is
then inspected by the Python `WorktreeService`, which reports the canonical
repository root, branch, HEAD and uncommitted paths without modifying the
source checkout.

## Background execution

After a desktop start or resume command returns, the Python sidecar owns Run
collection and lease heartbeats. Pause and cancel only request interruption;
the background collector remains the sole durable settlement path. The UI
rebuilds its view from SQLite every 2.5 seconds, and expired Runs are converted
to explicit restart-recovery checkpoints.
