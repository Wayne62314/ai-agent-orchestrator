# Desktop UI

This directory contains the React and TypeScript desktop interface.
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

## Packaged sidecar

Stage 10 packages the Python RPC server, the pinned `openai-codex` SDK and its
matching Windows Codex runtime without requiring a system Python installation.
From the repository root:

```text
python -m pip install ".[desktop-build]"
powershell -File packaging/build-windows-sidecar.ps1 -Python python
```

The script creates the target-triple-suffixed executable expected by Tauri, its
private runtime directory and a local JSON build manifest containing versions,
sizes and the executable SHA-256. It then runs `--self-check` against the frozen
binary. Generated binaries and manifests are intentionally not committed.

## Native development

The Tauri 2 shell is in `src-tauri`. It exposes one command,
`sidecar_request`, and validates the protocol, method allowlist, request size,
request id and response timeout before forwarding anything to Python.

Development mode starts Python with:

```text
python -m agent_orchestrator.desktop_rpc
```

Set `AIAO_PYTHON` only when a different trusted Python executable is required.
`AIAO_SIDECAR_PATH` can override the discovered packaged executable for trusted
development tests. A packaged executable is preferred automatically in normal
desktop builds; the system Python fallback is development-only.

Native prerequisites:

- Rust stable;
- Microsoft C++ Build Tools and a Windows SDK;
- WebView2.

Run:

```text
pnpm tauri:dev
```

Installer bundling remains disabled in this first stage 10 increment. The next
increment enables the approved per-user x64 NSIS target and its installation
options after the packaged sidecar input has passed CI.

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

## Durable task details

The task detail tabs call the private `task/detail` RPC on demand. Activity,
Run, Checkpoint and verification records use bounded cursor pagination; the
delivery report is rebuilt from durable verification and audit evidence.
Returned values pass through the shared redactor, and raw command output or
checkpoint payloads are never sent to the renderer.

Each tab has loading, empty and recoverable error states. Responses are bound
to the tab that requested them so a slow result cannot be rendered as a
different evidence type after rapid navigation.

## Maintenance and notifications

The settings page calls product-owned maintenance RPC methods for consistent
SQLite backup, guarded restore, and redacted diagnostic export. Restore accepts
only a registered file from the application backup directory, requires an exact
confirmation value, refuses to run while a task is active, and creates a
pre-restore safety backup.

Diagnostic archives contain runtime versions, database integrity, task counts,
and audit event metadata. They exclude the database, credentials, source code,
prompts, raw command output, and checkpoint payloads.

Windows notifications use the Tauri notification plugin and are disabled until
the user grants permission in settings. Notifications are limited to task
completion, approval waits, and attention states.
