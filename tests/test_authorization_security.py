from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from agent_orchestrator.authorization import (
    ActionRequest,
    ApprovalService,
    PermissionDecision,
    PermissionPolicy,
    SideEffectCoordinator,
)
from agent_orchestrator.checkpoint import CheckpointService
from agent_orchestrator.errors import (
    AuthorizationDeniedError,
    SideEffectUncertainError,
    ValidationError,
)
from agent_orchestrator.models import Event, EventType, SideEffectStatus, TaskState
from agent_orchestrator.resume import ResumePackageBuilder
from agent_orchestrator.security import REDACTED, SensitiveDataRedactor
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now
from agent_orchestrator.workspace import DriftKind, DriftReport, WorkspaceSnapshot


class PermissionPolicyTests(unittest.TestCase):
    def test_explicit_rules_wildcards_and_safe_defaults(self) -> None:
        policy = PermissionPolicy()
        configured = {
            "git": {"push": "deny", "commit": "ask"},
            "network": {"read_public": "allow"},
            "deployment": {"*": "ask"},
        }
        self.assertEqual(
            policy.evaluate(configured, "network.read_public"),
            PermissionDecision.ALLOW,
        )
        self.assertEqual(
            policy.evaluate(configured, "git.push"),
            PermissionDecision.DENY,
        )
        self.assertEqual(
            policy.evaluate(configured, "deployment.production"),
            PermissionDecision.ASK,
        )
        self.assertEqual(
            policy.evaluate({}, "messaging.send"),
            PermissionDecision.ASK,
        )
        self.assertEqual(
            policy.evaluate({}, "unknown.action"),
            PermissionDecision.DENY,
        )

    def test_action_hash_is_canonical_and_parameter_bound(self) -> None:
        first = ActionRequest(
            action_type="deployment.production",
            logical_step="release",
            parameters={"region": "hk", "version": 1},
            risk_summary="Changes production",
            rollback_plan="Deploy previous version",
        )
        reordered = ActionRequest(
            action_type="deployment.production",
            logical_step="release",
            parameters={"version": 1, "region": "hk"},
            risk_summary="Different prose is not authorization",
            rollback_plan="Still rollback",
        )
        changed = ActionRequest(
            action_type="deployment.production",
            logical_step="release",
            parameters={"version": 2, "region": "hk"},
            risk_summary="Changes production",
            rollback_plan="Deploy previous version",
        )
        self.assertEqual(first.action_hash, reordered.action_hash)
        self.assertNotEqual(first.action_hash, changed.action_hash)

    def test_action_parameters_reject_credentials_but_allow_references(self) -> None:
        with self.assertRaises(ValidationError):
            ActionRequest(
                action_type="network.authenticated_write",
                logical_step="notify",
                parameters={"api_key": "secret-value"},
                risk_summary="External write",
                rollback_plan="Delete the message",
            )
        action = ActionRequest(
            action_type="network.authenticated_write",
            logical_step="notify",
            parameters={"credential_ref": "keyring://notification-service"},
            risk_summary="External write",
            rollback_plan="Delete the message",
        )
        self.assertIn("credential_ref", action.parameters)


class ApprovalAndSideEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.task = self.service.create_task(
            title="Protected deployment",
            objective="Prove AC-05",
            workspace_path=self.workspace,
            permissions_policy={
                "deployment": {"production": "ask"},
                "git": {"push": "deny"},
                "network": {"read_public": "allow"},
            },
        )
        self.service.validate_task(self.task.task_id)
        self.emit(EventType.RUN_REQUESTED, "start")
        self.approvals = ApprovalService(
            store=self.store,
            service=self.service,
        )
        self.effects = SideEffectCoordinator(
            store=self.store,
            approvals=self.approvals,
        )
        self.action = ActionRequest(
            action_type="deployment.production",
            logical_step="release-v1",
            parameters={"environment": "production", "version": "1.0.0"},
            risk_summary="Replaces the production release",
            rollback_plan="Redeploy version 0.9.0",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def emit(self, event_type: EventType, suffix: str) -> None:
        task = self.store.get_task(self.task.task_id)
        result = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task.task_id,
                event_type=event_type,
                source="test",
                dedupe_key=f"{task.task_id}:{suffix}",
                occurred_at=utc_now(),
                expected_version=task.version,
            )
        )
        self.assertEqual(result.outcome, "APPLIED")

    def test_ac05_requires_exact_approval_before_external_execution(self) -> None:
        requested = self.approvals.request(self.task.task_id, self.action)
        self.assertEqual(requested.decision, PermissionDecision.ASK)
        self.assertFalse(requested.authorized)
        approval = requested.approval
        self.assertIsNotNone(approval)
        self.assertEqual(
            self.store.get_task(self.task.task_id).state,
            TaskState.WAITING_FOR_APPROVAL,
        )
        calls: list[str] = []
        with self.assertRaises(AuthorizationDeniedError):
            self.effects.execute(
                self.task.task_id,
                self.action,
                lambda action: calls.append(action.action_type) or "deploy-1",
            )
        self.assertEqual(calls, [])
        with self.assertRaises(ValidationError):
            self.approvals.decide(
                approval.approval_id,
                approved=True,
                expected_action_hash="0" * 64,
                decided_by="reviewer",
            )
        self.assertEqual(
            self.store.get_task(self.task.task_id).state,
            TaskState.WAITING_FOR_APPROVAL,
        )

        self.approvals.decide(
            approval.approval_id,
            approved=True,
            expected_action_hash=self.action.action_hash,
            decided_by="reviewer",
        )
        self.assertEqual(
            self.store.get_task(self.task.task_id).state,
            TaskState.READY,
        )
        executed = self.effects.execute(
            self.task.task_id,
            self.action,
            lambda action: calls.append(action.action_type) or "deploy-1",
            approval_id=approval.approval_id,
        )
        self.assertEqual(executed.record.status, SideEffectStatus.SUCCEEDED)
        self.assertEqual(calls, ["deployment.production"])
        self.assertEqual(
            self.store.get_approval(approval.approval_id).status,
            "CONSUMED",
        )

        duplicate = self.effects.execute(
            self.task.task_id,
            self.action,
            lambda action: calls.append("duplicate") or "deploy-2",
            approval_id=approval.approval_id,
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(calls, ["deployment.production"])

    def test_changed_action_cannot_reuse_old_approval(self) -> None:
        approval = self.approvals.request(
            self.task.task_id,
            self.action,
        ).approval
        self.approvals.decide(
            approval.approval_id,
            approved=True,
            expected_action_hash=self.action.action_hash,
            decided_by="reviewer",
        )
        changed = ActionRequest(
            action_type=self.action.action_type,
            logical_step=self.action.logical_step,
            parameters={"environment": "production", "version": "2.0.0"},
            risk_summary=self.action.risk_summary,
            rollback_plan=self.action.rollback_plan,
        )
        with self.assertRaises(AuthorizationDeniedError):
            self.effects.execute(
                self.task.task_id,
                changed,
                lambda _: "must-not-run",
                approval_id=approval.approval_id,
            )

    def test_duplicate_approval_request_is_idempotent(self) -> None:
        first = self.approvals.request(self.task.task_id, self.action)
        second = self.approvals.request(self.task.task_id, self.action)
        self.assertEqual(
            first.approval.approval_id,
            second.approval.approval_id,
        )
        self.assertEqual(
            len(self.store.list_approvals(self.task.task_id)),
            1,
        )

    def test_denied_approval_cancels_without_executing(self) -> None:
        approval = self.approvals.request(
            self.task.task_id,
            self.action,
        ).approval
        self.approvals.decide(
            approval.approval_id,
            approved=False,
            expected_action_hash=self.action.action_hash,
            decided_by="reviewer",
        )
        self.assertEqual(
            self.store.get_task(self.task.task_id).state,
            TaskState.CANCELLED,
        )
        self.assertEqual(
            self.store.list_side_effects(self.task.task_id),
            [],
        )

    def test_expired_approval_cannot_authorize_execution(self) -> None:
        approval = self.approvals.request(
            self.task.task_id,
            self.action,
        ).approval
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                ("2000-01-01T00:00:00+00:00", approval.approval_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ValidationError):
            self.approvals.decide(
                approval.approval_id,
                approved=True,
                expected_action_hash=self.action.action_hash,
                decided_by="late-reviewer",
            )
        with self.assertRaises(AuthorizationDeniedError):
            self.effects.execute(
                self.task.task_id,
                self.action,
                lambda _: "must-not-run",
                approval_id=approval.approval_id,
            )

    def test_denied_action_never_creates_a_side_effect(self) -> None:
        denied = ActionRequest(
            action_type="git.push",
            logical_step="publish",
            parameters={"remote": "origin"},
            risk_summary="Writes to remote Git",
            rollback_plan="Delete remote branch",
        )
        result = self.approvals.request(self.task.task_id, denied)
        self.assertEqual(result.decision, PermissionDecision.DENY)
        with self.assertRaises(AuthorizationDeniedError):
            self.effects.execute(
                self.task.task_id,
                denied,
                lambda _: "must-not-run",
            )
        self.assertEqual(
            self.store.list_side_effects(self.task.task_id),
            [],
        )

    def test_unknown_result_is_not_replayed_and_can_be_reconciled(self) -> None:
        allowed = ActionRequest(
            action_type="network.read_public",
            logical_step="read-release",
            parameters={"url": "https://example.test/release"},
            risk_summary="Reads public data",
            rollback_plan="No rollback required",
        )
        calls = 0

        def uncertain(_: ActionRequest) -> str:
            nonlocal calls
            calls += 1
            raise RuntimeError("connection dropped after request")

        with self.assertRaises(SideEffectUncertainError):
            self.effects.execute(self.task.task_id, allowed, uncertain)
        record = self.store.list_side_effects(self.task.task_id)[0]
        self.assertEqual(record.status, SideEffectStatus.UNKNOWN)
        with self.assertRaises(SideEffectUncertainError):
            self.effects.execute(self.task.task_id, allowed, uncertain)
        self.assertEqual(calls, 1)
        reconciled = self.effects.reconcile(
            record.effect_id,
            succeeded=True,
            external_result_id="confirmed-result",
        )
        self.assertEqual(reconciled.status, SideEffectStatus.SUCCEEDED)

        second = ActionRequest(
            action_type="network.read_public",
            logical_step="read-release-again",
            parameters={"url": "https://example.test/release"},
            risk_summary="Reads public data",
            rollback_plan="No rollback required",
        )
        with self.assertRaises(SideEffectUncertainError):
            self.effects.execute(self.task.task_id, second, uncertain)
        second_record = self.store.list_side_effects(self.task.task_id)[1]
        confirmed_failed = self.effects.reconcile(
            second_record.effect_id,
            succeeded=False,
        )
        self.assertEqual(confirmed_failed.status, SideEffectStatus.FAILED)

    def test_restart_marks_stale_pending_effect_unknown(self) -> None:
        allowed = ActionRequest(
            action_type="network.read_public",
            logical_step="crash-window",
            parameters={"url": "https://example.test"},
            risk_summary="Reads public data",
            rollback_plan="No rollback required",
        )
        record, created = self.store.reserve_side_effect(
            effect_id="effect_crashed",
            task_id=self.task.task_id,
            approval_id=None,
            idempotency_key=allowed.idempotency_key(self.task.task_id),
            logical_step=allowed.logical_step,
            action_type=allowed.action_type,
            parameters_hash=allowed.action_hash,
        )
        self.assertTrue(created)
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                "UPDATE side_effects SET updated_at = ? WHERE effect_id = ?",
                ("2000-01-01T00:00:00+00:00", record.effect_id),
            )
            connection.commit()
        finally:
            connection.close()
        recovered = self.effects.recover_stale(older_than_seconds=1)
        self.assertEqual([item.effect_id for item in recovered], [record.effect_id])
        self.assertEqual(recovered[0].status, SideEffectStatus.UNKNOWN)


class SensitiveArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.task = self.service.create_task(
            title="Redaction task",
            objective="Keep durable artifacts free of credentials",
            workspace_path=self.workspace,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_redactor_handles_text_mappings_and_explicit_values(self) -> None:
        redactor = SensitiveDataRedactor(["custom-sensitive-value"])
        text = (
            "token=abc123456789 sk_abcdefghijklmnop "
            "Bearer abcdefghijklmnop custom-sensitive-value"
        )
        result = redactor.redact_text(text)
        self.assertNotIn("abc123456789", result)
        self.assertNotIn("sk_", result)
        self.assertNotIn("custom-sensitive-value", result)
        self.assertIn(REDACTED, result)
        mapping = redactor.redact(
            {
                "password": "hidden",
                "credential_ref": "keyring://safe",
                "token_budget": 1000,
            }
        )
        self.assertEqual(mapping["password"], REDACTED)
        self.assertEqual(mapping["credential_ref"], "keyring://safe")
        self.assertEqual(mapping["token_budget"], 1000)

    def test_checkpoint_resume_event_and_audit_are_redacted(self) -> None:
        snapshot = WorkspaceSnapshot(
            path=str(self.workspace),
            observed_at=utc_now(),
            vcs_type=None,
            branch=None,
            head=None,
            files={},
            fingerprint="plain",
        )
        checkpoints = CheckpointService(self.store, self.root / "checkpoints")
        record = checkpoints.create(
            task=self.task,
            run_id=None,
            workspace=snapshot,
            progress={"token": "checkpoint-secret", "pending": []},
            decisions=[],
            current_block={"reason": "pause"},
            next_action={"description": "continue"},
            verification={},
            permissions={"credential_ref": "keyring://safe"},
        )
        payload = checkpoints.load(record)
        self.assertEqual(payload["progress"]["token"], REDACTED)
        raw = Path(record.payload_path).read_text(encoding="utf-8")
        self.assertNotIn("checkpoint-secret", raw)
        package = ResumePackageBuilder().build(
            task=self.task,
            checkpoint=record,
            payload=payload,
            current_workspace=snapshot,
            drift=DriftReport(
                kind=DriftKind.NONE,
                changed_paths=(),
                conflicting_paths=(),
                summary="token=resume-secret",
            ),
            thread_id=None,
        )
        self.assertNotIn("resume-secret", package.prompt)

        self.store.append_audit(
            task_id=self.task.task_id,
            run_id=None,
            kind="TEST_SECRET",
            payload={"api_key": "audit-secret"},
        )
        entries = self.store.list_audit(task_id=self.task.task_id)
        self.assertEqual(entries[-1].payload["api_key"], REDACTED)

        event = Event(
            event_id="evt_sensitive_payload",
            task_id=self.task.task_id,
            event_type=EventType.TASK_VALIDATED,
            source="test",
            dedupe_key="sensitive-event",
            payload={"token": "event-secret"},
            occurred_at=utc_now(),
        )
        self.service.process_event(event)
        stored = self.store.get_event(event.event_id)
        self.assertNotIn("event-secret", stored["payload_json"])
        self.assertIn(REDACTED, stored["payload_json"])

    def test_task_contract_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_task(
                title="Unsafe task",
                objective="Do work",
                workspace_path=self.workspace,
                acceptance_policy={"api_key": "must-not-persist"},
            )


if __name__ == "__main__":
    unittest.main()
