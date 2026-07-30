---
status: accepted
---

# Separate per-user program files, application data, and visible backups

v1 will install per user under the Windows Local AppData Programs known folder,
keep mutable application state under a separate Local AppData product folder,
and place default backup archives in a user-visible Documents folder. This
avoids administrator privileges, prevents upgrades from overwriting durable
state, and lets users find and copy backups without exposing the database and
runtime internals as ordinary documents.

## Consequences

The application resolves Windows known folders through platform APIs rather
than concatenating environment-variable strings. Program binaries, data, logs,
worktrees, and backups have distinct roots. Upgrade and repair operations touch
only program files plus explicit forward migrations. Uninstall preserves all
user data by default and removes it only after a separate explicit choice.
Credentials remain in Windows secure credential storage and are never included
in application backups.
