# Codex window docking spike

This developer-only Windows prototype tests whether the installed official
Codex desktop window can be hosted inside an Agent Dock window without
reimplementing Codex UI.

The V2 prototype:

- discovers a visible `ChatGPT.exe` top-level window;
- verifies that it comes from the installed `OpenAI.Codex` Windows package;
- watches the official window's native move/resize events without polling;
- displays a click-through magnetic preview when Codex approaches the dock;
- attaches only after the user releases the window inside the magnetic range;
- reparents that exact native window into a WinForms host panel;
- resizes it with the host;
- provides a host-owned drag handle that detaches Codex and returns it to native
  window movement;
- restores its parent, styles, and placement when detached or when the host
  closes;
- records restoration data before attachment so the next launch can recover
  from an abnormal previous exit;
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

## Test magnetic docking

1. Open this prototype and the official Codex desktop app side by side.
2. Drag the official Codex title bar toward the large central slot.
3. When the translucent preview says that Codex can be attached, release the
   mouse.
4. After attachment, drag the green strip above Codex to detach it again.

The preview is click-through and does not take keyboard or mouse focus from
Codex. Passing near the slot without pausing and releasing does not attach the
window.

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
