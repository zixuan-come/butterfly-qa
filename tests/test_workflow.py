from datetime import datetime, timezone

import pytest

from qa_agent.workflow.models import ArtifactPointer, WorkflowRun
from qa_agent.workflow.state_machine import WorkflowStateMachine
from qa_agent.workflow.states import WorkflowState
from qa_agent.workflow.transitions import InvalidTransitionError


def make_workflow() -> WorkflowRun:
    now = datetime.now(timezone.utc)
    return WorkflowRun(
        workflow_id="wf-001",
        project_id="demo-project",
        created_at=now,
        updated_at=now,
    )


def test_valid_transition_updates_state_and_history() -> None:
    machine = WorkflowStateMachine(make_workflow())
    review = ArtifactPointer(
        artifact_id="review-001",
        artifact_type="requirement_review",
        version=1,
    )

    record = machine.transition(
        WorkflowState.REQUIREMENT_REVIEWING,
        triggered_by="main-agent",
        reason="开始需求评审",
        related_artifacts=[review],
    )

    assert machine.current_state is WorkflowState.REQUIREMENT_REVIEWING
    assert record.from_state is WorkflowState.REQUIREMENT_RECEIVED
    assert record.related_artifacts == [review]
    assert machine.workflow.transition_history == [record]


def test_invalid_transition_does_not_mutate_workflow() -> None:
    workflow = make_workflow()
    machine = WorkflowStateMachine(workflow)

    with pytest.raises(InvalidTransitionError):
        machine.transition(
            WorkflowState.TESTCASE_DESIGNING,
            triggered_by="main-agent",
            reason="非法跨阶段跳转",
        )

    assert workflow.current_state is WorkflowState.REQUIREMENT_RECEIVED
    assert workflow.transition_history == []


def test_revision_states_increment_their_counters() -> None:
    workflow = make_workflow()
    workflow.current_state = WorkflowState.REQUIREMENT_REVIEWING
    machine = WorkflowStateMachine(workflow)

    machine.transition(
        WorkflowState.WAITING_PRODUCT_REVISION,
        triggered_by="requirement-review-skill",
        reason="需求存在阻塞问题",
    )

    assert workflow.requirement_revision_rounds == 1


def test_manual_intervention_remembers_and_clears_resume_state() -> None:
    workflow = make_workflow()
    workflow.current_state = WorkflowState.TESTCASE_REVIEWING
    machine = WorkflowStateMachine(workflow)

    machine.transition(
        WorkflowState.MANUAL_INTERVENTION_REQUIRED,
        triggered_by="main-agent",
        reason="用例评审输出无法通过校验",
    )
    assert workflow.manual_resume_state is WorkflowState.TESTCASE_REVIEWING

    machine.transition(
        WorkflowState.TESTCASE_REVIEWING,
        triggered_by="tester",
        reason="人工修复评审输入后恢复",
    )
    assert workflow.manual_resume_state is None


def test_completed_workflow_is_terminal() -> None:
    workflow = make_workflow()
    workflow.current_state = WorkflowState.COMPLETED

    assert WorkflowStateMachine(workflow).is_terminal is True
