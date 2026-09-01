import json
from datetime import datetime, timezone

from qa_agent.agent_protocol import (
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
)
from qa_agent.agent_runner import AgentRunner
from qa_agent.orchestrator import WorkflowOrchestrator
from qa_agent.storage.artifact_store import ArtifactStore
from qa_agent.workflow.models import InputFilePointer, WorkflowRun
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
