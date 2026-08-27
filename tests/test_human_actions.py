from datetime import datetime, timezone

import pytest

from qa_agent.human_actions import (
    HumanActionError,
    HumanApprovalService,
    ManualExecutionService,
)
from qa_agent.schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    Evidence,
    ExecutionBatch,
    ExecutionRecord,
    HumanApproval,
    TestCase as CaseModel,
    TestDesign as DesignModel,
    TestPoint as PointModel,
    TestStep as StepModel,
)
from qa_agent.storage import ArtifactStore, ArtifactStoreError
from qa_agent.workflow.models import ArtifactPointer, WorkflowRun
from qa_agent.workflow.states import WorkflowState


def now():
    return datetime.now(timezone.utc)


def make_workflow(state: WorkflowState) -> WorkflowRun:
    timestamp = now()
    return WorkflowRun(
        workflow_id="wf-001",
        project_id="demo-project",
        current_state=state,
        active_artifacts={
            "test_design": ArtifactPointer(
                artifact_id="design-001",
                artifact_type="test_design",
                version=1,
            )
        },
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_approval(decision: ApprovalDecision, comment: str = "") -> HumanApproval:
    timestamp = now()
    return HumanApproval(
        meta=ArtifactMeta(
            artifact_id=f"approval-{decision.value}",
            artifact_type="human_approval",
            project_id="demo-project",
            status=ArtifactStatus.COMPLETED,
            created_by="tester-001",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        approval_type=ApprovalType.TESTCASE_APPROVAL,
        target_artifact_id="design-001",
        target_artifact_type="test_design",
        target_artifact_version=1,
        decision=decision,
        decided_by="tester-001",
        decided_at=timestamp,
        comment=comment,
    )


def make_design() -> DesignModel:
    timestamp = now()
    return DesignModel(
        meta=ArtifactMeta(
            artifact_id="design-001",
            artifact_type="test_design",
            project_id="demo-project",
            status=ArtifactStatus.APPROVED,
            created_by="test-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        test_points=[
            PointModel(
                test_point_id="TP-001",
                requirement_refs=["REQ-001"],
                category="normal",
                description="保存地址",
            )
        ],
        test_cases=[
            CaseModel(
                case_id="TC-001",
                requirement_refs=["REQ-001"],
                test_point_refs=["TP-001"],
                title="保存有效地址",
                priority="P1",
                steps=[
                    StepModel(
                        step_no=1,
                        action="保存地址",
                        expected_result="保存成功",
                    )
                ],
            ),
            CaseModel(
                case_id="TC-002",
                requirement_refs=["REQ-001"],
                test_point_refs=["TP-001"],
                title="保存失败",
                priority="P1",
                steps=[
                    StepModel(
                        step_no=1,
                        action="提交无效地址",
                        expected_result="提示失败原因",
                    )
                ],
            ),
        ],
    )


def make_execution(case_ids=("TC-001", "TC-002")) -> ExecutionBatch:
    timestamp = now()
    return ExecutionBatch(
        meta=ArtifactMeta(
            artifact_id="execution-001",
            artifact_type="test_execution",
            project_id="demo-project",
            status=ArtifactStatus.COMPLETED,
            created_by="tester-001",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        test_design_id="design-001",
        test_design_version=1,
        records=[
            ExecutionRecord(
                record_id=f"record-{case_id}",
                case_id=case_id,
                case_version=1,
                environment="test",
                executed_by="tester-001",
                executed_at=timestamp,
                result="passed",
                actual_result="结果符合预期",
            )
            for case_id in case_ids
        ],
    )


def test_testcase_approval_advances_to_manual_execution(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_TESTCASE_APPROVAL)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())

    transition = HumanApprovalService(workflow, store).submit(
        make_approval(ApprovalDecision.APPROVED)
    )

    assert transition.to_state is WorkflowState.WAITING_MANUAL_EXECUTION
    assert workflow.current_state is WorkflowState.WAITING_MANUAL_EXECUTION
    assert store.load_decision("demo-project", "approval-approved")["decision"] == "approved"


def test_testcase_changes_requested_returns_for_revision(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_TESTCASE_APPROVAL)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())

    transition = HumanApprovalService(workflow, store).submit(
        make_approval(ApprovalDecision.CHANGES_REQUESTED, "补充权限场景")
    )

    assert transition.to_state is WorkflowState.WAITING_CASE_REVISION
    assert workflow.testcase_revision_rounds == 1


def test_approval_rejects_stale_target_before_saving_decision(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_TESTCASE_APPROVAL)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())
    approval = make_approval(ApprovalDecision.APPROVED)
    approval.target_artifact_version = 2

    with pytest.raises(HumanActionError, match="not the active artifact"):
        HumanApprovalService(workflow, store).submit(approval)

    with pytest.raises(ArtifactStoreError, match="does not exist"):
        store.load_decision("demo-project", "approval-approved")
    assert workflow.current_state is WorkflowState.WAITING_TESTCASE_APPROVAL


def test_complete_execution_is_saved_and_starts_report_generation(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_MANUAL_EXECUTION)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())

    path, transition = ManualExecutionService(workflow, store).submit(
        make_execution()
    )

    assert path.is_file()
    assert transition.to_state is WorkflowState.GENERATING_REPORT
    assert workflow.active_artifacts["test_execution"].artifact_id == "execution-001"


def test_incomplete_execution_is_rejected_without_state_change(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_MANUAL_EXECUTION)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())

    with pytest.raises(HumanActionError, match="missing cases: TC-002:v1"):
        ManualExecutionService(workflow, store).submit(
            make_execution(case_ids=("TC-001",))
        )

    assert workflow.current_state is WorkflowState.WAITING_MANUAL_EXECUTION
    assert "test_execution" not in workflow.active_artifacts


def test_execution_with_missing_evidence_is_rejected_without_state_change(tmp_path):
    workflow = make_workflow(WorkflowState.WAITING_MANUAL_EXECUTION)
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())
    execution = make_execution()
    execution.records[0].evidence.append(
        Evidence(
            evidence_id="missing-log",
            evidence_type="log",
            path="evidence/missing.log",
            description="缺失的执行日志",
        )
    )

    with pytest.raises(HumanActionError, match="evidence file does not exist"):
        ManualExecutionService(workflow, store).submit(execution)

    assert workflow.current_state is WorkflowState.WAITING_MANUAL_EXECUTION
    assert "test_execution" not in workflow.active_artifacts
