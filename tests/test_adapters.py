from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.adapters.app_server_limits import (
    AppServerRateLimitProvider,
)
from agent_orchestrator.adapters.base import RunRequest, RunStatus
from agent_orchestrator.adapters.fake import FakeExecutionAdapter
from agent_orchestrator.errors import (
    AdapterUnavailableError,
    ValidationError,
)


class AdapterTests(unittest.TestCase):
    def test_fake_adapter_complete_and_collect(self) -> None:
        adapter = FakeExecutionAdapter()
        request = RunRequest(
            task_id="task_1",
            run_id="run_1",
            workspace_path=".",
            prompt="test",
        )
        handle = adapter.start(request)
        self.assertEqual(handle.status, RunStatus.RUNNING)

        completed = adapter.complete(
            "run_1",
            final_response="done",
            usage={"input_tokens": 10},
        )
        collected = adapter.collect(adapter.inspect(handle))

        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertEqual(collected.final_response, "done")
        self.assertEqual(collected.usage["input_tokens"], 10)

    def test_fake_adapter_interrupt(self) -> None:
        adapter = FakeExecutionAdapter()
        handle = adapter.start(
            RunRequest(
                task_id="task_1",
                run_id="run_interrupt",
                workspace_path=".",
                prompt="test",
            )
        )
        result = adapter.interrupt(handle)
        self.assertEqual(result.status, RunStatus.INTERRUPTED)
        self.assertEqual(
            adapter.inspect(handle).status,
            RunStatus.INTERRUPTED,
        )

    def test_fake_adapter_rejects_collect_while_running(self) -> None:
        adapter = FakeExecutionAdapter()
        handle = adapter.start(
            RunRequest(
                task_id="task_1",
                run_id="run_active",
                workspace_path=".",
                prompt="test",
            )
        )
        with self.assertRaises(ValidationError):
            adapter.collect(handle)

    def test_rate_limit_provider_validates_binary_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-codex.exe"
            provider = AppServerRateLimitProvider(missing)
            with self.assertRaises(AdapterUnavailableError):
                provider.validate_environment()


if __name__ == "__main__":
    unittest.main()

