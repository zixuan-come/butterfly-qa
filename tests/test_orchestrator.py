import json
from datetime import datetime, timezone

import pytest

from qa_agent.agent_protocol import (
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    WorkflowAction,
)
from qa_agent.agent_runner import AgentRunner
from qa_agent.orchestrator import OrchestrationError, WorkflowOrchestrator
from qa_agent.storage.artifact_store import ArtifactStore
from qa_agent.workflow.models import (
    ArtifactPointer,
    InputFilePointer,
    WorkflowRun,
    WorkflowTransition,
)
from qa_agent.workflow.states import WorkflowState


def make_workflow() -> WorkflowRun:
    now = datetime.now(timezone.utc)
    return WorkflowRun(
        workflow_id="wf-001",
        project_id="demo-project",
        created_at=now,
        updated_at=now,
    )


class QueueRunner(AgentRunner):
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        output = next(self.outputs)
        now = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=output,
            started_at=now,
            completed_at=now,
        )


def action_payload(**overrides):
    payload = {
        "action": "invoke_agent",
        "target_role": "test_analysis_design",
        "skill_name": "requirement-review",
        "target_state": "requirement_reviewing",
        "reason": "执行需求评审",
        "expected_output_type": "requirement_review",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def review_payload(project_id="demo-project"):
    timestamp = datetime.now(timezone.utc).isoformat()
    return json.dumps(
        {
            "meta": {
                "artifact_id": "review-001",
                "artifact_type": "requirement_review",
                "project_id": project_id,
                "version": 1,
                "status": "completed",
                "source_artifacts": [],
                "created_by": "test-analysis-design-agent",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            "decision": "pass",
            "issues": [],
            "assumptions": [],
            "open_questions": [],
        },
        ensure_ascii=False,
    )


def test_completed_testcase_review_recovers_manual_state_to_owner_approval(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.MANUAL_INTERVENTION_REQUIRED
    workflow.manual_resume_state = WorkflowState.TESTCASE_REVIEWING
    workflow.active_artifacts["testcase_review"] = ArtifactPointer(
        artifact_id="case-review-001",
        artifact_type="testcase_review",
        version=1,
    )
    original = WorkflowAction(
        action="manual_intervention",
        target_state=WorkflowState.MANUAL_INTERVENTION_REQUIRED,
        reason="AI could not route the review result",
        human_question="请人工决定评审结果",
    )

    normalized = WorkflowOrchestrator(
        workflow,
        QueueRunner([]),
        artifact_store=ArtifactStore(tmp_path),
    )._normalize_testcase_review_action(original)

    assert normalized.action.value == "transition"
    assert normalized.target_state is WorkflowState.WAITING_TESTCASE_APPROVAL
    assert "测试负责人" in normalized.reason

def test_legacy_testcase_review_skill_name_is_normalized():
    action = WorkflowAction(
        action="invoke_agent",
        target_role=AgentRole.TESTCASE_REVIEW,
        skill_name="testcase-review",
        target_state=WorkflowState.TESTCASE_REVIEWING,
        reason="评审测试用例",
        expected_output_type="testcase_review",
    )

    normalized = WorkflowOrchestrator._normalize_skill_name(action)

    assert normalized.skill_name == "testcase-evaluation"

def test_orchestrator_invokes_specialist_and_persists_artifact(tmp_path):
    runner = QueueRunner([action_payload(), review_payload()])
    workflow = make_workflow()
    workflow.input_files.append(
        InputFilePointer(
            input_id="requirement-001",
            category="requirement",
            relative_path="input/requirement-001.md",
            sha256="a" * 64,
        )
    )
    store = ArtifactStore(tmp_path / "projects")
    orchestrator = WorkflowOrchestrator(workflow, runner, artifact_store=store)

    result = orchestrator.step()

    assert result.error is None
    assert workflow.current_state is WorkflowState.REQUIREMENT_REVIEWING
    assert result.artifact_path is not None
    assert result.artifact_path.is_file()
    assert workflow.active_artifacts["requirement_review"].artifact_id == "review-001"
    assert [request.role for request in runner.requests] == [
        AgentRole.MAIN_FLOW,
        AgentRole.TEST_ANALYSIS_DESIGN,
    ]
    assert runner.requests[0].input_files == []
    assert runner.requests[1].input_files == workflow.input_files
    assert store.load_workflow("demo-project")["current_state"] == "requirement_reviewing"


def test_wait_human_route_transitions_to_waiting_state(tmp_path):
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "wait_human",
                    "target_state": "waiting_product_revision",
                    "reason": "存在待产品确认项",
                    "human_question": "请产品确认评审清单",
                },
                ensure_ascii=False,
            )
        ]
    )
    workflow = make_workflow()
    workflow.current_state = WorkflowState.REQUIREMENT_REVIEWING

    result = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=ArtifactStore(tmp_path),
    ).step()

    assert result.error is None
    assert workflow.current_state is WorkflowState.WAITING_PRODUCT_REVISION
    assert result.transition is not None

def test_orchestrator_rejects_invalid_main_action_without_mutating_state(tmp_path):
    runner = QueueRunner([json.dumps({"action": "transition", "reason": "跨阶段"})])
    workflow = make_workflow()
    orchestrator = WorkflowOrchestrator(workflow, runner, artifact_store=ArtifactStore(tmp_path))

    result = orchestrator.step()

    assert result.action is None
    assert result.error is not None
    assert workflow.current_state is WorkflowState.REQUIREMENT_RECEIVED
    assert workflow.transition_history == []


def test_requirement_review_can_be_returned_for_product_revision(tmp_path):
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "transition",
                    "target_state": "waiting_product_revision",
                    "reason": "需求缺少已发货订单的处理规则",
                },
                ensure_ascii=False,
            )
        ]
    )
    workflow = make_workflow()
    workflow.current_state = WorkflowState.REQUIREMENT_REVIEWING
    orchestrator = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=ArtifactStore(tmp_path),
    )

    result = orchestrator.step()

    assert result.error is None
    assert workflow.current_state is WorkflowState.WAITING_PRODUCT_REVISION
    assert workflow.requirement_revision_rounds == 1
    assert workflow.transition_history[-1].reason == "需求缺少已发货订单的处理规则"


def test_orchestrator_rejects_invalid_artifact_and_keeps_processing_state(tmp_path):
    runner = QueueRunner([action_payload(), "{\"decision\": \"pass\"}"])
    workflow = make_workflow()
    orchestrator = WorkflowOrchestrator(workflow, runner, artifact_store=ArtifactStore(tmp_path))

    result = orchestrator.step()

    assert result.error is not None
    assert "specialist output rejected" in result.error
    assert workflow.current_state is WorkflowState.REQUIREMENT_REVIEWING
    assert workflow.active_artifacts == {}


def test_orchestrator_rejects_unknown_input_artifact_without_transition(tmp_path):
    action = action_payload(input_artifact_refs=["missing-artifact"])
    runner = QueueRunner([action])
    workflow = make_workflow()
    orchestrator = WorkflowOrchestrator(workflow, runner, artifact_store=ArtifactStore(tmp_path))

    result = orchestrator.step()

    assert result.error is not None
    assert "unknown input artifact reference" in result.error
    assert workflow.current_state is WorkflowState.REQUIREMENT_RECEIVED
    assert len(runner.requests) == 1


def test_requirement_analysis_uses_current_requirement_and_latest_review(tmp_path):
    workflow = make_workflow()
    workflow.input_files = [
        InputFilePointer(
            input_id="requirement-v1",
            category="requirement",
            relative_path="input/requirement-v1.md",
            sha256="a" * 64,
        ),
        InputFilePointer(
            input_id="requirement-v2",
            category="requirement",
            relative_path="input/requirement-v2.md",
            sha256="b" * 64,
        ),
        InputFilePointer(
            input_id="checkout-design",
            category="design",
            relative_path="input/checkout-design.png",
            sha256="c" * 64,
        ),
    ]
    workflow.current_requirement_input_id = "requirement-v2"
    review = ArtifactPointer(
        artifact_id="review-v2",
        artifact_type="requirement_review",
        version=2,
    )
    checklist = ArtifactPointer(
        artifact_id="checklist-v1",
        artifact_type="product_confirmation_checklist",
        version=1,
    )
    workflow.active_artifacts = {
        "requirement_review": review,
        "product_confirmation_checklist": checklist,
    }
    action = WorkflowAction(
        action="invoke_agent",
        target_role=AgentRole.TEST_ANALYSIS_DESIGN,
        skill_name="requirement-analysis",
        target_state=WorkflowState.REQUIREMENT_ANALYZING,
        reason="analyze the requirement",
        input_artifact_refs=["product_confirmation_checklist"],
        expected_output_type="requirement_analysis",
    )

    request = WorkflowOrchestrator(
        workflow,
        QueueRunner([]),
        artifact_store=ArtifactStore(tmp_path),
    )._specialist_request(action)

    assert [item.input_id for item in request.input_files] == [
        "requirement-v2",
        "checkout-design",
    ]
    assert "requirement-v1" not in [item.input_id for item in request.input_files]
    assert request.input_artifacts == [checklist, review]
    assert "current effective requirement document" in request.prompt
    assert "latest requirement_review artifact" in request.prompt


def test_old_workflow_falls_back_to_last_requirement_input(tmp_path):
    workflow = make_workflow()
    workflow.input_files = [
        InputFilePointer(
            input_id="requirement-v1",
            category="requirement",
            relative_path="input/requirement-v1.md",
            sha256="a" * 64,
        ),
        InputFilePointer(
            input_id="requirement-v2",
            category="requirement",
            relative_path="input/requirement-v2.md",
            sha256="b" * 64,
        ),
    ]

    request = WorkflowOrchestrator(
        workflow,
        QueueRunner([]),
        artifact_store=ArtifactStore(tmp_path),
    )._specialist_request(
        WorkflowAction(
            action="invoke_agent",
            target_role=AgentRole.TEST_ANALYSIS_DESIGN,
            skill_name="requirement-review",
            target_state=WorkflowState.REQUIREMENT_REVIEWING,
            reason="review the requirement",
            expected_output_type="requirement_review",
        )
    )

    assert [item.input_id for item in request.input_files] == ["requirement-v2"]


def test_requirement_analysis_requires_active_review(tmp_path):
    workflow = make_workflow()
    workflow.input_files = [
        InputFilePointer(
            input_id="requirement-v1",
            category="requirement",
            relative_path="input/requirement-v1.md",
            sha256="a" * 64,
        )
    ]
    action = WorkflowAction(
        action="invoke_agent",
        target_role=AgentRole.TEST_ANALYSIS_DESIGN,
        skill_name="requirement-analysis",
        target_state=WorkflowState.REQUIREMENT_ANALYZING,
        reason="analyze the requirement",
        expected_output_type="requirement_analysis",
    )

    with pytest.raises(OrchestrationError, match="requirement_review is required"):
        WorkflowOrchestrator(
            workflow,
            QueueRunner([]),
            artifact_store=ArtifactStore(tmp_path),
        )._specialist_request(action)


def test_human_risk_acceptance_prevents_analysis_from_falling_back(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.REQUIREMENT_ANALYZING
    review = ArtifactPointer(
        artifact_id="review-001",
        artifact_type="requirement_review",
        version=1,
    )
    approval = ArtifactPointer(
        artifact_id="approval-001",
        artifact_type="human_approval",
        version=1,
    )
    workflow.active_artifacts["requirement_review"] = review
    workflow.transition_history.append(
        WorkflowTransition(
            from_state=WorkflowState.WAITING_PRODUCT_REVISION,
            to_state=WorkflowState.REQUIREMENT_ANALYZING,
            triggered_by="admin",
            reason="risk accepted for controlled test",
            occurred_at=datetime.now(timezone.utc),
            related_artifacts=[review, approval],
        )
    )
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "transition",
                    "target_state": "waiting_product_revision",
                    "reason": "historical review risks remain",
                }
            ),
            json.dumps(
                {
                    "meta": {
                        "artifact_id": "analysis-001",
                        "artifact_type": "requirement_analysis",
                        "project_id": "demo-project",
                        "version": 1,
                        "status": "completed",
                        "source_artifacts": ["review-001"],
                        "created_by": "test-analysis-design-agent",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "requirements": [
                        {
                            "requirement_id": "REQ-001",
                            "title": "Requirement",
                            "description": "Requirement description",
                            "actors": [],
                            "business_rules": [],
                            "preconditions": [],
                            "postconditions": [],
                            "open_questions": ["Risk accepted for test"],
                        }
                    ],
                    "flows": [],
                    "states": [],
                    "permissions": [],
                    "assumptions": ["Human accepted previous review risks"],
                }
            ),
        ]
    )

    result = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=ArtifactStore(tmp_path),
    ).step()

    assert result.error is None
    assert workflow.current_state is WorkflowState.TESTCASE_DESIGNING
    assert result.action is not None
    assert result.action.skill_name == "requirement-analysis"
    assert result.action.target_state is WorkflowState.TESTCASE_DESIGNING
    assert len(runner.requests) == 2
    assert runner.requests[1].skill_name == "requirement-analysis"
@pytest.mark.parametrize(
    "state",
    [WorkflowState.WAITING_PRODUCT_REVISION, WorkflowState.MANUAL_INTERVENTION_REQUIRED],
)
def test_accepted_requirement_risk_recovers_from_blocked_states(tmp_path, state):
    workflow = make_workflow()
    workflow.current_state = state
    review = ArtifactPointer(
        artifact_id="review-001",
        artifact_type="requirement_review",
        version=1,
    )
    workflow.active_artifacts["requirement_review"] = review
    workflow.accepted_requirement_review = review
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "transition",
                    "target_state": "requirement_reviewing",
                    "reason": "模型误判为需要重新评审",
                }
            )
        ]
    )

    result = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=ArtifactStore(tmp_path),
    ).step()

    assert result.error is None
    assert result.action is not None
    assert result.action.action.value == "transition"
    assert result.action.target_state is WorkflowState.REQUIREMENT_ANALYZING
    assert workflow.current_state is WorkflowState.REQUIREMENT_ANALYZING
