from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from agent_orchestrator.external_events import (
    HmacSha256Authenticator,
    TrustedEventService,
)
from agent_orchestrator.models import (
    Event,
    EventType,
    ExternalEventKind,
    TaskState,
)
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now
from agent_orchestrator.webhook_server import build_webhook_server
from agent_orchestrator.worker import RecoveryWorker


class WebhookServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = root / "state.db"
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.secret = b"test-webhook-secret"
        self.store = SQLiteStore(self.database)
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.events = TrustedEventService(store=self.store, service=self.service)
        self.server = build_webhook_server(
            store=self.store,
            service=self.service,
            host="127.0.0.1",
            port=0,
            secret=self.secret,
        )
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.temporary_directory.cleanup()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def running_task(self, suffix: str):
        task = self.service.create_task(
            title=f"Webhook task {suffix}",
            objective="Resume from a real HTTP delivery",
            workspace_path=self.workspace,
            task_id=f"task_webhook_{suffix}",
        )
        self.service.validate_task(task.task_id)
        self.service.process_event(
            Event(
                event_id=f"evt_run_{suffix}",
                task_id=task.task_id,
                event_type=EventType.RUN_REQUESTED,
                source="test",
                dedupe_key=f"run:{suffix}",
                occurred_at=utc_now(),
            )
        )
        return self.store.get_task(task.task_id)

    @staticmethod
    def workflow_body(*, conclusion: str = "success", run_id: int = 42) -> bytes:
        return json.dumps(
            {
                "action": "completed",
                "repository": {"full_name": "octo/example"},
                "sender": {"login": "github-actions"},
                "workflow_run": {
                    "id": run_id,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": conclusion,
                    "head_branch": "main",
                },
            }
        ).encode("utf-8")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        event_name: str = "workflow_run",
        delivery_id: str | None = None,
        secret: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers: dict[str, str] = {}
        if method == "POST":
            signing_secret = self.secret if secret is None else secret
            headers = {
                "Content-Type": content_type,
                "X-GitHub-Event": event_name,
                "X-GitHub-Delivery": delivery_id or f"delivery-{uuid.uuid4().hex}",
                "X-Hub-Signature-256": HmacSha256Authenticator.sign(
                    body,
                    signing_secret,
                ),
            }
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
        finally:
            connection.close()

    def test_health_and_readiness_endpoints(self) -> None:
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertEqual(self.request("GET", "/readyz")[0], 200)
        self.assertEqual(self.request("GET", "/missing")[0], 404)

    def test_real_http_ci_delivery_resumes_the_only_matching_task(self) -> None:
        task = self.running_task("ci")
        self.events.register_wait(
            task.task_id,
            provider="github",
            kind=ExternalEventKind.CI_COMPLETED,
            subject="octo/example#workflow:CI#branch:main",
            condition={"status": "completed", "conclusion": "success"},
            timeout_seconds=300,
        )
        body = self.workflow_body()

        status, payload = self.request(
            "POST",
            "/webhooks/github",
            body=body,
            delivery_id="http-ci-success",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "consumed")
        self.assertEqual(payload["task_id"], task.task_id)
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.READY)

    def test_duplicate_http_delivery_returns_the_same_receipt(self) -> None:
        task = self.running_task("duplicate")
        self.events.register_wait(
            task.task_id,
            provider="github",
            kind=ExternalEventKind.CI_COMPLETED,
            subject="octo/example#branch:main",
            condition={"conclusion": "success"},
            timeout_seconds=300,
        )
        body = self.workflow_body()
        first = self.request(
            "POST",
            "/webhooks/github",
            body=body,
            delivery_id="http-duplicate",
        )
        second = self.request(
            "POST",
            "/webhooks/github",
            body=body,
            delivery_id="http-duplicate",
        )
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertEqual(
            first[1]["external_event_id"],
            second[1]["external_event_id"],
        )

    def test_wrong_signature_is_unauthorized_and_does_not_reserve_delivery(self) -> None:
        task = self.running_task("auth")
        self.events.register_wait(
            task.task_id,
            provider="github",
            kind=ExternalEventKind.CI_COMPLETED,
            subject="octo/example#workflow:42",
            condition={"conclusion": "success"},
            timeout_seconds=300,
        )
        body = self.workflow_body()
        rejected = self.request(
            "POST",
            "/webhooks/github",
            body=body,
            delivery_id="http-auth",
            secret=b"wrong-secret",
        )
        accepted = self.request(
            "POST",
            "/webhooks/github",
            body=body,
            delivery_id="http-auth",
        )
        self.assertEqual(rejected[0], 401)
        self.assertEqual(accepted[0], 200)

    def test_unmatched_and_ambiguous_deliveries_do_not_resume(self) -> None:
        unmatched = self.request(
            "POST",
            "/webhooks/github",
            body=self.workflow_body(run_id=99),
            delivery_id="http-unmatched",
        )
        self.assertEqual(unmatched[0], 202)

        first = self.running_task("ambiguous_one")
        second = self.running_task("ambiguous_two")
        for task in (first, second):
            self.events.register_wait(
                task.task_id,
                provider="github",
                kind=ExternalEventKind.CI_COMPLETED,
                subject="octo/example#workflow:42",
                condition={"conclusion": "success"},
                timeout_seconds=300,
            )
        ambiguous = self.request(
            "POST",
            "/webhooks/github",
            body=self.workflow_body(),
            delivery_id="http-ambiguous",
        )
        self.assertEqual(ambiguous[0], 409)
        self.assertEqual(
            self.store.get_task(first.task_id).state,
            TaskState.WAITING_FOR_SIGNAL,
        )
        self.assertEqual(
            self.store.get_task(second.task_id).state,
            TaskState.WAITING_FOR_SIGNAL,
        )

    def test_ping_and_request_validation(self) -> None:
        ping_body = b'{"zen":"Keep it logically awesome."}'
        ping = self.request(
            "POST",
            "/webhooks/github",
            body=ping_body,
            event_name="ping",
            delivery_id="http-ping",
        )
        unsupported = self.request(
            "POST",
            "/webhooks/github",
            body=b"{}",
            event_name="push",
            delivery_id="http-push",
        )
        wrong_type = self.request(
            "POST",
            "/webhooks/github",
            body=b"{}",
            content_type="text/plain",
        )
        self.assertEqual(ping[0], 200)
        self.assertEqual(unsupported[0], 400)
        self.assertEqual(wrong_type[0], 415)

    def test_worker_tick_expires_wait_after_restart(self) -> None:
        task = self.running_task("worker")
        self.events.register_wait(
            task.task_id,
            provider="github",
            kind=ExternalEventKind.CI_COMPLETED,
            subject="octo/example#workflow:42",
            condition={"conclusion": "success"},
            timeout_seconds=300,
        )
        reopened_store = SQLiteStore(self.database)
        reopened_service = OrchestratorService(reopened_store)
        reopened_service.initialize()
        result = RecoveryWorker(
            store=reopened_store,
            service=reopened_service,
        ).tick(observed_at="2999-01-01T00:00:00+00:00")
        self.assertEqual(result.expired_waits, 1)
        self.assertEqual(
            reopened_store.get_task(task.task_id).state,
            TaskState.NEEDS_ATTENTION,
        )


if __name__ == "__main__":
    unittest.main()
