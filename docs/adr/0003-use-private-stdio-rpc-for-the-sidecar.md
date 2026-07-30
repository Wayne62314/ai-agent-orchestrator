---
status: accepted
---

# Use private stdio RPC between the desktop host and Python sidecar

The Tauri host will spawn the Python sidecar and communicate over a
versioned, newline-delimited JSON RPC protocol on the child's standard input
and output. The desktop product will not expose an HTTP or WebSocket listener
by default. This avoids loopback-port discovery, origin validation, CSRF,
session-token handling, and port conflicts while preserving a streaming,
request-correlated protocol suitable for long-running Tasks.

## Consequences

Tauri exposes a narrow allowlist of commands and events to React; React cannot
address the sidecar directly. Protocol output is reserved for framed messages,
while diagnostics use standard error and redacted log files. The host validates
the protocol version during startup, supervises the child process, bounds
message sizes and queues, and treats malformed or unsolicited privileged
messages as faults. Existing HTTP and webhook entry points remain optional
development or future integration surfaces and are not started by the v1
desktop application.
