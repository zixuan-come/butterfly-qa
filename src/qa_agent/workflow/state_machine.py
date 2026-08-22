"""State machine operations for a persistent workflow run."""

from datetime import datetime, timezone

from .models import ArtifactPointer, WorkflowRun, WorkflowTransition
from .states import WorkflowState
from .transitions import next_states, validate_transition


class WorkflowStateMachine:
    """Apply validated state transitions to a workflow run."""

    def __init__(self, workflow: WorkflowRun) -> None:
        self.workflow = workflow

    @property
    def current_state(self) -> WorkflowState:
        return self.workflow.current_state

    @property
    def is_terminal(self) -> bool:
        return self.current_state in {
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
        }

    def available_states(self) -> frozenset[WorkflowState]:
        return next_states(self.current_state)

    def set_active_artifact(self, name: str, artifact: ArtifactPointer) -> None:
        if not name.strip():
            raise ValueError("artifact name must not be empty")
        self.workflow.active_artifacts[name] = artifact
        self.workflow.updated_at = datetime.now(timezone.utc)

    def transition(
        self,
        target: WorkflowState,
        *,
        triggered_by: str,
        reason: str,
        related_artifacts: list[ArtifactPointer] | None = None,
        occurred_at: datetime | None = None,
    ) -> WorkflowTransition:
        if not triggered_by.strip():
            raise ValueError("triggered_by must not be empty")
        if not reason.strip():
            raise ValueError("reason must not be empty")

        current = self.current_state
        validate_transition(current, target)
        transition_time = occurred_at or datetime.now(timezone.utc)

        record = WorkflowTransition(
            from_state=current,
            to_state=target,
            triggered_by=triggered_by,
            reason=reason,
            occurred_at=transition_time,
            related_artifacts=related_artifacts or [],
        )

        if target is WorkflowState.MANUAL_INTERVENTION_REQUIRED:
            self.workflow.manual_resume_state = current
        elif current is WorkflowState.MANUAL_INTERVENTION_REQUIRED:
            self.workflow.manual_resume_state = None

        self._update_revision_rounds(current, target)
        self.workflow.current_state = target
        self.workflow.transition_history.append(record)
        self.workflow.updated_at = transition_time
        return record

    def _update_revision_rounds(
        self,
        current: WorkflowState,
        target: WorkflowState,
    ) -> None:
        if target is WorkflowState.WAITING_PRODUCT_REVISION:
            self.workflow.requirement_revision_rounds += 1
        elif target is WorkflowState.WAITING_CASE_REVISION:
            self.workflow.testcase_revision_rounds += 1
        elif (
            current is WorkflowState.WAITING_REPORT_APPROVAL
            and target is WorkflowState.GENERATING_REPORT
        ):
            self.workflow.report_revision_rounds += 1
