import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from qa_agent.agent_protocol import AgentRequest, AgentResponse, AgentStatus
from qa_agent.agent_runner import AgentRunner
from qa_agent.project import ProjectManager
from qa_agent.schemas import (
    ArtifactMeta,
    ArtifactStatus,
    RequirementReview as ReviewModel,
    RequirementReviewIssue as ReviewIssueModel,
    ReviewDecision,
    TestCase as CaseModel,
    TestDesign as DesignModel,
    TestPoint as PointModel,
    TestReport as ReportModel,
    TestStep as StepModel,
)
from qa_agent.storage import ArtifactStore
from qa_agent.web import create_app
from qa_agent.workflow.models import ArtifactPointer, WorkflowRun
from qa_agent.workflow.states import WorkflowState


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_health_uses_the_standard_response_envelope(tmp_path):
    with _client(tmp_path) as client:
        response = client.get(
            "/api/v1/health",
            headers={"X-Request-ID": "request-health-001"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-health-001"
    assert response.json() == {
        "code": "OK",
        "message": "请求成功",
        "data": {
            "status": "healthy",
            "service": "butterfly-qa-api",
            "version": "0.1.0",
        },
        "request_id": "request-health-001",
    }


def test_project_create_list_and_detail_share_domain_state(tmp_path):
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/projects",
            json={
                "project_id": "address-change",
                "name": "修改收货地址",
                "created_by": "tester-001",
            },
        )
        listed = client.get("/api/v1/projects")
        detail = client.get("/api/v1/projects/address-change")

    assert created.status_code == 201
    assert created.json()["code"] == "OK"
    assert created.json()["data"]["state"] == "requirement_received"
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["project_id"] == "address-change"
    assert detail.status_code == 200
    assert detail.json()["data"]["workflow_id"].startswith("wf-")
    assert detail.json()["data"]["revision_rounds"] == {
        "requirement": 0,
        "testcase": 0,
        "report": 0,
    }


def test_duplicate_project_returns_conflict_with_business_code(tmp_path):
    payload = {
        "project_id": "demo",
        "name": "演示项目",
        "created_by": "tester-001",
    }
    with _client(tmp_path) as client:
        assert client.post("/api/v1/projects", json=payload).status_code == 201
        response = client.post("/api/v1/projects", json=payload)

    assert response.status_code == 409
    assert response.json()["code"] == "PROJECT_ALREADY_EXISTS"
    assert response.json()["message"] == "项目已存在"
    assert response.json()["data"] is None
    assert response.json()["request_id"]


def test_missing_project_returns_not_found(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/v1/projects/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "PROJECT_NOT_FOUND"
    assert response.json()["message"] == "项目不存在"
    assert response.json()["data"] is None


def test_validation_error_keeps_the_standard_response_envelope(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/projects",
            json={
                "project_id": "invalid project id",
                "name": "",
                "created_by": "tester-001",
                "unexpected": True,
            },
        )

    payload = response.json()
    assert response.status_code == 422
    assert payload["code"] == "INVALID_REQUEST"
    assert payload["message"] == "请求参数校验失败"
    assert len(payload["data"]["errors"]) == 3
    assert payload["request_id"]


def test_input_upload_is_persisted_and_visible_in_workflow_status(tmp_path):
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/projects",
            json={
                "project_id": "demo",
                "name": "演示项目",
                "created_by": "tester-001",
            },
        )
        uploaded = client.post(
            "/api/v1/projects/demo/inputs",
            data={
                "category": "requirement",
                "imported_by": "tester-001",
                "input_id": "requirement-001",
            },
            files={"file": ("requirement.md", "# 修改收货地址", "text/markdown")},
        )
        workflow = client.get("/api/v1/projects/demo/workflow")

    assert uploaded.status_code == 201
    assert uploaded.json()["data"]["original_name"] == "requirement.md"
    assert uploaded.json()["data"]["input_id"] == "requirement-001"
    assert workflow.status_code == 200
    assert workflow.json()["data"]["state"] == "requirement_received"
    assert workflow.json()["data"]["input_files"][0]["input_id"] == "requirement-001"


def test_input_upload_rejects_oversized_file_and_removes_temporary_file(tmp_path):
    app = create_app(tmp_path, max_upload_bytes=4)
    with TestClient(app) as client:
        client.post(
            "/api/v1/projects",
            json={
                "project_id": "demo",
                "name": "演示项目",
                "created_by": "tester-001",
            },
        )
        response = client.post(
            "/api/v1/projects/demo/inputs",
            data={"category": "requirement", "imported_by": "tester-001"},
            files={"file": ("requirement.md", "12345", "text/markdown")},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"
    upload_dir = tmp_path / ".butterfly-qa" / "uploads"
    assert list(upload_dir.iterdir()) == []


def test_text_input_preview_uses_envelope_and_respects_size_limit(tmp_path):
    app = create_app(tmp_path, max_text_preview_bytes=4)
    with TestClient(app) as client:
        _create_project(client)
        uploaded = client.post(
            "/api/v1/projects/demo/inputs",
            data={
                "category": "requirement",
                "imported_by": "tester-001",
                "input_id": "requirement-text",
            },
            files={"file": ("requirement.md", "abcdef", "text/markdown")},
        )
        response = client.get(
            "/api/v1/projects/demo/inputs/requirement-text"
        )

    assert uploaded.status_code == 201
    assert response.status_code == 200
    assert response.json()["data"]["preview_kind"] == "text"
    assert response.json()["data"]["content"] == "abcd"
    assert response.json()["data"]["truncated"] is True
    assert response.json()["data"]["input"]["original_name"] == "requirement.md"


def test_binary_input_preview_exposes_safe_inline_content(tmp_path):
    content = b"not-a-real-png"
    with _client(tmp_path) as client:
        _create_project(client)
        client.post(
            "/api/v1/projects/demo/inputs",
            data={
                "category": "requirement",
                "imported_by": "tester-001",
                "input_id": "requirement-image",
            },
            files={"file": ("requirement.png", content, "image/png")},
        )
        preview = client.get(
            "/api/v1/projects/demo/inputs/requirement-image"
        )
        raw = client.get(
            "/api/v1/projects/demo/inputs/requirement-image/content"
        )

    assert preview.status_code == 200
    assert preview.json()["data"]["preview_kind"] == "image"
    assert preview.json()["data"]["content"] is None
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"
    assert raw.headers["content-disposition"] == "inline"
    assert raw.content == content


def test_missing_input_preview_returns_not_found(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        response = client.get("/api/v1/projects/demo/inputs/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "INPUT_NOT_FOUND"


class QueueRunner(AgentRunner):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = iter(outputs)
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        now = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=next(self.outputs),
            started_at=now,
            completed_at=now,
        )


def test_run_endpoint_executes_one_bounded_orchestration_step(tmp_path):
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "wait_human",
                    "reason": "尚未导入需求",
                    "human_question": "请先上传产品需求文档",
                },
                ensure_ascii=False,
            )
        ]
    )
    app = create_app(
        tmp_path,
        runner_factory=lambda _workspace, _store: runner,
    )
    with TestClient(app) as client:
        client.post(
            "/api/v1/projects",
            json={
                "project_id": "demo",
                "name": "演示项目",
                "created_by": "tester-001",
            },
        )
        response = client.post("/api/v1/projects/demo/runs", json={})

    assert response.status_code == 200
    assert response.json()["code"] == "OK"
    assert response.json()["data"]["state"] == "requirement_received"
    assert response.json()["data"]["action"]["action"] == "wait_human"
    assert response.json()["data"]["agent"]["status"] == "succeeded"
    assert len(runner.requests) == 1


def test_async_run_returns_task_and_persists_latest_status(tmp_path):
    runner = QueueRunner(
        [
            json.dumps(
                {
                    "action": "wait_human",
                    "reason": "请补充产品需求",
                    "human_question": "请上传需求文档",
                },
                ensure_ascii=False,
            )
        ]
    )
    app = create_app(
        tmp_path,
        runner_factory=lambda _workspace, _store: runner,
    )
    with TestClient(app) as client:
        _create_project(client)
        started = client.post(
            "/api/v1/projects/demo/runs",
            params={"async_run": "true"},
            json={},
        )
        run_id = started.json()["data"]["run_id"]
        task = None
        for _ in range(30):
            task = client.get(f"/api/v1/projects/demo/runs/{run_id}").json()["data"]
            if task["status"] not in {"queued", "running"}:
                break
            import time
            time.sleep(0.02)
        latest = client.get("/api/v1/projects/demo/runs/latest")

    assert started.status_code == 200
    assert started.json()["data"]["status"] in {"queued", "running", "needs_human"}
    assert task["status"] == "needs_human"
    assert task["result"]["action"]["action"] == "wait_human"
    assert len(task["timeline"]) >= 3
    assert latest.status_code == 200
    assert latest.json()["data"]["run_id"] == run_id
    assert latest.json()["data"]["status"] == "needs_human"

def _create_project(client: TestClient, project_id: str = "demo") -> None:
    response = client.post(
        "/api/v1/projects",
        json={
            "project_id": project_id,
            "name": "演示项目",
            "created_by": "tester-001",
        },
    )
    assert response.status_code == 201


def _seed_requirement_review(
    tmp_path,
    *,
    project_id: str = "demo",
    module_id: str | None = None,
    state: WorkflowState = WorkflowState.REQUIREMENT_RECEIVED,
) -> ArtifactStore:
    timestamp = datetime.now(timezone.utc)
    store = ArtifactStore(tmp_path / "projects", module_id=module_id)
    review = ReviewModel(
        meta=ArtifactMeta(
            artifact_id="review-001",
            artifact_type="requirement_review",
            project_id=project_id,
            status=ArtifactStatus.PENDING,
            created_by="requirement-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        decision=ReviewDecision.NEEDS_HUMAN_DECISION,
        issues=[
            ReviewIssueModel(
                issue_id="REQ-ISSUE-001",
                issue_type="规则缺失",
                severity="high",
                location="第 12-14 行",
                description="连续失败后的锁定规则未定义",
                impact="测试无法判断第几次失败后应锁定账号",
                suggestion="明确失败次数、统计周期和解锁方式",
                needs_product_confirmation=True,
            ),
            ReviewIssueModel(
                issue_id="REQ-ISSUE-002",
                issue_type="表达不清",
                severity="low",
                location="第 20 行",
                description="提示文案未说明展示时机",
                impact="测试无法确定提示出现的时机",
                suggestion="补充提示展示时机",
                needs_product_confirmation=False,
            ),
        ],
        open_questions=[
            "账号连续登录失败多少次后锁定？",
            "管理员手动解锁后失败次数是否清零？",
        ],
    )
    store.save_artifact(review)
    workflow = WorkflowRun.model_validate(store.load_workflow(project_id))
    workflow.current_state = state
    workflow.active_artifacts["requirement_review"] = ArtifactPointer(
        artifact_id=review.meta.artifact_id,
        artifact_type=review.meta.artifact_type,
        version=review.meta.version,
    )
    store.save_workflow(project_id, workflow)
    return store


def _seed_test_design(
    tmp_path,
    state: WorkflowState,
    project_id: str = "demo",
) -> ArtifactStore:
    timestamp = datetime.now(timezone.utc)
    store = ArtifactStore(tmp_path / "projects")
    design = DesignModel(
        meta=ArtifactMeta(
            artifact_id="design-001",
            artifact_type="test_design",
            project_id=project_id,
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
            )
        ],
    )
    store.save_artifact(design)
    manager = ProjectManager(store.projects_root)
    workflow = manager.load_workflow(project_id)
    workflow.current_state = state
    workflow.active_artifacts["test_design"] = ArtifactPointer(
        artifact_id="design-001",
        artifact_type="test_design",
        version=1,
    )
    store.save_workflow(project_id, workflow)
    return store


def _seed_test_report(
    tmp_path,
    state: WorkflowState,
    project_id: str = "demo",
) -> ArtifactStore:
    timestamp = datetime.now(timezone.utc)
    store = ArtifactStore(tmp_path / "projects")
    report = ReportModel(
        meta=ArtifactMeta(
            artifact_id="report-001",
            artifact_type="test_report",
            project_id=project_id,
            status=ArtifactStatus.PENDING,
            created_by="main-flow-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        scope="修改收货地址",
        environment="SIT",
        total_cases=1,
        passed=1,
        failed=0,
        blocked=0,
        skipped=0,
        conclusion="核心功能验证通过。",
    )
    store.save_artifact(report)
    manager = ProjectManager(store.projects_root)
    workflow = manager.load_workflow(project_id)
    workflow.current_state = state
    workflow.active_artifacts["test_report"] = ArtifactPointer(
        artifact_id="report-001",
        artifact_type="test_report",
        version=1,
    )
    store.save_workflow(project_id, workflow)
    return store


def _execution_payload(*, with_missing_evidence: bool = False) -> dict:
    evidence = []
    if with_missing_evidence:
        evidence.append(
            {
                "evidence_id": "missing-log",
                "evidence_type": "log",
                "path": "evidence/missing.log",
                "description": "不存在的日志",
            }
        )
    return {
        "submitted_by": "tester-001",
        "records": [
            {
                "record_id": "record-TC-001",
                "case_id": "TC-001",
                "case_version": 1,
                "environment": "test",
                "executed_by": "tester-001",
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "result": "passed",
                "actual_result": "结果符合预期",
                "evidence": evidence,
            }
        ],
    }


def test_web_application_and_built_assets_are_served(tmp_path):
    with _client(tmp_path) as client:
        page = client.get("/")

        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert '<div id="app"></div>' in page.text
        asset_path = page.text.split('src="', 1)[1].split('"', 1)[0]
        asset = client.get(asset_path)

    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]


def test_approval_uses_active_artifact_and_advances_workflow(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.WAITING_TESTCASE_APPROVAL)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "testcase_approval",
                "decision": "approved",
                "decided_by": "tester-001",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["target_artifact"] == {
        "artifact_id": "design-001",
        "artifact_type": "test_design",
        "version": 1,
    }
    assert response.json()["data"]["state"] == "waiting_manual_execution"


def test_risk_acceptance_advances_requirement_review_to_analysis(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_requirement_review(tmp_path, state=WorkflowState.WAITING_PRODUCT_REVISION)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "risk_acceptance",
                "decision": "approved",
                "decided_by": "product-owner",
                "comment": "已知登录锁定规则暂缺，但本轮先按现网行为开展测试，产品负责人跟进补充。",
            },
        )
        workflow = client.get("/api/v1/projects/demo/workflow")

    assert response.status_code == 200
    assert response.json()["data"]["target_artifact"] == {
        "artifact_id": "review-001",
        "artifact_type": "requirement_review",
        "version": 1,
    }
    assert response.json()["data"]["state"] == "requirement_analyzing"
    assert workflow.json()["data"]["state"] == "requirement_analyzing"
    assert "已知登录锁定规则暂缺" in workflow.json()["data"]["transition_history"][-1]["reason"]


def test_risk_acceptance_requires_a_reason(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_requirement_review(tmp_path, state=WorkflowState.WAITING_PRODUCT_REVISION)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "risk_acceptance",
                "decision": "approved",
                "decided_by": "product-owner",
                "comment": "   ",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


def test_testcase_changes_requested_returns_to_revision(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.WAITING_TESTCASE_APPROVAL)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "testcase_approval",
                "decision": "changes_requested",
                "decided_by": "tester-001",
                "comment": "补充管理员权限和并发保存场景",
            },
        )
        workflow = client.get("/api/v1/projects/demo/workflow")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "waiting_case_revision"
    assert workflow.json()["data"]["revision_rounds"]["testcase"] == 1


@pytest.mark.parametrize("decision", ["changes_requested", "rejected"])
def test_report_non_approval_returns_to_generation(tmp_path, decision):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_report(tmp_path, WorkflowState.WAITING_REPORT_APPROVAL)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "report_approval",
                "decision": decision,
                "decided_by": "tester-001",
                "comment": "补充失败用例风险和发布建议",
            },
        )
        workflow = client.get("/api/v1/projects/demo/workflow")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "generating_report"
    assert workflow.json()["data"]["revision_rounds"]["report"] == 1


def test_non_approval_requires_comment(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.WAITING_TESTCASE_APPROVAL)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "testcase_approval",
                "decision": "rejected",
                "decided_by": "tester-001",
                "comment": "   ",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


def test_approval_rejects_invalid_workflow_state(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.REQUIREMENT_RECEIVED)
        response = client.post(
            "/api/v1/projects/demo/approvals",
            json={
                "approval_type": "testcase_approval",
                "decision": "approved",
                "decided_by": "tester-001",
            },
        )

    assert response.status_code == 409
    assert response.json()["code"] == "HUMAN_ACTION_REJECTED"


def test_manual_execution_advances_to_report_generation(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.WAITING_MANUAL_EXECUTION)
        response = client.post(
            "/api/v1/projects/demo/executions",
            json=_execution_payload(),
        )

    assert response.status_code == 201
    assert response.json()["data"]["state"] == "generating_report"
    assert response.json()["data"]["artifact_path"].endswith("v1.json")


def test_manual_execution_rejects_missing_evidence(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        _seed_test_design(tmp_path, WorkflowState.WAITING_MANUAL_EXECUTION)
        response = client.post(
            "/api/v1/projects/demo/executions",
            json=_execution_payload(with_missing_evidence=True),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "HUMAN_ACTION_REJECTED"
    assert "evidence file does not exist" in response.json()["message"]


def test_evidence_upload_is_immutable_and_rejects_duplicate_id(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        first = client.post(
            "/api/v1/projects/demo/evidence",
            data={
                "evidence_type": "screenshot",
                "description": "保存成功截图",
                "evidence_id": "proof-001",
            },
            files={"file": ("proof.png", b"png-content", "image/png")},
        )
        duplicate = client.post(
            "/api/v1/projects/demo/evidence",
            data={
                "evidence_type": "log",
                "description": "重复标识日志",
                "evidence_id": "proof-001",
            },
            files={"file": ("proof.log", b"log-content", "text/plain")},
        )

    assert first.status_code == 201
    assert first.json()["data"]["path"] == "evidence/proof-001.png"
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "EVIDENCE_ALREADY_EXISTS"
    assert not (tmp_path / "projects" / "demo" / "evidence" / "proof-001.log").exists()


def test_active_artifact_returns_json_and_optional_markdown(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        store = _seed_test_design(
            tmp_path,
            WorkflowState.WAITING_TESTCASE_APPROVAL,
        )
        store.save_artifact_text(
            "demo",
            "test_design",
            "design-001",
            1,
            "# 测试设计",
        )
        response = client.get("/api/v1/projects/demo/artifacts/test_design")
        missing = client.get("/api/v1/projects/demo/artifacts/test_report")

    assert response.status_code == 200
    assert response.json()["data"]["content"]["meta"]["artifact_id"] == "design-001"
    assert response.json()["data"]["markdown"] == "# 测试设计"
    assert response.json()["data"]["markdown_path"].endswith("v1.md")
    assert missing.status_code == 404
    assert missing.json()["code"] == "ACTIVE_ARTIFACT_NOT_FOUND"


def test_confirmation_checklist_requires_an_active_requirement_review(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        response = client.post(
            "/api/v1/projects/demo/confirmation-checklists"
        )

    assert response.status_code == 404
    assert response.json()["code"] == "REQUIREMENT_REVIEW_NOT_FOUND"


def test_confirmation_checklist_is_saved_as_versioned_json_and_markdown(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client)
        store = _seed_requirement_review(tmp_path)

        first = client.post(
            "/api/v1/projects/demo/confirmation-checklists"
        )
        second = client.post(
            "/api/v1/projects/demo/confirmation-checklists"
        )
        active = client.get(
            "/api/v1/projects/demo/artifacts/product_confirmation_checklist"
        )

    assert first.status_code == 201
    first_data = first.json()["data"]
    assert first_data["version"] == 1
    assert first_data["content"]["source_review_id"] == "review-001"
    assert first_data["content"]["source_review_version"] == 1
    assert len(first_data["content"]["items"]) == 2
    assert first_data["content"]["items"][0] == {
        "item_id": "CHK-001",
        "source_issue_id": "REQ-ISSUE-001",
        "severity": "high",
        "location": "第 12-14 行",
        "question": "账号连续登录失败多少次后锁定？",
        "decision_options": [
            "接受建议并补充为正式产品规则",
            "不采纳建议，并填写替代产品规则",
        ],
        "product_decision": "",
        "owner": "",
        "status": "pending",
    }
    assert first_data["content"]["items"][1]["source_issue_id"] is None
    assert "管理员手动解锁后失败次数是否清零？" in first_data["markdown"]
    assert "测试无法判断第几次失败后应锁定账号" not in first_data["markdown"]
    assert "明确失败次数、统计周期和解锁方式" not in first_data["markdown"]
    assert first_data["markdown_path"].endswith("v1.md")

    second_data = second.json()["data"]
    assert second_data["artifact_id"] == first_data["artifact_id"]
    assert second_data["version"] == 2
    assert second_data["markdown_path"].endswith("v2.md")
    assert active.status_code == 200
    assert active.json()["data"]["version"] == 2

    workflow = WorkflowRun.model_validate(store.load_workflow("demo"))
    pointer = workflow.active_artifacts["product_confirmation_checklist"]
    assert pointer.artifact_id == first_data["artifact_id"]
    assert pointer.version == 2
    artifact_root = (
        store.project_root("demo")
        / "artifacts"
        / "product_confirmation_checklist"
        / first_data["artifact_id"]
    )
    assert (artifact_root / "v1.json").is_file()
    assert (artifact_root / "v1.md").is_file()
    assert (artifact_root / "v2.json").is_file()
    assert (artifact_root / "v2.md").is_file()


def test_confirmation_checklist_is_isolated_by_feature_module(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "commerce")
        module = client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "checkout",
                "name": "结算",
                "created_by": "tester-001",
            },
        )
        assert module.status_code == 201
        _seed_requirement_review(
            tmp_path,
            project_id="commerce",
            module_id="checkout",
        )

        generated = client.post(
            "/api/v1/projects/commerce/confirmation-checklists",
            params={"module_id": "checkout"},
        )
        module_active = client.get(
            "/api/v1/projects/commerce/artifacts/"
            "product_confirmation_checklist",
            params={"module_id": "checkout"},
        )
        root_generation = client.post(
            "/api/v1/projects/commerce/confirmation-checklists"
        )
        root_active = client.get(
            "/api/v1/projects/commerce/artifacts/"
            "product_confirmation_checklist"
        )

    assert generated.status_code == 201
    assert module_active.status_code == 200
    assert root_generation.status_code == 404
    assert root_generation.json()["code"] == "REQUIREMENT_REVIEW_NOT_FOUND"
    assert root_active.status_code == 404
    assert (
        tmp_path
        / "projects"
        / "commerce"
        / "modules"
        / "checkout"
        / "artifacts"
        / "product_confirmation_checklist"
    ).is_dir()


def test_feature_module_create_list_detail_and_duplicate_conflict(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "commerce")
        checkout = client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "checkout",
                "name": "结算",
                "created_by": "tester-001",
            },
        )
        profile = client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "profile",
                "name": "个人资料",
                "created_by": "tester-002",
            },
        )
        duplicate = client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "checkout",
                "name": "重复结算",
                "created_by": "tester-001",
            },
        )
        listed = client.get("/api/v1/projects/commerce/modules")
        detail = client.get("/api/v1/projects/commerce/modules/checkout")

    assert checkout.status_code == 201
    assert checkout.json()["data"]["module_id"] == "checkout"
    assert checkout.json()["data"]["state"] == "requirement_received"
    assert profile.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "FEATURE_MODULE_ALREADY_EXISTS"
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 2
    assert {item["module_id"] for item in listed.json()["data"]["items"]} == {
        "checkout",
        "profile",
    }
    assert detail.status_code == 200
    assert detail.json()["data"]["workflow_id"].startswith("wf-")


def test_feature_module_input_and_workflow_are_isolated_from_v1_context(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "commerce")
        created = client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "checkout",
                "name": "结算",
                "created_by": "tester-001",
            },
        )
        uploaded = client.post(
            "/api/v1/projects/commerce/inputs",
            params={"module_id": "checkout"},
            data={
                "category": "requirement",
                "imported_by": "tester-001",
                "input_id": "checkout-requirement",
            },
            files={"file": ("checkout.md", "# 结算需求", "text/markdown")},
        )
        module_workflow = client.get(
            "/api/v1/projects/commerce/workflow",
            params={"module_id": "checkout"},
        )
        root_workflow = client.get("/api/v1/projects/commerce/workflow")
        preview = client.get(
            "/api/v1/projects/commerce/inputs/checkout-requirement",
            params={"module_id": "checkout"},
        )
        missing_from_root = client.get(
            "/api/v1/projects/commerce/inputs/checkout-requirement"
        )

    assert created.status_code == 201
    assert uploaded.status_code == 201
    assert module_workflow.status_code == 200
    assert module_workflow.json()["data"]["module_id"] == "checkout"
    assert module_workflow.json()["data"]["input_files"][0]["input_id"] == (
        "checkout-requirement"
    )
    assert root_workflow.status_code == 200
    assert root_workflow.json()["data"]["module_id"] is None
    assert root_workflow.json()["data"]["input_files"] == []
    assert preview.status_code == 200
    assert preview.json()["data"]["content"] == "# 结算需求"
    assert preview.json()["data"]["content_url"].endswith(
        "?module_id=checkout"
    )
    assert missing_from_root.status_code == 404
    assert missing_from_root.json()["code"] == "INPUT_NOT_FOUND"
    assert (
        tmp_path
        / "projects"
        / "commerce"
        / "modules"
        / "checkout"
        / "input"
        / "checkout-requirement.md"
    ).is_file()


def test_missing_feature_module_returns_module_not_found(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "commerce")
        detail = client.get("/api/v1/projects/commerce/modules/missing")
        workflow = client.get(
            "/api/v1/projects/commerce/workflow",
            params={"module_id": "missing"},
        )

    assert detail.status_code == 404
    assert detail.json()["code"] == "FEATURE_MODULE_NOT_FOUND"
    assert workflow.status_code == 404
    assert workflow.json()["code"] == "FEATURE_MODULE_NOT_FOUND"

def test_project_update_delete_endpoints_and_fallback_after_delete(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "first")
        _create_project(client, "second")

        updated = client.put(
            "/api/v1/projects/first",
            json={"name": "第一项目（已修改）"},
        )
        deleted = client.delete("/api/v1/projects/first")
        missing = client.get("/api/v1/projects/first")
        listed = client.get("/api/v1/projects")

    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "第一项目（已修改）"
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "resource_type": "project",
        "resource_id": "first",
    }
    assert missing.status_code == 404
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["project_id"] == "second"
    assert not (tmp_path / "projects" / "first").exists()


def test_feature_module_update_delete_endpoints_preserve_other_context(tmp_path):
    with _client(tmp_path) as client:
        _create_project(client, "commerce")
        client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "checkout",
                "name": "旧结算",
                "created_by": "tester-001",
            },
        )
        client.post(
            "/api/v1/projects/commerce/modules",
            json={
                "module_id": "profile",
                "name": "个人资料",
                "created_by": "tester-001",
            },
        )

        updated = client.put(
            "/api/v1/projects/commerce/modules/checkout",
            json={"name": "新结算"},
        )
        deleted = client.delete(
            "/api/v1/projects/commerce/modules/checkout",
        )
        missing = client.get(
            "/api/v1/projects/commerce/modules/checkout",
        )
        profile = client.get(
            "/api/v1/projects/commerce/modules/profile",
        )
        listed = client.get("/api/v1/projects/commerce/modules")

    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "新结算"
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "resource_type": "feature_module",
        "resource_id": "checkout",
    }
    assert missing.status_code == 404
    assert missing.json()["code"] == "FEATURE_MODULE_NOT_FOUND"
    assert profile.status_code == 200
    assert profile.json()["data"]["name"] == "个人资料"
    assert listed.json()["data"]["total"] == 1
    assert not (
        tmp_path / "projects" / "commerce" / "modules" / "checkout"
    ).exists()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/v1/projects/missing", {"name": "不存在"}),
        (
            "put",
            "/api/v1/projects/demo/modules/missing",
            {"name": "不存在"},
        ),
    ],
)
def test_management_update_missing_resource_returns_not_found(
    tmp_path,
    method,
    path,
    payload,
):
    with _client(tmp_path) as client:
        _create_project(client)
        response = getattr(client, method)(path, json=payload)

    assert response.status_code == 404
    assert response.json()["data"] is None
