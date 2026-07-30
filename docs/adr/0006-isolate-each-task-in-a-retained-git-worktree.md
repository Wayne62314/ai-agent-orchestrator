---
status: accepted
---

# Isolate each Task in a retained Git worktree

Every Task will receive one application-managed Git worktree and a dedicated
`aiao/task-<short-id>` branch created from an explicit committed revision. The
agent never edits the user's original working directory. Worktrees are retained
after success, failure, or cancellation and are removed only by an explicit
user cleanup when the Task is terminal and the worktree is clean.

## Consequences

Uncommitted changes in the source working directory are never stashed, copied,
or silently included; the user must acknowledge that the Task starts from the
selected committed revision. Automatic commit requires a hash-bound Approval.
v1 does not automatically merge or push. A missing, moved, corrupt, dirty, or
otherwise ambiguous worktree enters Needs Attention and cannot be force-deleted
through the normal cleanup flow.
