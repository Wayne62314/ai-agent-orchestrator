from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from agent_orchestrator.adapters.app_server_limits import (
    AppServerRateLimitProvider,
)
from agent_orchestrator.adapters.codex_sdk import CodexSdkExecutionAdapter
from agent_orchestrator.adapters.fake import FakeExecutionAdapter
from agent_orchestrator.execution import ExecutionCoordinator
from agent_orchestrator.models import RunState, TaskState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore


class _FakeSdkStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeSdkTurn:
    def __init__(self, turn_id: str):
        self.id = turn_id
        self.interrupted = False

    def run(self):
        return SimpleNamespace(
            status=_FakeSdkStatus(
                "interrupted" if self.interrupted else "completed"
            ),
            error=None,
            usage=None,
            final_response=(
                None if self.interrupted else "adapter completed"
            ),
        )

    def interrupt(self):
        self.interrupted = True
        return {}


class _AlreadyCompletedSdkTurn(_FakeSdkTurn):
    def interrupt(self):
        raise RuntimeError("no active turn to interrupt")


class _FakeSdkThread:
    def __init__(self, thread_id: str, turn_factory=_FakeSdkTurn):
        self.id = thread_id
        self.turn_factory = turn_factory

    def turn(self, _prompt, **_kwargs):
        return self.turn_factory(f"turn-{self.id}")


class _FakeSdkClient:
    def __init__(self, turn_factory=_FakeSdkTurn):
        self.started = 0
        self.resumed: list[str] = []
        self.closed = False
        self.turn_factory = turn_factory

    def thread_start(self, **_kwargs):
        self.started += 1
        return _FakeSdkThread("thread-new", self.turn_factory)

    def thread_resume(self, thread_id, **_kwargs):
        self.resumed.append(thread_id)
        return _FakeSdkThread(thread_id, self.turn_factory)

    def close(self):
        self.closed = True


class _FakeRpcClient:
    def __init__(self):
        self.notifications = [
            {
                "method": "account/rateLimits/updated",
                "params": {
                    "rateLimits": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 52,
                            "resetsAt": 1_800_000_000,
                        },
                        "rateLimitReachedType": None,
                    }
                },
            }
        ]

    def call(self, method, params=None, timeout=None):
        self.last_method = method
        return {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 25,
                        "resetsAt": 1_800_000_000,
                    },
                    "rateLimitReachedType": None,
                }
            }
        }

    def next_notification(self, *, timeout):
        if not self.notifications:
            raise TimeoutError
        return self.notifications.pop(0)


class StageTwoAdapterTests(unittest.TestCase):
    def test_codex_adapter_start_collect_and_resume(self) -> None:
        client = _FakeSdkClient()
        adapter = CodexSdkExecutionAdapter(client=client)
        from agent_orchestrator.adapters.base import RunRequest, RunStatus

        first = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-1",
                workspace_path=".",
                prompt="test",
            )
        )
        result = adapter.collect(first)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.final_response, "adapter completed")

        resumed = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-2",
                workspace_path=".",
                prompt="resume",
                thread_id="thread-existing",
            )
        )
        self.assertEqual(resumed.thread_id, "thread-existing")
        self.assertEqual(client.resumed, ["thread-existing"])

    def test_codex_adapter_interrupt(self) -> None:
        client = _FakeSdkClient()
        adapter = CodexSdkExecutionAdapter(client=client)
        from agent_orchestrator.adapters.base import RunRequest, RunStatus

        handle = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-interrupt",
                workspace_path=".",
                prompt="test",
            )
        )
        result = adapter.interrupt(handle)
        self.assertEqual(result.status, RunStatus.INTERRUPTED)

    def test_interrupt_race_collects_already_completed_turn(self) -> None:
        client = _FakeSdkClient(turn_factory=_AlreadyCompletedSdkTurn)
        adapter = CodexSdkExecutionAdapter(client=client)
        from agent_orchestrator.adapters.base import RunRequest, RunStatus

        handle = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-race",
                workspace_path=".",
                prompt="test",
            )
        )
        result = adapter.interrupt(handle)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.final_response, "adapter completed")

    def test_rate_limit_read_and_update(self) -> None:
        provider = AppServerRateLimitProvider(client=_FakeRpcClient())
        initial = provider.read()
        self.assertEqual(initial[0].limit_id, "codex")
        self.assertEqual(initial[0].used_percent, 25.0)
        self.assertIsNotNone(initial[0].resets_at)
        updated = provider.wait_for_update(timeout=1)
        self.assertEqual(updated[0].used_percent, 52.0)

    def test_execution_coordinator_completed_run_enters_verifying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteStore(root / "state.db")
            service = OrchestratorService(store)
            service.initialize()
            task = service.create_task(
                title="Coordinator task",
                objective="Finish a fake run",
                workspace_path=root,
            )
            service.validate_task(task.task_id)
            adapter = FakeExecutionAdapter()
            coordinator = ExecutionCoordinator(
                store=store,
                service=service,
                adapter=adapter,
                owner="worker-1",
            )
            started = coordinator.start(
                task_id=task.task_id,
                prompt="test",
            )
            adapter.complete(started.record.run_id, final_response="done")
            finished = coordinator.collect(started)
            self.assertEqual(finished.record.state, RunState.COMPLETED)
            self.assertEqual(
                store.get_task(task.task_id).state,
                TaskState.VERIFYING,
            )

    def test_execution_coordinator_interrupt_enters_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteStore(root / "state.db")
            service = OrchestratorService(store)
            service.initialize()
            task = service.create_task(
                title="Interrupt task",
                objective="Interrupt a fake run",
                workspace_path=root,
            )
            service.validate_task(task.task_id)
            coordinator = ExecutionCoordinator(
                store=store,
                service=service,
                adapter=FakeExecutionAdapter(),
                owner="worker-1",
            )
            started = coordinator.start(task_id=task.task_id, prompt="test")
            finished = coordinator.interrupt(started)
            self.assertEqual(finished.record.state, RunState.INTERRUPTED)
            self.assertEqual(
                store.get_task(task.task_id).state,
                TaskState.WAITING_FOR_SIGNAL,
            )


if __name__ == "__main__":
    unittest.main()
