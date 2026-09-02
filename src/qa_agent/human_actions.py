"""Human approval and manual test execution services."""

from __future__ import annotations

from pathlib import Path

from .schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactStatus,
    ExecutionBatch,
    HumanApproval,
    TestDesign,
)
from .evidence import EvidenceService
from .storage import ArtifactStore
from .workflow.models import ArtifactPointer, WorkflowRun, WorkflowTransition
from .workflow.state_machine import WorkflowStateMachine
from .workflow.states import WorkflowState
from .workflow.transitions import validate_transition


class HumanActionError(ValueError):
    """Raised when a human action cannot safely enter the workflow."""


class HumanApprovalService:
    """Validate and persist a human decision before changing workflow state."""

    _ROUTES = {
        (WorkflowState.WAITING_PRODUCT_REVISION, ApprovalType.RISK_ACCEPTANCE): {
            ApprovalDecision.APPROVED: WorkflowState.REQUIREMENT_ANALYZING,
        },
        (WorkflowState.WAITING_TESTCASE_APPROVAL, ApprovalType.TESTCASE_APPROVAL): {
            ApprovalDecision.APPROVED: WorkflowState.WAITING_MANUAL_EXECUTION,
            ApprovalDecision.REJECTED: WorkflowState.WAITING_CASE_REVISION,
            ApprovalDecision.CHANGES_REQUESTED: WorkflowState.WAITING_CASE_REVISION,
        },
        (WorkflowState.WAITING_REPORT_APPROVAL, ApprovalType.REPORT_APPROVAL): {
            ApprovalDecision.APPROVED: WorkflowState.COMPLETED,
            ApprovalDecision.REJECTED: WorkflowState.GENERATING_REPORT,
            ApprovalDecision.CHANGES_REQUESTED: WorkflowState.GENERATING_REPORT,
        },
    }
    _TARGET_TYPES = {
        ApprovalType.RISK_ACCEPTANCE: "requirement_review",
        ApprovalType.TESTCASE_APPROVAL: "test_design",
        ApprovalType.REPORT_APPROVAL: "test_report",
    }

    def __init__(self, workflow: WorkflowRun, store: ArtifactStore) -> None:
        self.workflow = workflow
        self.store = store

    def submit(self, approval: HumanApproval) -> WorkflowTransition:
        if approval.meta.project_id != self.workflow.project_id:
            raise HumanActionError("approval project_id does not match workflow")
        if approval.meta.artifact_type != "human_approval":
            raise HumanActionError("approval meta.artifact_type must be 'human_approval'")
        if approval.meta.status is not ArtifactStatus.COMPLETED:
            raise HumanActionError("approval status must be completed")

        decisions = self._ROUTES.get(
            (self.workflow.current_state, approval.approval_type)
        )
        if decisions is None:
            raise HumanActionError(
                f"{approval.approval_type.value} is not allowed in state "
                f"{self.workflow.current_state.value}"
            )
        target_state = decisions[approval.decision]
        self._validate_target_artifact(approval)
        validate_transition(self.workflow.current_state, target_state)

        self.store.save_decision(
            self.workflow.project_id,
            approval.meta.artifact_id,
            approval,
        )
        if (
            approval.approval_type is ApprovalType.RISK_ACCEPTANCE
            and approval.decision is ApprovalDecision.APPROVED
        ):
            self.workflow.accepted_requirement_review = ArtifactPointer(
                artifact_id=approval.target_artifact_id,
                artifact_type=approval.target_artifact_type,
                version=approval.target_artifact_version,
            )
        transition = WorkflowStateMachine(self.workflow).transition(
            target_state,
            triggered_by=approval.decided_by,
            reason=approval.comment or f"人工审批结论：{approval.decision.value}",
            related_artifacts=[
                ArtifactPointer(
                    artifact_id=approval.target_artifact_id,
                    artifact_type=approval.target_artifact_type,
                    version=approval.target_artifact_version,
                ),
                ArtifactPointer(
                    artifact_id=approval.meta.artifact_id,
                    artifact_type=approval.meta.artifact_type,
                    version=approval.meta.version,
                ),
            ],
            occurred_at=approval.decided_at,
        )
        self.store.save_workflow(self.workflow.project_id, self.workflow)
        return transition

    def _validate_target_artifact(self, approval: HumanApproval) -> None:
        expected_type = self._TARGET_TYPES[approval.approval_type]
        if approval.target_artifact_type != expected_type:
            raise HumanActionError(
                f"{approval.approval_type.value} must target {expected_type!r}"
            )
        active = self.workflow.active_artifacts.get(approval.target_artifact_type)
        if active is None:
            raise HumanActionError(
                f"no active artifact for {approval.target_artifact_type!r}"
            )
        if (
            active.artifact_id != approval.target_artifact_id
            or active.version != approval.target_artifact_version
        ):
            raise HumanActionError("approval target is not the active artifact version")
        self.store.load_artifact(
            self.workflow.project_id,
            active.artifact_type,
            active.artifact_id,
            active.version,
        )


class ManualExecutionService:
    """Validate a complete set of manual results and enter report generation."""

    def __init__(self, workflow: WorkflowRun, store: ArtifactStore) -> None:
        self.workflow = workflow
        self.store = store

    def submit(self, execution: ExecutionBatch) -> tuple[Path, WorkflowTransition]:
        if self.workflow.current_state is not WorkflowState.WAITING_MANUAL_EXECUTION:
            raise HumanActionError(
                "manual execution can only be submitted while waiting for execution"
            )
        if execution.meta.project_id != self.workflow.project_id:
            raise HumanActionError("execution project_id does not match workflow")
        if execution.meta.artifact_type != "test_execution":
            raise HumanActionError(
                "execution meta.artifact_type must be 'test_execution'"
            )
        if execution.meta.status is not ArtifactStatus.COMPLETED:
            raise HumanActionError("execution status must be completed")
        try:
            EvidenceService(self.store).verify_batch(
                self.workflow.project_id,
                execution,
            )
        except ValueError as exc:
            raise HumanActionError(f"execution evidence rejected: {exc}") from exc

        design_pointer = self.workflow.active_artifacts.get("test_design")
        if design_pointer is None:
            raise HumanActionError("active test_design artifact is required")
        if (
            design_pointer.artifact_id != execution.test_design_id
            or design_pointer.version != execution.test_design_version
        ):
            raise HumanActionError("execution references a stale test design version")

        design_payload = self.store.load_artifact(
            self.workflow.project_id,
            "test_design",
            design_pointer.artifact_id,
            design_pointer.version,
        )
        design = TestDesign.model_validate(design_payload)
        expected_cases = {
            (case.case_id, case.version) for case in design.test_cases
        }
        actual_cases = {
            (record.case_id, record.case_version) for record in execution.records
        }
        missing = expected_cases - actual_cases
        unknown = actual_cases - expected_cases
        if missing:
            raise HumanActionError(
                "execution is incomplete; missing cases: "
                + ", ".join(f"{case_id}:v{version}" for case_id, version in sorted(missing))
            )
        if unknown:
            raise HumanActionError(
                "execution contains unknown cases: "
                + ", ".join(f"{case_id}:v{version}" for case_id, version in sorted(unknown))
            )

        validate_transition(
            self.workflow.current_state,
            WorkflowState.GENERATING_REPORT,
        )
        path = self.store.save_artifact(execution)
        execution_pointer = ArtifactPointer(
            artifact_id=execution.meta.artifact_id,
            artifact_type=execution.meta.artifact_type,
            version=execution.meta.version,
        )
        machine = WorkflowStateMachine(self.workflow)
        machine.set_active_artifact("test_execution", execution_pointer)
        transition = machine.transition(
            WorkflowState.GENERATING_REPORT,
            triggered_by=execution.meta.created_by,
            reason="人工测试执行结果已完整提交",
            related_artifacts=[design_pointer, execution_pointer],
        )
        self.store.save_workflow(self.workflow.project_id, self.workflow)
        return path, transition
