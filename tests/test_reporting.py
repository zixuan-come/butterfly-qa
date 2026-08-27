import json
from datetime import datetime, timezone

import pytest

from qa_agent.agent_protocol import AgentRequest, AgentResponse, AgentStatus
from qa_agent.agent_runner import AgentRunner
from qa_agent.human_actions import HumanApprovalService
from qa_agent.orchestrator import WorkflowOrchestrator
from qa_agent.reporting import ReportValidationError, TestReportService as ReportService
from qa_agent.schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    Evidence,
    ExecutionBatch,
    ExecutionRecord,
    HumanApproval,
    TestReport as ReportModel,
)
from qa_agent.storage import ArtifactStore
from qa_agent.workflow.models import ArtifactPointer, WorkflowRun
from qa_agent.workflow.states import WorkflowState


def now():
    return datetime.now(timezone.utc)


def make_execution() -> ExecutionBatch:
    timestamp = now()
    raw_records = [
        ("TC-001", "passed", [], []),
        ("TC-002", "failed", ["BUG-001"], ["evidence/failure.png"]),
        ("TC-003", "blocked", [], ["evidence/service.log"]),
        ("TC-004", "skipped", [], []),
    ]
    records = []
    for case_id, result, defects, evidence_paths in raw_records:
        evidence = [
            Evidence(
                evidence_id=f"evidence-{case_id}",
                evidence_type="screenshot" if path.endswith(".png") else "log",
                path=path,
                description=f"{case_id} 测试证据",
            )
            for path in evidence_paths
        ]
        records.append(
            ExecutionRecord(
                record_id=f"record-{case_id}",
                case_id=case_id,
                case_version=1,
                environment="SIT",
                executed_by="tester-001",
                executed_at=timestamp,
                result=result,
                actual_result=f"{case_id} 的实际执行结果",
                defect_refs=defects,
                evidence=evidence,
            )
        )
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
        records=records,
    )


def make_workflow() -> WorkflowRun:
    timestamp = now()
    return WorkflowRun(
        workflow_id="wf-001",
        project_id="demo-project",
        current_state=WorkflowState.GENERATING_REPORT,
        active_artifacts={
            "test_design": ArtifactPointer(
                artifact_id="design-001",
                artifact_type="test_design",
                version=1,
            ),
            "test_execution": ArtifactPointer(
                artifact_id="execution-001",
                artifact_type="test_execution",
                version=1,
            ),
        },
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_report(**overrides) -> ReportModel:
    timestamp = now()
    values = {
        "meta": ArtifactMeta(
            artifact_id="report-001",
            artifact_type="test_report",
            project_id="demo-project",
            status=ArtifactStatus.PENDING,
            source_artifacts=["design-001", "execution-001"],
            created_by="main-flow-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        "scope": "修改收货地址",
        "environment": "SIT",
        "total_cases": 4,
        "passed": 1,
        "failed": 1,
        "blocked": 1,
        "skipped": 1,
        "defect_refs": ["BUG-001"],
        "risk_summary": ["一条用例阻塞，一条用例跳过"],
        "conclusion": "存在失败和阻塞项，需要处理后重新评估。",
        "trace_refs": {
            "REQ-001": [
                "record-TC-001",
                "record-TC-002",
                "record-TC-003",
                "record-TC-004",
            ]
        },
    }
    values.update(overrides)
    return ReportModel(**values)


def prepare_store(tmp_path) -> ArtifactStore:
    store = ArtifactStore(tmp_path)
    evidence_root = store.project_root("demo-project") / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "failure.png").write_bytes(b"failure screenshot")
    (evidence_root / "service.log").write_text(
        "service unavailable\n", encoding="utf-8"
    )
    store.save_artifact(make_execution())
    return store


def test_report_is_verified_rendered_and_waits_for_approval(tmp_path):
    workflow = make_workflow()
    saved = ReportService(workflow, prepare_store(tmp_path)).accept(
        make_report()
    )

    markdown = saved.markdown_path.read_text(encoding="utf-8")
    assert saved.json_path.is_file()
    assert "# 测试报告：修改收货地址" in markdown
    assert "| 4 | 1 | 1 | 1 | 1 |" in markdown
    assert "BUG-001" in markdown
    assert "evidence/failure.png" in markdown
    assert workflow.current_state is WorkflowState.WAITING_REPORT_APPROVAL
    assert workflow.active_artifacts["test_report"].artifact_id == "report-001"


def test_report_rejects_incorrect_statistics_without_saving(tmp_path):
    workflow = make_workflow()

    with pytest.raises(ReportValidationError, match="statistics do not match"):
        ReportService(workflow, prepare_store(tmp_path)).accept(
            make_report(passed=2)
        )

    assert workflow.current_state is WorkflowState.GENERATING_REPORT
    assert "test_report" not in workflow.active_artifacts


def test_report_rejects_defects_not_found_in_execution(tmp_path):
    workflow = make_workflow()

    with pytest.raises(ReportValidationError, match="defect_refs do not match"):
        ReportService(workflow, prepare_store(tmp_path)).accept(
            make_report(defect_refs=["BUG-001", "BUG-HALLUCINATED"])
        )


def test_report_rejection_returns_to_generation_and_records_revision(tmp_path):
    workflow = make_workflow()
    store = prepare_store(tmp_path)
    ReportService(workflow, store).accept(make_report())

    timestamp = now()
    approval = HumanApproval(
        meta=ArtifactMeta(
            artifact_id="approval-report-rejected",
            artifact_type="human_approval",
            project_id="demo-project",
            status=ArtifactStatus.COMPLETED,
            created_by="tester-001",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        approval_type=ApprovalType.REPORT_APPROVAL,
        target_artifact_id="report-001",
        target_artifact_type="test_report",
        target_artifact_version=1,
        decision=ApprovalDecision.REJECTED,
        decided_by="tester-001",
        decided_at=timestamp,
        comment="补充失败用例的风险说明",
    )

    transition = HumanApprovalService(workflow, store).submit(approval)

    assert transition.to_state is WorkflowState.GENERATING_REPORT
    assert workflow.current_state is WorkflowState.GENERATING_REPORT
    assert workflow.report_revision_rounds == 1


class QueueRunner(AgentRunner):
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def run(self, request: AgentRequest) -> AgentResponse:
        timestamp = now()
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=next(self.outputs),
            started_at=timestamp,
            completed_at=timestamp,
        )


def test_orchestrator_closes_report_generation_stage(tmp_path):
    workflow = make_workflow()
    store = prepare_store(tmp_path)
    action = json.dumps(
        {
            "action": "invoke_agent",
            "target_role": "main_flow",
            "skill_name": "test-report",
            "target_state": "generating_report",
            "reason": "根据人工执行结果生成测试报告",
            "expected_output_type": "test_report",
        },
        ensure_ascii=False,
    )
    orchestrator = WorkflowOrchestrator(
        workflow,
        QueueRunner([action, make_report().model_dump_json()]),
        artifact_store=store,
    )

    result = orchestrator.step()

    assert result.error is None
    assert result.markdown_path is not None
    assert result.transition.to_state is WorkflowState.WAITING_REPORT_APPROVAL
    assert workflow.current_state is WorkflowState.WAITING_REPORT_APPROVAL
