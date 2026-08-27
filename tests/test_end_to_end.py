import json
from datetime import datetime, timezone

from qa_agent.agent_protocol import AgentRequest, AgentResponse, AgentRole, AgentStatus
from qa_agent.agent_runner import AgentRunner
from qa_agent.evidence import EvidenceService
from qa_agent.human_actions import HumanApprovalService, ManualExecutionService
from qa_agent.orchestrator import WorkflowOrchestrator
from qa_agent.project import InputCategory, ProjectManager
from qa_agent.schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    ExecutionBatch,
    ExecutionRecord,
    HumanApproval,
)
from qa_agent.storage import ArtifactStore
from qa_agent.workflow.models import WorkflowRun
from qa_agent.workflow.states import WorkflowState


class QueueRunner(AgentRunner):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = iter(outputs)

    def run(self, request: AgentRequest) -> AgentResponse:
        timestamp = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=next(self.outputs),
            started_at=timestamp,
            completed_at=timestamp,
        )


def _meta(artifact_id: str, artifact_type: str, project_id: str) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "project_id": project_id,
        "version": 1,
        "status": "completed",
        "source_artifacts": [],
        "created_by": "test-agent",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _action(**values) -> str:
    return json.dumps(values, ensure_ascii=False)


def _agent_outputs(project_id: str) -> list[str]:
    review = {
        "meta": _meta("review-001", "requirement_review", project_id),
        "decision": "pass",
        "issues": [],
        "assumptions": [],
        "open_questions": [],
    }
    analysis = {
        "meta": _meta("analysis-001", "requirement_analysis", project_id),
        "requirements": [
            {
                "requirement_id": "REQ-001",
                "title": "修改未发货订单地址",
                "description": "用户可以修改未发货订单的收货地址。",
                "actors": ["用户"],
                "business_rules": ["已发货订单不可修改"],
                "preconditions": ["订单存在且未发货"],
                "postconditions": ["订单展示最新地址"],
                "open_questions": [],
            }
        ],
        "flows": ["进入订单详情 -> 修改地址 -> 保存"],
        "states": ["未发货", "已发货"],
        "permissions": ["订单所有者"],
        "assumptions": [],
    }
    design = {
        "meta": _meta("design-001", "test_design", project_id),
        "test_points": [
            {
                "test_point_id": "TP-001",
                "requirement_refs": ["REQ-001"],
                "category": "normal",
                "description": "未发货订单可成功修改地址",
                "risk": "high",
            }
        ],
        "test_cases": [
            {
                "case_id": "TC-001",
                "version": 1,
                "requirement_refs": ["REQ-001"],
                "test_point_refs": ["TP-001"],
                "title": "修改未发货订单地址成功",
                "priority": "P0",
                "preconditions": ["存在未发货订单"],
                "test_data": ["有效收货地址"],
                "steps": [
                    {
                        "step_no": 1,
                        "action": "进入订单详情并保存新地址",
                        "expected_result": "提示修改成功并展示新地址",
                    }
                ],
                "tags": ["address-change"],
            }
        ],
    }
    case_review = {
        "meta": _meta("case-review-001", "testcase_review", project_id),
        "decision": "pass",
        "issues": [],
        "coverage_summary": "已覆盖核心成功路径。",
    }
    return [
        _action(
            action="invoke_agent",
            target_role="test_analysis_design",
            skill_name="requirement-review",
            target_state="requirement_reviewing",
            reason="执行需求评审",
            expected_output_type="requirement_review",
        ),
        json.dumps(review, ensure_ascii=False),
        _action(
            action="invoke_agent",
            target_role="test_analysis_design",
            skill_name="requirement-analysis",
            target_state="requirement_analyzing",
            reason="分析需求",
            expected_output_type="requirement_analysis",
        ),
        json.dumps(analysis, ensure_ascii=False),
        _action(
            action="invoke_agent",
            target_role="test_analysis_design",
            skill_name="testcase-design",
            target_state="testcase_designing",
            reason="设计测试点和功能测试用例",
            expected_output_type="test_design",
        ),
        json.dumps(design, ensure_ascii=False),
        _action(
            action="invoke_agent",
            target_role="testcase_review",
            skill_name="testcase-evaluation",
            target_state="testcase_reviewing",
            reason="评审测试用例",
            expected_output_type="testcase_review",
        ),
        json.dumps(case_review, ensure_ascii=False),
        _action(
            action="transition",
            target_state="waiting_testcase_approval",
            reason="用例评审通过，等待人工确认",
        ),
        _action(
            action="invoke_agent",
            target_role="main_flow",
            skill_name="test-report",
            target_state="generating_report",
            reason="根据人工执行结果生成测试报告",
            expected_output_type="test_report",
        ),
        json.dumps(
            {
                "meta": {
                    **_meta("report-001", "test_report", project_id),
                    "status": "pending",
                    "source_artifacts": ["design-001", "execution-001"],
                },
                "scope": "修改收货地址",
                "environment": "SIT",
                "total_cases": 1,
                "passed": 1,
                "failed": 0,
                "blocked": 0,
                "skipped": 0,
                "defect_refs": [],
                "risk_summary": [],
                "conclusion": "核心功能验证通过。",
                "trace_refs": {"REQ-001": ["record-TC-001"]},
            },
            ensure_ascii=False,
        ),
    ]


def test_address_change_happy_path_reaches_report_approval(tmp_path):
    workspace = tmp_path / "workspace"
    projects = workspace / "projects"
    workspace.mkdir()
    requirement = tmp_path / "requirement.md"
    requirement.write_text("# 修改收货地址\n", encoding="utf-8")

    manager = ProjectManager(projects)
    manager.create_project("address-change", "修改收货地址", created_by="tester-001")
    manager.import_input(
        "address-change",
        requirement,
        InputCategory.REQUIREMENT,
        imported_by="tester-001",
        input_id="requirement-001",
    )
    store = ArtifactStore(projects)
    workflow = WorkflowRun.model_validate(store.load_workflow("address-change"))
    runner = QueueRunner(_agent_outputs("address-change"))
    orchestrator = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=store,
        project_root=store.project_root("address-change"),
    )

    for expected_state in (
        WorkflowState.REQUIREMENT_REVIEWING,
        WorkflowState.REQUIREMENT_ANALYZING,
        WorkflowState.TESTCASE_DESIGNING,
        WorkflowState.TESTCASE_REVIEWING,
        WorkflowState.WAITING_TESTCASE_APPROVAL,
    ):
        result = orchestrator.step()
        assert result.error is None
        assert workflow.current_state is expected_state

    approval = HumanApproval(
        meta=ArtifactMeta(
            artifact_id="approval-001",
            artifact_type="human_approval",
            project_id="address-change",
            status=ArtifactStatus.COMPLETED,
            created_by="tester-001",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        approval_type=ApprovalType.TESTCASE_APPROVAL,
        target_artifact_id="design-001",
        target_artifact_type="test_design",
        target_artifact_version=1,
        decision=ApprovalDecision.APPROVED,
        decided_by="tester-001",
        decided_at=datetime.now(timezone.utc),
    )
    HumanApprovalService(workflow, store).submit(approval)

    source = tmp_path / "passed.log"
    source.write_text("case passed\n", encoding="utf-8")
    evidence = EvidenceService(store).import_file(
        "address-change",
        source,
        "log",
        description="执行日志",
        evidence_id="execution-log-001",
    )
    execution = ExecutionBatch(
        meta=ArtifactMeta(
            artifact_id="execution-001",
            artifact_type="test_execution",
            project_id="address-change",
            status=ArtifactStatus.COMPLETED,
            created_by="tester-001",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        test_design_id="design-001",
        test_design_version=1,
        records=[
            ExecutionRecord(
                record_id="record-TC-001",
                case_id="TC-001",
                case_version=1,
                environment="SIT",
                executed_by="tester-001",
                executed_at=datetime.now(timezone.utc),
                result="passed",
                actual_result="地址修改成功",
                evidence=[evidence],
            )
        ],
    )
    ManualExecutionService(workflow, store).submit(execution)
    result = orchestrator.step()

    assert result.error is None
    assert result.markdown_path is not None
    assert workflow.current_state is WorkflowState.WAITING_REPORT_APPROVAL
