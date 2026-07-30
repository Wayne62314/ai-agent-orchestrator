---
status: accepted
---

# Use the tray application as the background process owner

Closing the main window will hide the application to the Windows notification
area while the Task and Python sidecar continue running. The user must choose
an explicit Quit command to stop the application; if a Task is active, Quit
first pauses it and writes a valid Checkpoint. v1 will not install a Windows
service because a tray-owned sidecar satisfies background execution while
keeping startup, visibility, recovery, and uninstall behavior understandable.

## Consequences

The tray menu must expose Open, Task status, Pause or Resume, and Quit. A
first-use explanation must make the close behavior visible. After a computer
restart, the Task remains recoverable, but automatic relaunch happens only when
the user enabled the default-off sign-in startup option.
