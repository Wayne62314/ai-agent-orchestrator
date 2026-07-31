---
status: proposed
---

# Host the real Codex window and retain a supported fallback

The Agent Dock must never implement a visual imitation of Codex. The central
executor area hosts the installed official Codex desktop window when Windows
allows a safe attachment. The visible process, account, task history, composer,
and agent output remain owned and rendered by the official Codex application.

Window attachment is a Windows integration technique, not a documented Codex
extension API. It is therefore an optional presentation enhancement rather than
the product's only integration path.

The durable integration has three layers:

1. documented `codex://` deep links open a new or existing official Codex task;
2. the documented local App Server protocol provides structured task and turn
   coordination where the product needs machine-readable state;
3. native Windows window attachment places the real Codex window inside the
   Agent Dock when compatible.

If native attachment is unavailable or fails, Agent Dock opens the same
official Codex task as a managed companion window and keeps the task manager
and message queue alongside it. The fallback must not replace Codex with a
custom chat surface.

## Consequences

- Windows-native code may discover, attach, resize, detach, and restore the
  official Codex top-level window.
- Attachment must be explicitly reversible and must restore the original
  parent, styles, and placement on normal shutdown.
- The product must identify the official packaged application rather than
  trusting a window title alone.
- A Codex update, DPI mismatch, elevated-process mismatch, or Windows policy can
  disable embedded presentation without disabling the whole product.
- Task-to-thread mapping, deep-link navigation, and orchestration state remain
  independent from the attachment mechanism.
- Any future custom executor UI must be visibly branded as Agent Dock; it must
  never be presented as Codex.
