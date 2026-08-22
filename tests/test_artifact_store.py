from datetime import datetime, timezone

import pytest

from qa_agent.schemas import (
    ArtifactMeta,
    ArtifactStatus,
    TestCase as CaseModel,
    TestDesign as DesignModel,
    TestPoint as PointModel,
    TestStep as StepModel,
)
from qa_agent.storage import ArtifactStore, ArtifactStoreError
from qa_agent.workflow import WorkflowRun


def make_design(version: int = 1) -> DesignModel:
    timestamp = datetime.now(timezone.utc)
    return DesignModel(
        meta=ArtifactMeta(
            artifact_id="design-001",
            artifact_type="test_design",
            project_id="demo-project",
            version=version,
            status=ArtifactStatus.DRAFT,
            created_by="test-agent",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        test_points=[
            PointModel(
                test_point_id="TP-001",
                requirement_refs=["REQ-001"],
                category="normal",
                description="保存有效地址",
            )
        ],
        test_cases=[
            CaseModel(
                case_id="TC-001",
                requirement_refs=["REQ-001"],
                test_point_refs=["TP-001"],
                title="保存有效收货地址",
                priority="P1",
                steps=[
                    StepModel(
                        step_no=1,
                        action="保存地址",
                        expected_result="地址保存成功",
                    )
                ],
            )
        ],
    )


def test_store_preserves_versions_and_loads_latest(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    first_path = store.save_artifact(make_design(version=1))
    second_path = store.save_artifact(make_design(version=2))

    assert first_path.name == "v1.json"
    assert second_path.name == "v2.json"
    assert store.load_artifact(
        "demo-project", "test_design", "design-001"
    )["meta"]["version"] == 2
    assert store.load_artifact(
        "demo-project", "test_design", "design-001", 1
    )["meta"]["version"] == 1


def test_store_rejects_duplicate_version(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    store.save_artifact(make_design())

    with pytest.raises(ArtifactStoreError, match="already exists"):
        store.save_artifact(make_design())


def test_store_rejects_unsafe_project_id(tmp_path) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(ArtifactStoreError, match="invalid project_id"):
        store.project_root("../outside")


def test_store_saves_and_loads_workflow(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    timestamp = datetime.now(timezone.utc)
    workflow = WorkflowRun(
        workflow_id="wf-001",
        project_id="demo-project",
        created_at=timestamp,
        updated_at=timestamp,
    )

    store.save_workflow("demo-project", workflow)
    loaded = store.load_workflow("demo-project")

    assert loaded["workflow_id"] == "wf-001"
    assert loaded["current_state"] == "requirement_received"


def test_store_does_not_overwrite_decision(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    decision = {"decision": "approved", "decided_by": "tester-001"}

    store.save_decision("demo-project", "decision-001", decision)

    with pytest.raises(ArtifactStoreError, match="already exists"):
        store.save_decision(
            "demo-project",
            "decision-001",
            {"decision": "rejected", "decided_by": "tester-002"},
        )

    assert store.load_decision("demo-project", "decision-001") == decision


def test_atomic_writes_leave_no_temporary_files(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    store.save_workflow("demo-project", {"current_state": "requirement_received"})

    project_root = store.project_root("demo-project")
    assert list(project_root.rglob("*.tmp")) == []
