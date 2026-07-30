"""Normalization adapters for authenticated external wake-up signals."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..errors import ValidationError
from ..models import ExternalEventKind, NormalizedExternalEvent
from ..store import utc_now


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{name} must be an object.")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _required_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer.")
    return value


class GitHubEventAdapter:
    """Extract only trusted GitHub metadata; message bodies become hashes."""

    provider = "github"

    def __init__(self, event_name: str):
        self.event_name = event_name

    def normalize(
        self,
        payload: Mapping[str, Any],
        *,
        delivery_id: str,
        occurred_at: str = "",
    ) -> NormalizedExternalEvent:
        repository = _mapping(payload.get("repository"), "repository")
        repository_name = _required_text(repository.get("full_name"), "repository.full_name")
        sender = _mapping(payload.get("sender") or {}, "sender")
        actor = str(sender.get("login") or "")
        action = str(payload.get("action") or "")

        if self.event_name == "workflow_run":
            run = _mapping(payload.get("workflow_run"), "workflow_run")
            run_id = _required_int(run.get("id"), "workflow_run.id")
            facts = {
                "action": action,
                "status": str(run.get("status") or ""),
                "conclusion": str(run.get("conclusion") or ""),
                "workflow": str(run.get("name") or ""),
                "branch": str(run.get("head_branch") or ""),
                "run_id": run_id,
            }
            kind = ExternalEventKind.CI_COMPLETED
            subject = f"{repository_name}#workflow:{run_id}"
            trust = "TRUSTED_METADATA"
        elif self.event_name in {
            "pull_request_review",
            "pull_request_review_comment",
        }:
            pull_request = _mapping(payload.get("pull_request"), "pull_request")
            number = _required_int(pull_request.get("number"), "pull_request.number")
            review = _mapping(payload.get("review") or {}, "review")
            comment = _mapping(payload.get("comment") or {}, "comment")
            body = str(review.get("body") or comment.get("body") or "")
            facts = {
                "action": action,
                "state": str(review.get("state") or "commented").lower(),
                "actor": actor,
                "pr_number": number,
                "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()
                if body
                else "",
            }
            kind = ExternalEventKind.PR_REVIEW
            subject = f"{repository_name}#pr:{number}"
            trust = "UNTRUSTED_CONTENT_OMITTED"
        elif self.event_name == "issues":
            issue = _mapping(payload.get("issue"), "issue")
            number = _required_int(issue.get("number"), "issue.number")
            labels = issue.get("labels") or []
            if not isinstance(labels, list):
                raise ValidationError("issue.labels must be an array.")
            facts = {
                "action": action,
                "state": str(issue.get("state") or ""),
                "actor": actor,
                "issue_number": number,
                "labels": sorted(
                    str(item.get("name"))
                    for item in labels
                    if isinstance(item, Mapping) and item.get("name")
                ),
            }
            kind = ExternalEventKind.ISSUE_CHANGED
            subject = f"{repository_name}#issue:{number}"
            trust = "TRUSTED_METADATA"
        else:
            raise ValidationError(f"Unsupported GitHub event {self.event_name!r}.")

        return NormalizedExternalEvent(
            provider=self.provider,
            kind=kind,
            delivery_id=_required_text(delivery_id, "delivery_id"),
            subject=subject,
            facts=facts,
            occurred_at=occurred_at or utc_now(),
            content_trust=trust,
        )


class ServiceHealthEventAdapter:
    provider = "health-probe"

    def normalize(
        self,
        payload: Mapping[str, Any],
        *,
        delivery_id: str,
        occurred_at: str = "",
    ) -> NormalizedExternalEvent:
        service = _required_text(payload.get("service"), "service")
        status = _required_text(payload.get("status"), "status").lower()
        if status not in {"healthy", "degraded", "unhealthy"}:
            raise ValidationError("Service status is not recognized.")
        return NormalizedExternalEvent(
            provider=self.provider,
            kind=ExternalEventKind.SERVICE_HEALTH,
            delivery_id=_required_text(delivery_id, "delivery_id"),
            subject=service,
            facts={
                "service": service,
                "status": status,
                "check_id": str(payload.get("check_id") or ""),
            },
            occurred_at=occurred_at or utc_now(),
        )


class RateLimitEventAdapter:
    provider = "app-server"

    def normalize(
        self,
        payload: Mapping[str, Any],
        *,
        delivery_id: str,
        occurred_at: str = "",
    ) -> NormalizedExternalEvent:
        provider_name = _required_text(payload.get("provider"), "provider")
        bucket = _required_text(payload.get("bucket"), "bucket")
        available = payload.get("available")
        if not isinstance(available, bool):
            raise ValidationError("available must be a boolean.")
        remaining = payload.get("remaining")
        if remaining is not None and (
            isinstance(remaining, bool) or not isinstance(remaining, int)
        ):
            raise ValidationError("remaining must be an integer or null.")
        return NormalizedExternalEvent(
            provider=self.provider,
            kind=ExternalEventKind.RATE_LIMIT,
            delivery_id=_required_text(delivery_id, "delivery_id"),
            subject=f"{provider_name}:{bucket}",
            facts={
                "provider": provider_name,
                "bucket": bucket,
                "available": available,
                "remaining": remaining,
                "reset_at": str(payload.get("reset_at") or ""),
            },
            occurred_at=occurred_at or utc_now(),
        )
