# Codex window docking spike

This developer-only Windows prototype tests whether the installed official
Codex desktop window can be hosted inside an Agent Dock window without
reimplementing Codex UI.

The prototype:

- discovers a visible `ChatGPT.exe` top-level window;
- reparents that exact native window into a WinForms host panel;
- resizes it with the host;
- restores its parent, styles, and placement when detached or when the host
  closes;
- opens a new official Codex task through the documented `codex://` deep link.

It deliberately does not render a Codex-like chat interface and does not copy,
scrape, or persist Codex credentials or content.

## Run

```powershell
dotnet run --project .\spikes\codex-window-docking\CodexWindowDockingSpike.csproj
```

Use **Attach real Codex window** only after the official Codex desktop app has
a visible window. Always use **Safely detach** before closing during manual
testing; closing the host also attempts the same restoration automatically.

## Probe without attaching

```powershell
dotnet run --project .\spikes\codex-window-docking\CodexWindowDockingSpike.csproj -- --probe --output .\codex-window-probe.json
```

The probe only enumerates eligible windows. It does not move or modify them.

## Scope

This is a feasibility spike, not production code. Native reparenting uses
undocumented behavior from the perspective of the official Codex product and
must not become the only supported integration path. The production design
must retain a deep-link and App Server fallback.
