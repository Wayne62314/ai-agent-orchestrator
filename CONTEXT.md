# AI Agent Orchestrator Context

This context defines the product language for a local desktop application that
runs, pauses, resumes, verifies, and audits long-running AI development tasks.

## Language

**Task**:
A user's development objective bound to one repository and one isolated worktree.
_Avoid_: Job, workflow

**Run**:
One execution attempt made by an agent for a Task.
_Avoid_: Task, session

**Checkpoint**:
A verified, durable snapshot containing the facts required to resume a Task.
_Avoid_: Save, autosave

**Resume Package**:
The verified context assembled from a Checkpoint and current repository facts for a new Run.
_Avoid_: Continue prompt, memory

**Worktree**:
The isolated Git working directory created for exactly one Task.
_Avoid_: Workspace, project folder

**Approval**:
A time-limited user decision bound to the exact hash of one proposed high-risk action.
_Avoid_: Confirmation, permission

**Side Effect**:
An externally observable action tracked in the durable idempotency ledger.
_Avoid_: Command, operation

**Verification Policy**:
The required and optional checks that determine whether a Task may succeed.
_Avoid_: Test list, acceptance prompt

**Delivery Report**:
The final evidence summary for a completed or attention-requiring Task.
_Avoid_: Log, result

## Relationships

- A **Task** has one **Worktree** and one or more **Runs**
- A **Run** may produce one or more **Checkpoints**
- A **Resume Package** is built from the latest valid **Checkpoint**
- A **Task** has exactly one **Verification Policy**
- A high-risk **Side Effect** requires one matching, unexpired **Approval**
- A **Delivery Report** summarizes the **Runs**, verification evidence, and final Task state

## Example dialogue

> **Developer:** "The window closed during a Run. Should we create a new Task?"
> **Domain expert:** "No. Keep the same Task and Worktree, verify its latest Checkpoint,
> build a Resume Package, and start the next Run."

## Flagged ambiguities

- "workspace" previously meant both the user's repository and the agent's isolated
  working directory. Use **repository** for the selected project and **Worktree**
  for the Task's isolated working directory.
- "approval" and "confirmation" were used interchangeably. **Approval** is the
  durable, hash-bound decision; an ordinary UI confirmation is not an Approval.
