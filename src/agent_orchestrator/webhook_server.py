"""Minimal authenticated GitHub webhook HTTP service."""

from __future__ import annotations

import json
import logging
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .adapters.trusted_events import GitHubEventAdapter
from .errors import (
    AmbiguousSignalError,
    NoMatchingSignalError,
    SourceAuthenticationError,
    ValidationError,
)
from .external_events import (
    MAX_WEBHOOK_BYTES,
    HmacSha256Authenticator,
    TrustedEventService,
)
from .service import OrchestratorService
from .store import SQLiteStore
from .worker import RecoveryWorker

LOGGER = logging.getLogger(__name__)
SUPPORTED_GITHUB_EVENTS = frozenset(
    {
        "issues",
        "pull_request_review",
        "pull_request_review_comment",
        "workflow_run",
    }
)


class GitHubWebhookApplication:
    def __init__(self, *, events: TrustedEventService, secret: bytes):
        if not secret:
            raise ValidationError("GitHub webhook secret is not configured.")
        self.events = events
        self.secret = secret

    def handle(
        self,
        *,
        event_name: str,
        delivery_id: str,
        signature: str,
        body: bytes,
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        if event_name == "ping":
            HmacSha256Authenticator.verify(body, signature, self.secret)
            return HTTPStatus.OK, {"status": "ok", "event": "ping"}
        if event_name not in SUPPORTED_GITHUB_EVENTS:
            raise ValidationError(f"Unsupported GitHub event {event_name!r}.")
        record = self.events.route_webhook(
            adapter=GitHubEventAdapter(event_name),
            body=body,
            delivery_id=delivery_id,
            signature=signature,
            secret=self.secret,
        )
        return HTTPStatus.OK, {
            "status": record.status.value.lower(),
            "external_event_id": record.external_event_id,
            "task_id": record.task_id,
            "event_kind": record.kind,
        }


class WebhookHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: GitHubWebhookApplication,
    ):
        self.application = application
        super().__init__(server_address, WebhookRequestHandler)


class WebhookRequestHandler(BaseHTTPRequestHandler):
    server: WebhookHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/readyz":
            try:
                with self.server.application.events.store.transaction() as connection:
                    connection.execute("SELECT 1").fetchone()
            except Exception:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "unavailable"},
                )
                return
            self._send_json(HTTPStatus.OK, {"status": "ready"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhooks/github":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "content_type_must_be_application_json"},
            )
            return
        length_value = self.headers.get("Content-Length")
        if length_value is None:
            self._send_json(
                HTTPStatus.LENGTH_REQUIRED,
                {"error": "content_length_required"},
            )
            return
        try:
            content_length = int(length_value)
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_content_length"},
            )
            return
        if content_length < 0 or content_length > MAX_WEBHOOK_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request_body_too_large"},
            )
            return

        event_name = self.headers.get("X-GitHub-Event", "").strip()
        delivery_id = self.headers.get("X-GitHub-Delivery", "").strip()
        signature = self.headers.get("X-Hub-Signature-256", "").strip()
        if not event_name or not delivery_id or not signature:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "required_github_headers_missing"},
            )
            return
        body = self.rfile.read(content_length)
        try:
            status, response = self.server.application.handle(
                event_name=event_name,
                delivery_id=delivery_id,
                signature=signature,
                body=body,
            )
        except SourceAuthenticationError:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "source_authentication_failed"},
            )
            return
        except NoMatchingSignalError:
            self._send_json(
                HTTPStatus.ACCEPTED,
                {"status": "ignored", "reason": "no_active_wait_satisfied"},
            )
            return
        except AmbiguousSignalError:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "ambiguous_active_wait"},
            )
            return
        except ValidationError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_webhook", "message": str(exc)},
            )
            return
        self._send_json(status, response)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("webhook_http %s", format % args)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def build_webhook_server(
    *,
    store: SQLiteStore,
    service: OrchestratorService,
    host: str,
    port: int,
    secret: bytes,
) -> WebhookHTTPServer:
    application = GitHubWebhookApplication(
        events=TrustedEventService(store=store, service=service),
        secret=secret,
    )
    return WebhookHTTPServer((host, port), application)


def run_webhook_server(
    *,
    database_path: str | Path,
    host: str,
    port: int,
    secret: bytes,
    worker_interval_seconds: float = 5.0,
) -> None:
    store = SQLiteStore(database_path)
    service = OrchestratorService(store)
    service.initialize()
    server = build_webhook_server(
        store=store,
        service=service,
        host=host,
        port=port,
        secret=secret,
    )
    worker = RecoveryWorker(
        store=store,
        service=service,
        interval_seconds=worker_interval_seconds,
    )
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=worker.run,
        args=(stop_event,),
        name="orchestrator-recovery-worker",
        daemon=True,
    )
    worker_thread.start()
    LOGGER.info("Webhook server listening on %s:%s", host, port)
    previous_handlers: dict[signal.Signals, Any] = {}

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_name] = signal.signal(
                signal_name,
                request_shutdown,
            )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop_event.set()
        server.server_close()
        worker_thread.join(timeout=max(1.0, worker_interval_seconds * 2))
        for signal_name, handler in previous_handlers.items():
            signal.signal(signal_name, handler)
