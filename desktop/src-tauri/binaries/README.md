# Packaged desktop sidecar

Stage 10 builds the private Python RPC server into:

```text
agent-orchestrator-sidecar-x86_64-pc-windows-msvc.exe
```

The executable, its private `agent-orchestrator-sidecar-runtime` directory and
its build manifest are generated files and are not committed. Directory mode
avoids extracting the approximately 140 MB Codex runtime on every application
start; the eventual NSIS installer is still a single `Setup.exe`.
From the repository root, install the `desktop-build` optional dependencies and
run:

```text
powershell -File packaging/build-windows-sidecar.ps1 -Python path/to/python.exe
```

Tauri removes the target-triple suffix when it copies the sidecar beside the
desktop executable.
