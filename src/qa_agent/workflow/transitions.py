"""Allowed transitions between workflow states."""

from .states import WorkflowState


class InvalidTransitionError(ValueError):
    """Raised when a workflow attempts an unsupported state transition."""


ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.REQUIREMENT_RECEIVED: frozenset(
        {
            WorkflowState.REQUIREMENT_REVIEWING,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.REQUIREMENT_REVIEWING: frozenset(
        {
            WorkflowState.WAITING_PRODUCT_REVISION,
            WorkflowState.REQUIREMENT_ANALYZING,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_PRODUCT_REVISION: frozenset(
        {
            WorkflowState.REQUIREMENT_REVIEWING,
            WorkflowState.REQUIREMENT_ANALYZING,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.REQUIREMENT_ANALYZING: frozenset(
        {
            WorkflowState.TESTCASE_DESIGNING,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.TESTCASE_DESIGNING: frozenset(
        {
            WorkflowState.TESTCASE_REVIEWING,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.TESTCASE_REVIEWING: frozenset(
        {
            WorkflowState.WAITING_CASE_REVISION,
            WorkflowState.WAITING_TESTCASE_APPROVAL,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_CASE_REVISION: frozenset(
        {
            WorkflowState.TESTCASE_DESIGNING,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_TESTCASE_APPROVAL: frozenset(
        {
            WorkflowState.WAITING_CASE_REVISION,
            WorkflowState.WAITING_MANUAL_EXECUTION,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_MANUAL_EXECUTION: frozenset(
        {
            WorkflowState.GENERATING_REPORT,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.GENERATING_REPORT: frozenset(
        {
            WorkflowState.WAITING_REPORT_APPROVAL,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_REPORT_APPROVAL: frozenset(
        {
            WorkflowState.GENERATING_REPORT,
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.MANUAL_INTERVENTION_REQUIRED: frozenset(
        {
            WorkflowState.REQUIREMENT_REVIEWING,
            WorkflowState.WAITING_PRODUCT_REVISION,
            WorkflowState.REQUIREMENT_ANALYZING,
            WorkflowState.TESTCASE_DESIGNING,
            WorkflowState.TESTCASE_REVIEWING,
            WorkflowState.WAITING_CASE_REVISION,
            WorkflowState.WAITING_TESTCASE_APPROVAL,
            WorkflowState.WAITING_MANUAL_EXECUTION,
            WorkflowState.GENERATING_REPORT,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.COMPLETED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
}


def next_states(current: WorkflowState) -> frozenset[WorkflowState]:
    """Return all states that can be reached directly from the current state."""

    return ALLOWED_TRANSITIONS[current]


def can_transition(current: WorkflowState, target: WorkflowState) -> bool:
    """Return whether the target is a valid direct transition."""

    return target in next_states(current)


def validate_transition(current: WorkflowState, target: WorkflowState) -> None:
    """Raise a workflow-level error when a direct transition is invalid."""

    if can_transition(current, target):
        return

    raise InvalidTransitionError(
        f"cannot transition from {current.value!r} to {target.value!r}"
    )
