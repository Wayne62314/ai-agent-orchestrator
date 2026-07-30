---
status: accepted
---

# Let Codex own authentication and credential storage

The desktop application will use the stable Codex App Server account methods
for account status, ChatGPT browser login, device-code fallback, API-key login,
and logout. Codex, rather than React, Tauri, or the Python orchestrator, owns
token persistence and refresh. On Windows, v1 configures
`cli_auth_credentials_store = "keyring"` and fails visibly if secure credential
storage is unavailable instead of silently falling back to plaintext
`auth.json`.

## Consequences

ChatGPT login is the primary first-run path and API-key login is an alternate
path. The desktop UI may display non-secret account metadata returned by
`account/read`, but credentials and raw authentication messages never enter
SQLite, Checkpoints, application logs, crash diagnostics, or UI persistence.
Externally managed ChatGPT tokens are excluded because that App Server mode is
experimental and would make this product responsible for refresh and
revocation.
