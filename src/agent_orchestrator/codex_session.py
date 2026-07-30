"""Product-facing Codex App Server account and login service."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Any

from .errors import AdapterUnavailableError, ValidationError


@dataclass(frozen=True, slots=True)
class AccountSummary:
    signed_in: bool
    account_type: str | None
    email: str | None
    plan_type: str | None
    requires_openai_auth: bool


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    login_type: str
    login_id: str | None
    authorization_url: str | None = None
    verification_url: str | None = None
    user_code: str | None = None


class CodexSessionService:
    """Delegates authentication lifecycle to Codex without persisting secrets."""

    def __init__(self, client: Any):
        self.client = client
        self._notifications = client.subscribe_notifications()

    def read_account(self, *, refresh_token: bool = False) -> AccountSummary:
        result = self.client.call(
            "account/read",
            {"refreshToken": refresh_token},
        )
        account = result.get("account")
        account = account if isinstance(account, dict) else None
        return AccountSummary(
            signed_in=account is not None,
            account_type=(
                str(account.get("type"))
                if account is not None and account.get("type") is not None
                else None
            ),
            email=(
                str(account.get("email"))
                if account is not None and account.get("email") is not None
                else None
            ),
            plan_type=(
                str(account.get("planType"))
                if account is not None and account.get("planType") is not None
                else None
            ),
            requires_openai_auth=bool(result.get("requiresOpenaiAuth", True)),
        )

    def start_chatgpt_login(
        self,
        *,
        device_code: bool = False,
    ) -> LoginAttempt:
        login_type = "chatgptDeviceCode" if device_code else "chatgpt"
        params: dict[str, Any] = {"type": login_type}
        if not device_code:
            params.update(
                {
                    "useHostedLoginSuccessPage": True,
                    "appBrand": "codex",
                }
            )
        result = self.client.call("account/login/start", params)
        return self._login_attempt(result, login_type=login_type)

    def login_with_api_key(self, api_key: str) -> AccountSummary:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValidationError("API key cannot be empty.")
        self.client.call(
            "account/login/start",
            {"type": "apiKey", "apiKey": api_key},
        )
        return self.read_account(refresh_token=False)

    def wait_for_login(
        self,
        login_id: str,
        *,
        timeout: float = 300,
    ) -> AccountSummary:
        if not login_id:
            raise ValidationError("Login id cannot be empty.")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdapterUnavailableError("Timed out waiting for Codex login.")
            try:
                notification = self._notifications.get(timeout=remaining)
            except queue.Empty as exc:
                raise AdapterUnavailableError(
                    "Timed out waiting for Codex login."
                ) from exc
            if notification.get("method") != "account/login/completed":
                continue
            params = notification.get("params")
            if not isinstance(params, dict):
                continue
            if str(params.get("loginId") or "") != login_id:
                continue
            if not params.get("success"):
                raise AdapterUnavailableError(
                    str(params.get("error") or "Codex login failed.")
                )
            return self.read_account(refresh_token=False)

    def cancel_login(self, login_id: str) -> None:
        if not login_id:
            raise ValidationError("Login id cannot be empty.")
        self.client.call(
            "account/login/cancel",
            {"loginId": login_id},
        )

    def logout(self) -> AccountSummary:
        self.client.call("account/logout", {})
        return self.read_account(refresh_token=False)

    def close(self) -> None:
        if hasattr(self.client, "unsubscribe_notifications"):
            self.client.unsubscribe_notifications(self._notifications)

    @staticmethod
    def _login_attempt(
        result: dict[str, Any],
        *,
        login_type: str,
    ) -> LoginAttempt:
        login_id = result.get("loginId")
        return LoginAttempt(
            login_type=login_type,
            login_id=str(login_id) if login_id is not None else None,
            authorization_url=(
                str(result.get("authUrl"))
                if result.get("authUrl") is not None
                else None
            ),
            verification_url=(
                str(result.get("verificationUrl"))
                if result.get("verificationUrl") is not None
                else None
            ),
            user_code=(
                str(result.get("userCode"))
                if result.get("userCode") is not None
                else None
            ),
        )
