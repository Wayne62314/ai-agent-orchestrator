from __future__ import annotations

import unittest

from agent_orchestrator.errors import InvalidTransitionError
from agent_orchestrator.models import EventType, TaskState
from agent_orchestrator.state_machine import allowed_events, next_state


class StateMachineTests(unittest.TestCase):
    def test_happy_path_transitions(self) -> None:
        state = TaskState.DRAFT
        for event, expected in (
            (EventType.TASK_VALIDATED, TaskState.READY),
            (EventType.RUN_REQUESTED, TaskState.RUNNING),
            (EventType.PHASE_COMPLETED, TaskState.VERIFYING),
            (EventType.CHECKS_PASSED, TaskState.SUCCEEDED),
        ):
            state = next_state(state, event)
            self.assertEqual(state, expected)

    def test_wait_and_resume_transition(self) -> None:
        waiting = next_state(TaskState.RUNNING, EventType.SIGNAL_REQUIRED)
        self.assertEqual(waiting, TaskState.WAITING_FOR_SIGNAL)
        self.assertEqual(
            next_state(waiting, EventType.SIGNAL_RECEIVED),
            TaskState.READY,
        )
        self.assertEqual(
            next_state(waiting, EventType.SIGNAL_TIMEOUT),
            TaskState.NEEDS_ATTENTION,
        )

    def test_completed_phase_can_request_normal_continuation(self) -> None:
        self.assertEqual(
            next_state(
                TaskState.VERIFYING,
                EventType.CONTINUATION_REQUIRED,
            ),
            TaskState.READY,
        )

    def test_manual_confirmation_can_complete_or_return_to_work(self) -> None:
        waiting = next_state(
            TaskState.VERIFYING,
            EventType.MANUAL_CONFIRMATION_REQUIRED,
        )
        self.assertEqual(waiting, TaskState.NEEDS_ATTENTION)
        self.assertEqual(
            next_state(waiting, EventType.MANUAL_CONFIRMED),
            TaskState.SUCCEEDED,
        )
        self.assertEqual(
            next_state(waiting, EventType.MANUAL_REJECTED),
            TaskState.READY,
        )

    def test_explicit_pause_and_resume_transition(self) -> None:
        self.assertEqual(
            next_state(TaskState.RUNNING, EventType.PAUSE_REQUESTED),
            TaskState.PAUSED,
        )
        self.assertEqual(
            next_state(TaskState.PAUSED, EventType.RESUME_REQUESTED),
            TaskState.READY,
        )

    def test_cancel_is_allowed_from_every_non_terminal_state(self) -> None:
        for state in TaskState:
            if state.is_terminal:
                continue
            with self.subTest(state=state):
                self.assertEqual(
                    next_state(state, EventType.CANCEL_REQUESTED),
                    TaskState.CANCELLED,
                )

    def test_terminal_state_rejects_more_events(self) -> None:
        for state in (TaskState.SUCCEEDED, TaskState.CANCELLED):
            with self.subTest(state=state):
                with self.assertRaises(InvalidTransitionError):
                    next_state(state, EventType.CANCEL_REQUESTED)

    def test_allowed_events_are_stably_sorted(self) -> None:
        values = [item.value for item in allowed_events(TaskState.RUNNING)]
        self.assertEqual(values, sorted(values))
        self.assertIn(EventType.PHASE_COMPLETED.value, values)
        self.assertIn(EventType.CANCEL_REQUESTED.value, values)


if __name__ == "__main__":
    unittest.main()
