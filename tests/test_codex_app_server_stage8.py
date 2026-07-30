from __future__ import annotations

import queue
import unittest

from agent_orchestrator.adapters.base import RunRequest, RunStatus
from agent_orchestrator.adapters.codex_app_server import (
    CodexAppServerExecutionAdapter,
)
from agent_orchestrator.codex_session import CodexSessionService


class _FakeAppServerClient:
    def __init__(self, *, auto_complete: bool = True):
        self.auto_complete = auto_complete
        self.notifications: queue.Queue[dict] = queue.Queue()
        self.server_requests: queue.Queue[dict] = queue.Queue()
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[tuple[int, dict | None, dict | None]] = []
        self.response_events: queue.Queue[
            tuple[int, dict | None, dict | None]
        ] = queue.Queue()
        self.started = False
        self.closed = False
        self.account: dict | None = None

    def start(self) -> None:
        self.started = True

    def subscribe_notifications(self):
        return self.notifications

    def unsubscribe_notifications(self, _subscriber) -> None:
        return None

    def call(self, method, params=None, *, timeout=None):
        del timeout
        params = params or {}
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-new"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            turn_id = "turn-1"
            if self.auto_complete:
                self.notifications.put(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": params["threadId"],
                            "turnId": turn_id,
                            "delta": "done",
                        },
                    }
                )
                self.notifications.put(
                    {
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "id": turn_id,
                                "status": "completed",
                                "error": None,
                            }
                        },
                    }
                )
            return {
                "turn": {
                    "id": turn_id,
                    "status": "inProgress",
                    "items": [],
                }
            }
        if method == "turn/interrupt":
            self.notifications.put(
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": params["turnId"],
                            "status": "interrupted",
                            "error": None,
                        }
                    },
                }
            )
            return {}
        if method == "account/read":
            return {
                "account": self.account,
                "requiresOpenaiAuth": True,
            }
        if method == "account/login/start":
            if params["type"] == "apiKey":
                self.account = {"type": "apiKey"}
                return {"type": "apiKey"}
            if params["type"] == "chatgptDeviceCode":
                return {
                    "type": "chatgptDeviceCode",
                    "loginId": "login-device",
                    "verificationUrl": "https://auth.example/device",
                    "userCode": "ABCD-1234",
                }
            return {
                "type": "chatgpt",
                "loginId": "login-browser",
                "authUrl": "https://auth.example/browser",
            }
        if method == "account/logout":
            self.account = None
            return {}
        if method == "account/login/cancel":
            return {}
        raise AssertionError(f"Unexpected App Server method: {method}")

    def next_server_request(self, *, timeout):
        try:
            return self.server_requests.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def respond(self, request_id, *, result=None, error=None):
        response = (request_id, result, error)
        self.responses.append(response)
        self.response_events.put(response)

    def responses_queue_get(self):
        return self.response_events.get(timeout=2)

    def close(self):
        self.closed = True


class CodexAppServerStageEightTests(unittest.TestCase):
    def test_start_collect_uses_restricted_workspace_policy(self) -> None:
        client = _FakeAppServerClient()
        adapter = CodexAppServerExecutionAdapter(client=client)
        handle = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-1",
                workspace_path=".",
                prompt="finish the task",
                sandbox="workspace-write",
            )
        )
        result = adapter.collect(handle)
        adapter.close()
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.final_response, "done")
        turn_params = next(
            params
            for method, params in client.calls
            if method == "turn/start"
        )
        self.assertEqual(turn_params["approvalPolicy"], "never")
        self.assertEqual(
            turn_params["sandboxPolicy"]["type"],
            "workspaceWrite",
        )
        self.assertFalse(turn_params["sandboxPolicy"]["networkAccess"])
        thread_params = next(
            params
            for method, params in client.calls
            if method == "thread/start"
        )
        self.assertEqual(thread_params["sandbox"], "workspace-write")

    def test_resume_and_interrupt_existing_thread(self) -> None:
        client = _FakeAppServerClient(auto_complete=False)
        adapter = CodexAppServerExecutionAdapter(client=client)
        handle = adapter.start(
            RunRequest(
                task_id="task-1",
                run_id="run-2",
                workspace_path=".",
                prompt="resume",
                thread_id="thread-existing",
            )
        )
        result = adapter.interrupt(handle)
        adapter.close()
        self.assertEqual(handle.thread_id, "thread-existing")
        self.assertEqual(result.status, RunStatus.INTERRUPTED)
        self.assertTrue(
            any(method == "thread/resume" for method, _params in client.calls)
        )

    def test_unexpected_codex_approval_is_declined(self) -> None:
        client = _FakeAppServerClient(auto_complete=False)
        adapter = CodexAppServerExecutionAdapter(client=client)
        adapter._ensure_started()
        client.server_requests.put(
            {
                "id": 17,
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "unsafe"},
            }
        )
        request_id, result, error = client.responses_queue_get()
        adapter.close()
        self.assertEqual(request_id, 17)
        self.assertEqual(result, {"decision": "decline"})
        self.assertIsNone(error)

    def test_account_service_delegates_login_without_persisting_key(self) -> None:
        client = _FakeAppServerClient()
        sessions = CodexSessionService(client)
        browser = sessions.start_chatgpt_login()
        device = sessions.start_chatgpt_login(device_code=True)
        account = sessions.login_with_api_key("unit-test-placeholder")
        logged_out = sessions.logout()
        sessions.close()
        self.assertEqual(browser.login_id, "login-browser")
        self.assertEqual(device.user_code, "ABCD-1234")
        self.assertEqual(account.account_type, "apiKey")
        self.assertFalse(logged_out.signed_in)


if __name__ == "__main__":
    unittest.main()
