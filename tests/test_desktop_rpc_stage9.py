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
from agent_orchestrator.models import RunState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now


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
        invalid_type = self.request("task/list", {"limit": "20"})
        self.assertEqual(invalid_type["error"]["code"], "REQUEST_REJECTED")

    def test_task_detail_sections_are_durable_redacted_and_paginated(self) -> None:
        for index in range(3):
            self.store.append_audit(
                task_id=self.task.task_id,
                run_id=None,
                kind="DETAIL_TEST",
                payload={"index": index, "token": "sk-secret-value"},
            )
        first = self.request(
            "task/detail",
            {
                "taskId": self.task.task_id,
                "section": "activities",
                "limit": 2,
            },
        )["result"]
        self.assertEqual(len(first["items"]), 2)
        self.assertIsNotNone(first["nextCursor"])
        self.assertNotIn("sk-secret-value", json.dumps(first))
        second = self.request(
            "task/detail",
            {
                "taskId": self.task.task_id,
                "section": "activities",
                "limit": 2,
                "cursor": first["nextCursor"],
            },
        )["result"]
        self.assertNotEqual(
            {item["id"] for item in first["items"]},
            {item["id"] for item in second["items"]},
        )

        run = self.store.create_run(
            run_id="run-detail",
            task_id=self.task.task_id,
            engine="fake",
            lease_owner="desktop-test",
            lease_seconds=60,
        )
        self.store.bind_run(
            run_id=run.run_id,
            lease_owner="desktop-test",
            provider_run_id="provider-detail",
            thread_id="thread-detail",
            lease_seconds=60,
        )
        self.store.finish_run(
            run_id=run.run_id,
            lease_owner="desktop-test",
            state=RunState.COMPLETED,
            result_summary="safe result",
        )
        checkpoint = self.store.reserve_checkpoint(
            checkpoint_id="checkpoint-detail",
            task_id=self.task.task_id,
            run_id=run.run_id,
            schema_version=1,
            workspace_revision="abc123",
            payload_path="pending",
        )
        self.store.finalize_checkpoint(
            checkpoint_id=checkpoint.checkpoint_id,
            payload_path="checkpoint.json",
            payload_hash="deadbeef",
        )
        timestamp = utc_now()
        self.store.record_verification(
            verification_id="verification-detail",
            task_id=self.task.task_id,
            run_id=run.run_id,
            attempt=1,
            check_name="unit",
            required=True,
            status="PASSED",
            command=("python", "-m", "unittest"),
            exit_code=0,
            timed_out=False,
            output_truncated=False,
            duration_ms=12,
            summary="passed",
            log_path="verification.log",
            started_at=timestamp,
            ended_at=timestamp,
        )
        for section in ("runs", "checkpoints", "verifications"):
            result = self.request(
                "task/detail",
                {
                    "taskId": self.task.task_id,
                    "section": section,
                    "limit": 10,
                },
            )["result"]
            self.assertTrue(result["items"], section)
        report = self.request(
            "task/detail",
            {"taskId": self.task.task_id, "section": "report"},
        )["result"]
        self.assertEqual(report["attempts"][0]["passed"], 1)
        self.assertTrue(report["auditChainValid"])

    def test_task_detail_rejects_cross_section_cursor(self) -> None:
        response = self.request(
            "task/detail",
            {
                "taskId": self.task.task_id,
                "section": "runs",
                "cursor": "activities:10",
            },
        )
        self.assertEqual(response["error"]["code"], "REQUEST_REJECTED")

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
