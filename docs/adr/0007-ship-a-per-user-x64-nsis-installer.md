---
status: accepted
---

# Ship a per-user x64 NSIS installer with guarded migrations

v1 will ship one Tauri-generated NSIS `Setup.exe` for 64-bit Windows 10 22H2
and Windows 11. It installs per user without elevation, bundles the Python and
Codex runtime components, and embeds the small WebView2 Evergreen bootstrapper.
MSI, ARM64, and a fully offline WebView2 bundle are deferred because they add
release-matrix and installer weight without improving the initial
internet-dependent Codex workflow.

## Consequences

Manual updates reuse a stable installer identity. Before replacing binaries or
running a forward-only database migration, the installer or first-run migrator
creates and verifies a local backup. Migration failure leaves the previous
version and backup recoverable and prevents the application from opening a
partially migrated database. Downgrades across an incompatible Schema are
blocked. Uninstall preserves user data unless the user separately and
explicitly requests its removal.
