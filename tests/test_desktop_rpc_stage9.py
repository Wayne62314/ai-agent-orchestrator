from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.desktop_rpc import (
    MAX_MESSAGE_BYTES,
    PROTOCOL,
    DesktopQueryService,
    DesktopRpcApplication,
    DesktopRpcServer,
)
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore


class DesktopRpcStageNineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = SQLiteStore(root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.task = self.service.create_task(
            title="Desktop task",
            objective="Expose a stable, redacted desktop read model.",
            workspace_path=root,
            permissions_policy={"filesystem": {"write": "deny"}},
            acceptance_policy={
                "checks": [
                    {
                        "name": "tests",
                        "command": ["python", "-m", "unittest"],
                    }
                ]
            },
        )
        self.service.validate_task(self.task.task_id)
        self.application = DesktopRpcApplication(
            DesktopQueryService(self.store)
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def request(
        self,
        method: str,
        params: dict | None = None,
        *,
        protocol: str = PROTOCOL,
    ) -> dict:
        server = DesktopRpcServer(
            self.application,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        return server.handle_line(
            json.dumps(
                {
                    "protocol": protocol,
                    "id": "request-1",
                    "method": method,
                    "params": params or {},
                }
            )
        )

    def test_initialize_returns_bounded_desktop_snapshot(self) -> None:
        response = self.request("system/initialize")
        result = response["result"]
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["tasks"]["items"][0]["id"], self.task.task_id)
        self.assertEqual(result["tasks"]["nextCursor"], None)

    def test_task_read_rebuilds_state_from_durable_storage(self) -> None:
        reopened = SQLiteStore(self.store.database_path)
        reopened.initialize()
        application = DesktopRpcApplication(DesktopQueryService(reopened))
        server = DesktopRpcServer(
            application,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        response = server.handle_line(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "id": 8,
                    "method": "task/read",
                    "params": {"taskId": self.task.task_id},
                }
            )
        )
        self.assertEqual(response["result"]["state"], "READY")
        self.assertTrue(response["result"]["activities"])

    def test_unknown_and_malformed_requests_are_rejected(self) -> None:
        unknown = self.request("database/update-directly")
        self.assertEqual(unknown["error"]["code"], "METHOD_NOT_FOUND")
        mismatch = self.request("system/status", protocol="future.protocol")
        self.assertEqual(mismatch["error"]["code"], "PROTOCOL_MISMATCH")
        server = DesktopRpcServer(
            self.application,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        malformed = server.handle_line("{not-json}\n")
        self.assertEqual(malformed["error"]["code"], "INVALID_JSON")

    def test_message_size_and_page_size_are_bounded(self) -> None:
        server = DesktopRpcServer(
            self.application,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        oversized = server.handle_line("x" * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(oversized["error"]["code"], "MESSAGE_TOO_LARGE")
        invalid_page = self.request("task/list", {"limit": 101})
        self.assertEqual(invalid_page["error"]["code"], "REQUEST_REJECTED")

    def test_serve_preserves_ids_and_emits_one_response_per_line(self) -> None:
        requests = "".join(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "id": index,
                    "method": "system/status",
                    "params": {},
                }
            )
            + "\n"
            for index in (1, 2)
        )
        output = io.StringIO()
        server = DesktopRpcServer(
            self.application,
            input_stream=io.StringIO(requests),
            output_stream=output,
        )
        self.assertEqual(server.serve(), 0)
        responses = [
            json.loads(line) for line in output.getvalue().splitlines()
        ]
        self.assertEqual([item["id"] for item in responses], [1, 2])


if __name__ == "__main__":
    unittest.main()
