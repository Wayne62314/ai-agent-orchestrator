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

## Windows installer

Stage 10 builds one per-user x64 NSIS installer. It does not require
administrator rights and installs under the current user's local application
directory. From a Windows build environment:

```text
pnpm tauri build --bundles nsis
powershell -File ../packaging/collect-windows-installer.ps1
```

The collector gives the setup executable a stable versioned name and emits its
SHA-256 plus a JSON build manifest. CI retains these three files as the
`ai-agent-orchestrator-windows-installer` artifact.

Interactive installation creates a Start menu entry and offers the desktop
shortcut on the finish page. Login startup is a separate prompt and defaults to
No. Silent deployment can opt in with `/AUTOSTART`; `/NS` suppresses shortcuts.
An update preserves the existing login-start choice. Uninstall removes program
files and shortcuts, while the standard data-deletion checkbox remains
unchecked so tasks, settings and backups are preserved by default.

Before a database schema upgrade, the sidecar writes a verified backup and
SHA-256 manifest under `backups/pre-upgrade`. Migrations run against a private
copy; only a fully migrated database with the expected version history,
integrity check and foreign-key check replaces the live file. A failed
migration leaves the original database untouched and prevents startup. An
older application also refuses to open a database created by a newer schema.

Windows CI installs the collected setup executable into an isolated per-user
directory, verifies the installed sidecar, Start menu and desktop shortcuts,
then silently uninstalls it and proves both roaming and local app-data
sentinels remain. A second cycle verifies explicit `/AUTOSTART` registration
and its removal during uninstall.

Release-candidate validation also runs on fresh `windows-2022` and
`windows-2025` hosted VMs without invoking a development Python entry point.
It launches the installed Tauri application until the real application
database is created, then scans the exact hash-bound setup executable with
Microsoft Defender.

The upgrade lane rebuilds the immutable 0.10.0 installer from its approved Git
commit, creates a real task and retained worktree with its packaged sidecar,
installs 0.12.0 over the same program directory, and verifies the task,
worktree, user backup, Schema 6-to-7 safety backup, and default-uninstall data
all remain. JSON evidence from both upgrade and Defender checks is retained
with the candidate artifacts.

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
