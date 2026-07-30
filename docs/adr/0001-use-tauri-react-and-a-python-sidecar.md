---
status: accepted
---

# Use Tauri, React, and a Python sidecar for the Windows desktop application

The v1 desktop application will use Tauri 2 for the Windows shell and native
capabilities, React with TypeScript for the user interface, and the existing
Python orchestrator as a separately packaged background sidecar. This keeps the
durable Python domain core intact while providing a maintainable modern UI and
a direct path to a Windows installer without accepting Electron's bundled
browser footprint or constraining the product to a prototype-oriented webview
wrapper.

## Consequences

The application build requires Rust, Node.js, and Python toolchains, but end
users require none of them. The Tauri shell owns sidecar startup, shutdown,
single-instance behavior, tray integration, file dialogs, and local
notifications. React never accesses SQLite or credentials directly; privileged
operations cross a narrow Tauri-to-sidecar boundary.
