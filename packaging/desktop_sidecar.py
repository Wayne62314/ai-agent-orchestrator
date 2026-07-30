"""PyInstaller entry point for the private desktop RPC sidecar."""

from agent_orchestrator.desktop_rpc import main

if __name__ == "__main__":
    raise SystemExit(main())
