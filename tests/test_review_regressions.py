"""Regression coverage for approval, source versions and revision handoffs."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient

from qa_agent.agent_protocol import AgentRole, WorkflowAction
from qa_agent.agent_runner import StubAgentRunner
from qa_agent.human_actions import HumanApprovalService
from qa_agent.orchestrator import WorkflowOrchestrator
from qa_agent.project import InputCategory, ProjectManager
from qa_agent.schemas import ApprovalDecision, ApprovalType, ArtifactMeta, ArtifactStatus, HumanApproval, TestDesign as DesignModel
from qa_agent.storage import ArtifactStore
from qa_agent.web import create_app
from qa_agent.workflow.models import ArtifactPointer, InputFilePointer, WorkflowRun, WorkflowTransition
from qa_agent.workflow.states import WorkflowState

from test_human_actions import make_approval, make_case_review, make_design
from test_orchestrator import QueueRunner, action_payload, make_workflow, review_payload
from test_reporting import make_report
from test_web_api import _create_project, _download_template, _fill_execution_template, _seed_test_design


@pytest.mark.parametrize("action", ["transition", "wait_human", "invoke_agent"])
@pytest.mark.parametrize("current,target", [
    (WorkflowState.WAITING_REPORT_APPROVAL, WorkflowState.COMPLETED),
    (WorkflowState.WAITING_TESTCASE_APPROVAL, WorkflowState.WAITING_MANUAL_EXECUTION),
    (WorkflowState.GENERATING_REPORT, WorkflowState.WAITING_REPORT_APPROVAL),
])
def test_ai_cannot_cross_human_or_report_service_gates(tmp_path, action, current, target):
    workflow = make_workflow()
    workflow.current_state = current
    payload = {"action": action, "target_state": target.value, "reason": "Assume approval"}
    if action == "invoke_agent":
        payload.update(target_role="test_analysis_design", skill_name="testcase-design", expected_output_type="test_design")
    if action == "wait_human":
        payload["human_question"] = "Confirm?"
    runner = QueueRunner([json.dumps(payload)])
    store = ArtifactStore(tmp_path)

    result = WorkflowOrchestrator(workflow, runner, artifact_store=store).step()

    assert result.error is not None
    assert workflow.current_state is current
    assert workflow.transition_history == []
    assert len(runner.requests) == 1
    assert list(tmp_path.glob("**/decisions/*.json")) == []


@pytest.mark.parametrize("resume", [
    WorkflowState.TESTCASE_DESIGNING, WorkflowState.TESTCASE_REVIEWING,
    WorkflowState.GENERATING_REPORT,
])
def test_accepted_requirement_risk_does_not_rewind_later_stages(tmp_path, resume):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.MANUAL_INTERVENTION_REQUIRED
    workflow.manual_resume_state = resume
    review = ArtifactPointer(artifact_id="review-001", artifact_type="requirement_review", version=1)
    workflow.active_artifacts["requirement_review"] = review
    workflow.accepted_requirement_review = review
    runner = QueueRunner([json.dumps({"action": "transition", "target_state": resume.value, "reason": "Resume interrupted stage"})])

    result = WorkflowOrchestrator(workflow, runner, artifact_store=ArtifactStore(tmp_path)).step()

    assert result.error is None
    assert workflow.current_state is resume
    assert workflow.manual_resume_state is None


def test_requirement_revision_invalidates_outputs_and_old_risk_acceptance(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo", "Demo", created_by="admin")
    source = tmp_path / "requirement.md"
    source.write_text("v1", encoding="utf-8")
    manager.import_requirement_version("demo", source, imported_by="admin", input_id="req-v1")
    workflow = manager.load_workflow("demo")
    review = ArtifactPointer(artifact_id="review-001", artifact_type="requirement_review", version=1)
    workflow.active_artifacts["requirement_review"] = review
    workflow.active_artifacts["test_design"] = ArtifactPointer(artifact_id="design-001", artifact_type="test_design", version=1)
    workflow.accepted_requirement_review = review
    workflow.accepted_requirement_input_id = "req-v1"
    workflow.requirement_risk_acceptance_invalidated = False
    workflow.current_state = WorkflowState.WAITING_MANUAL_EXECUTION
    workflow.transition_history.append(WorkflowTransition(
        from_state=WorkflowState.WAITING_PRODUCT_REVISION,
        to_state=WorkflowState.REQUIREMENT_ANALYZING,
        triggered_by="admin", reason="Accept risk", occurred_at=datetime.now(timezone.utc),
        related_artifacts=[review, ArtifactPointer(artifact_id="approved-001", artifact_type="human_approval", version=1)],
    ))
    manager.store.save_workflow("demo", workflow)
    source.write_text("v2 changed behavior", encoding="utf-8")

    manager.import_requirement_version("demo", source, imported_by="admin", input_id="req-v2")
    updated = manager.load_workflow("demo")

    assert updated.current_state is WorkflowState.REQUIREMENT_RECEIVED
    assert updated.current_requirement_input_id == "req-v2"
    assert updated.active_artifacts == {}
    assert updated.accepted_requirement_review is None
    assert updated.accepted_requirement_input_id is None
    assert updated.transition_history[-1].from_state is WorkflowState.WAITING_MANUAL_EXECUTION
    assert review in updated.transition_history[-1].related_artifacts
    updated.active_artifacts["requirement_review"] = review
    assert not WorkflowOrchestrator(updated, QueueRunner([]))._has_accepted_current_review_risk()


def test_legacy_unbound_acceptance_cannot_authorize_multiple_requirement_versions():
    workflow = make_workflow()
    review = ArtifactPointer(artifact_id="review-001", artifact_type="requirement_review", version=1)
    workflow.active_artifacts["requirement_review"] = review
    workflow.accepted_requirement_review = review
    workflow.input_files = [InputFilePointer(input_id=f"req-{v}", category="requirement", relative_path=f"input/req-{v}.md", sha256=str(v) * 64) for v in (1, 2)]
    workflow.current_requirement_input_id = "req-2"

    assert not WorkflowOrchestrator(workflow, QueueRunner([]))._has_accepted_current_review_risk()


def test_new_design_is_reviewed_again_after_human_revision(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.WAITING_TESTCASE_APPROVAL
    store = ArtifactStore(tmp_path)
    design, review = make_design(), make_case_review()
    store.save_artifact(design)
    store.save_artifact(review)
    for artifact in (design, review):
        workflow.active_artifacts[artifact.meta.artifact_type] = ArtifactPointer(
            artifact_id=artifact.meta.artifact_id, artifact_type=artifact.meta.artifact_type, version=artifact.meta.version)
    workflow.testcase_review_design = workflow.active_artifacts["test_design"]
    HumanApprovalService(workflow, store).submit(make_approval(ApprovalDecision.CHANGES_REQUESTED, "Add permission cases"))
    assert "testcase_review" not in workflow.active_artifacts
    assert workflow.testcase_review_design is None

    revised = design.model_copy(deep=True)
    revised.meta.version = 2
    new_review = review.model_copy(deep=True)
    new_review.meta.version = 2
    new_review.meta.source_artifacts = ["design-001:v2"]
    runner = QueueRunner([
        action_payload(skill_name="testcase-design", target_state="testcase_designing", expected_output_type="test_design"),
        revised.model_dump_json(),
        json.dumps({"action": "invoke_agent", "target_role": "testcase_review", "skill_name": "testcase-evaluation", "target_state": "testcase_reviewing", "reason": "Review revised design", "expected_output_type": "testcase_review"}),
        new_review.model_dump_json(),
    ])
    harness = WorkflowOrchestrator(workflow, runner, artifact_store=store)

    assert harness.step().error is None
    assert "testcase_review" not in workflow.active_artifacts
    assert harness.step().error is None
    assert runner.requests[-1].role is AgentRole.TESTCASE_REVIEW
    assert workflow.active_artifacts["testcase_review"].version == 2
    assert workflow.testcase_review_design.version == 2
    assert "Add permission cases" in runner.requests[1].prompt


def test_legacy_review_for_v1_cannot_skip_v2_review(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.TESTCASE_REVIEWING
    store = ArtifactStore(tmp_path)
    review = make_case_review()
    store.save_artifact(review)
    workflow.active_artifacts = {
        "test_design": ArtifactPointer(artifact_id="design-001", artifact_type="test_design", version=2),
        "testcase_review": ArtifactPointer(artifact_id="case-review-001", artifact_type="testcase_review", version=1),
    }
    harness = WorkflowOrchestrator(workflow, QueueRunner([]), artifact_store=store)
    action = WorkflowAction(action="transition", target_state="waiting_testcase_approval", reason="Reuse old review")

    assert harness._normalize_testcase_review_action(action) is action
    with pytest.raises(ValueError, match="review of the active test design"):
        harness._validate_action_target(action)


def test_report_rejection_feedback_reaches_both_agents(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.WAITING_REPORT_APPROVAL
    store = ArtifactStore(tmp_path)
    report = make_report()
    store.save_artifact(report)
    workflow.active_artifacts["test_report"] = ArtifactPointer(artifact_id="report-001", artifact_type="test_report", version=1)
    now = datetime.now(timezone.utc)
    approval = HumanApproval(
        meta=ArtifactMeta(artifact_id="feedback-001", artifact_type="human_approval", project_id=workflow.project_id,
                          status=ArtifactStatus.COMPLETED, created_by="owner", created_at=now, updated_at=now),
        approval_type=ApprovalType.REPORT_APPROVAL, target_artifact_id="report-001", target_artifact_type="test_report",
        target_artifact_version=1, decision=ApprovalDecision.CHANGES_REQUESTED,
        decided_by="owner", decided_at=now, comment="Explain BUG-001 release impact explicitly",
    )
    HumanApprovalService(workflow, store).submit(approval)
    harness = WorkflowOrchestrator(workflow, QueueRunner([]), artifact_store=store)
    action = WorkflowAction(action="invoke_agent", target_role="main_flow", skill_name="test-report",
                            expected_output_type="test_report", target_state="generating_report", reason="Revise report")

    main = harness._main_request()
    specialist = harness._specialist_request(action)

    assert approval.comment in main.prompt
    assert approval.comment in specialist.prompt
    assert workflow.active_artifacts["test_report"] in specialist.input_artifacts
    assert store.load_workflow(workflow.project_id)["revision_feedback"] == {"test_report": "feedback-001"}


def test_requirement_upload_serializes_with_background_run(tmp_path, monkeypatch):
    from qa_agent.web import app as web_app

    started, release, upload_waiting = Event(), Event(), Event()
    original_lock = web_app._project_lock

    def observed_lock(request, project_id, module_id=None):
        lock = original_lock(request, project_id, module_id)
        if started.is_set() and request.url.path.endswith("/inputs"):
            assert lock.locked()
            upload_waiting.set()
        return lock

    monkeypatch.setattr(web_app, "_project_lock", observed_lock)

    def respond(request):
        if request.role is AgentRole.MAIN_FLOW:
            started.set()
            assert release.wait(10)
            return action_payload()
        return review_payload("demo")

    app = create_app(tmp_path, runner_factory=lambda *args: StubAgentRunner(respond))
    with TestClient(app) as client:
        _create_project(client)
        first = client.post("/api/v1/projects/demo/inputs", data={"category": "requirement", "imported_by": "admin", "input_id": "req-v1"},
                            files={"file": ("requirement.md", b"v1", "text/markdown")})
        assert first.status_code == 201
        task = client.post("/api/v1/projects/demo/runs?async_run=true", json={})
        assert task.status_code == 200
        assert started.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            revised = pool.submit(client.post, "/api/v1/projects/demo/inputs",
                                  data={"category": "requirement", "imported_by": "admin", "input_id": "req-v2"},
                                  files={"file": ("requirement.md", b"v2", "text/markdown")})
            try:
                assert upload_waiting.wait(5)
                assert not revised.done()
                release.set()
                result = revised.result(timeout=10)
                assert result.status_code == 201
                state = client.get("/api/v1/projects/demo/workflow").json()["data"]
                assert state["current_requirement_input_id"] == "req-v2"
                assert state["state"] == "requirement_received"
                assert state["active_artifacts"] == {}
                assert [item["input_id"] for item in state["input_files"]] == ["req-v1", "req-v2"]
            finally:
                release.set()


def test_uploading_revised_requirement_rejects_previous_execution_template(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        _create_project(client)
        client.post("/api/v1/projects/demo/inputs", data={"category": "requirement", "imported_by": "admin"},
                    files={"file": ("requirement.md", b"v1", "text/markdown")})
        _seed_test_design(tmp_path, WorkflowState.WAITING_MANUAL_EXECUTION)
        old = _fill_execution_template(_download_template(client), {"TC-001": "通过"})
        changed = client.post("/api/v1/projects/demo/inputs", data={"category": "requirement", "imported_by": "admin"},
                              files={"file": ("requirement.md", b"v2", "text/markdown")})
        response = client.post("/api/v1/projects/demo/executions/upload", data={"submitted_by": "admin"},
                               files={"file": ("old-result.xlsx", old, "application/octet-stream")})

        assert changed.status_code == 201
        assert response.status_code == 404
        state = client.get("/api/v1/projects/demo/workflow").json()["data"]
        assert state["state"] == "requirement_received"
        assert "test_execution" not in state["active_artifacts"]


@pytest.mark.parametrize("source_module,target_project,target_module", [
    (None, "other", None),
    (None, "demo", "login"),
    ("login", "demo", None),
    ("login", "demo", "register"),
])
def test_api_rejects_execution_excel_from_another_context(
    tmp_path, source_module, target_project, target_module,
):
    with TestClient(create_app(tmp_path)) as client:
        _create_project(client)
        if target_project != "demo":
            _create_project(client, target_project)
        for module_id in {source_module, target_module} - {None}:
            created = client.post("/api/v1/projects/demo/modules", json={
                "module_id": module_id, "name": module_id, "created_by": "admin",
            })
            assert created.status_code == 201
        source = _seed_test_design(
            tmp_path, WorkflowState.WAITING_MANUAL_EXECUTION, module_id=source_module,
        )
        design = DesignModel.model_validate(source.load_artifact("demo", "test_design", "design-001", 1))
        design.meta.project_id = target_project
        target = ArtifactStore(tmp_path / "projects", module_id=target_module)
        target.save_artifact(design)
        workflow = WorkflowRun.model_validate(target.load_workflow(target_project))
        workflow.current_state = WorkflowState.WAITING_MANUAL_EXECUTION
        workflow.active_artifacts["test_design"] = ArtifactPointer(
            artifact_id="design-001", artifact_type="test_design", version=1,
        )
        target.save_workflow(target_project, workflow)
        downloaded = client.get("/api/v1/projects/demo/artifacts/test_design/download",
                                params={"format": "xlsx", **({"module_id": source_module} if source_module else {})})
        assert downloaded.status_code == 200
        wrong = _fill_execution_template(downloaded.content, {"TC-001": "通过"})
        params = {"module_id": target_module} if target_module else {}
        rejected = client.post(f"/api/v1/projects/{target_project}/executions/upload", params=params,
                               data={"submitted_by": "admin"},
                               files={"file": ("wrong.xlsx", wrong, "application/octet-stream")})

        assert rejected.status_code == 422
        assert rejected.json()["code"] == "EXECUTION_XLSX_REJECTED"
        current = WorkflowRun.model_validate(target.load_workflow(target_project))
        assert current.current_state is WorkflowState.WAITING_MANUAL_EXECUTION
        assert "test_execution" not in current.active_artifacts
        correct = client.get(f"/api/v1/projects/{target_project}/artifacts/test_design/download",
                             params={"format": "xlsx", **params})
        assert correct.status_code == 200
        accepted = client.post(f"/api/v1/projects/{target_project}/executions/upload", params=params,
                               data={"submitted_by": "admin"},
                               files={"file": ("correct.xlsx", _fill_execution_template(correct.content, {"TC-001": "通过"}),
                                                "application/octet-stream")})
        assert accepted.status_code == 201


def test_unversioned_review_cannot_authorize_a_revised_design(tmp_path):
    workflow = make_workflow()
    workflow.current_state = WorkflowState.TESTCASE_REVIEWING
    workflow.active_artifacts["test_design"] = ArtifactPointer(
        artifact_id="design-001", artifact_type="test_design", version=2,
    )
    review = make_case_review()
    review.meta.source_artifacts = ["design-001"]
    harness = WorkflowOrchestrator(workflow, QueueRunner([]), artifact_store=ArtifactStore(tmp_path))

    with pytest.raises(ValueError, match="active test design"):
        harness._accept_artifact("testcase_review", review.model_dump_json())

    assert "testcase_review" not in workflow.active_artifacts
