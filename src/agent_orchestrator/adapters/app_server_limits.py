"""Codex App Server stdio client for ChatGPT rate-limit observation."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..errors import AdapterUnavailableError
from ..store import utc_now
from .base import RateLimitProvider, RateLimitSnapshot


class JsonRpcClient(Protocol):
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...

    def next_notification(self, *, timeout: float) -> dict[str, Any]: ...


class StdioJsonRpcClient:
    def __init__(
        self,
        codex_binary: str | Path,
        *,
        timeout: float = 15.0,
    ):
        self.codex_binary = Path(codex_binary).expanduser().resolve()
        self.timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=50)
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._process is not None:
                return
            if not self.codex_binary.is_file():
                raise AdapterUnavailableError(
                    f"Codex runtime was not found at {self.codex_binary}."
                )
            creation_flags = (
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            self._process = subprocess.Popen(
                [
                    str(self.codex_binary),
                    "app-server",
                    "--listen",
                    "stdio://",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._read_stderr, daemon=True).start()
        self.call(
            "initialize",
            {
                "clientInfo": {
                    "name": "ai_agent_orchestrator",
                    "title": "AI Agent Orchestrator",
                    "version": "0.3.0",
                }
            },
        )
        self.notify("initialized", {})

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._process is None:
            self.start()
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._responses[request_id] = response_queue
            self._write(
                {
                    "method": method,
                    "id": request_id,
                    "params": params or {},
                }
            )
        try:
            response = response_queue.get(timeout=timeout or self.timeout)
        except queue.Empty as exc:
            raise AdapterUnavailableError(
                f"Timed out waiting for App Server method {method!r}."
            ) from exc
        finally:
            with self._lock:
                self._responses.pop(request_id, None)
        if "error" in response:
            error = response["error"]
            raise AdapterUnavailableError(
                f"App Server {method!r} failed: {error.get('message', error)}"
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if self._process is None:
            raise AdapterUnavailableError("App Server is not running.")
        with self._lock:
            self._write({"method": method, "params": params})

    def next_notification(self, *, timeout: float) -> dict[str, Any]:
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("No App Server notification arrived.") from exc

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def __enter__(self) -> "StdioJsonRpcClient":
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            detail = self._stderr[-1] if self._stderr else "process is not running"
            raise AdapterUnavailableError(f"App Server unavailable: {detail}")
        process.stdin.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if isinstance(request_id, int):
                with self._lock:
                    target = self._responses.get(request_id)
                if target is not None:
                    target.put(message)
            elif "method" in message:
                self._notifications.put(message)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())


class AppServerRateLimitProvider(RateLimitProvider):
    def __init__(
        self,
        codex_binary: str | Path | None = None,
        *,
        client: JsonRpcClient | None = None,
    ):
        if client is None and codex_binary is None:
            raise ValueError("codex_binary or client is required.")
        self.client = client or StdioJsonRpcClient(codex_binary)  # type: ignore[arg-type]
        self._owns_client = client is None

    def validate_environment(self) -> None:
        binary = getattr(self.client, "codex_binary", None)
        if binary is not None and not Path(binary).is_file():
            raise AdapterUnavailableError(
                f"Codex runtime was not found at {Path(binary)}."
            )

    def read(self) -> tuple[RateLimitSnapshot, ...]:
        self.validate_environment()
        result = self.client.call("account/rateLimits/read", {})
        return self._parse_result(result, reference="account/rateLimits/read")

    def wait_for_update(self, *, timeout: float) -> tuple[RateLimitSnapshot, ...]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("No rate-limit update arrived.")
            notification = self.client.next_notification(timeout=remaining)
            if notification.get("method") != "account/rateLimits/updated":
                continue
            params = notification.get("params")
            if not isinstance(params, dict):
                continue
            return self._parse_result(
                params,
                reference="account/rateLimits/updated",
            )

    def close(self) -> None:
        if self._owns_client and hasattr(self.client, "close"):
            self.client.close()  # type: ignore[union-attr]

    def __enter__(self) -> "AppServerRateLimitProvider":
        if hasattr(self.client, "start"):
            self.client.start()  # type: ignore[union-attr]
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    @staticmethod
    def _parse_result(
        result: dict[str, Any],
        *,
        reference: str,
    ) -> tuple[RateLimitSnapshot, ...]:
        buckets = result.get("rateLimitsByLimitId")
        if not isinstance(buckets, dict) or not buckets:
            single = result.get("rateLimits")
            buckets = (
                {str(single.get("limitId") or "codex"): single}
                if isinstance(single, dict)
                else {}
            )
        observed_at = utc_now()
        snapshots: list[RateLimitSnapshot] = []
        for key, bucket in sorted(buckets.items()):
            if not isinstance(bucket, dict):
                continue
            primary = bucket.get("primary")
            primary = primary if isinstance(primary, dict) else {}
            snapshots.append(
                RateLimitSnapshot(
                    provider="codex-chatgpt",
                    limit_id=str(bucket.get("limitId") or key),
                    used_percent=AppServerRateLimitProvider._float_or_none(
                        primary.get("usedPercent")
                    ),
                    resets_at=AppServerRateLimitProvider._epoch_to_iso(
                        primary.get("resetsAt")
                    ),
                    reached_type=(
                        str(bucket["rateLimitReachedType"])
                        if bucket.get("rateLimitReachedType") is not None
                        else None
                    ),
                    observed_at=observed_at,
                    raw_reference=reference,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _epoch_to_iso(value: Any) -> str | None:
        if not isinstance(value, (int, float)):
            return None
        return datetime.fromtimestamp(value, tz=UTC).isoformat()

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None
