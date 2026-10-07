from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

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


@pytest.mark.parametrize("component", [".", ".."])
def test_store_rejects_special_components_for_all_path_identifiers(
    tmp_path, component
) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError, match="invalid module_id"):
        ArtifactStore(tmp_path, module_id=component)
    with pytest.raises(ArtifactStoreError, match="invalid project_id"):
        store.save_project(component, {"name": "Invalid"})
    with pytest.raises(ArtifactStoreError, match="invalid project_id"):
        store.load_project(component)
    with pytest.raises(ArtifactStoreError, match="invalid project_id"):
        store.delete_project(component)
    with pytest.raises(ArtifactStoreError, match="invalid artifact_type"):
        store.load_artifact("demo", component, "design")
    with pytest.raises(ArtifactStoreError, match="invalid artifact_id"):
        store.load_artifact("demo", "test_design", component)
    with pytest.raises(ArtifactStoreError, match="invalid decision_id"):
        store.save_decision("demo", component, {"decision": "approved"})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module_id", [".", "..", ""])
def test_delete_module_rejects_special_ids_and_preserves_siblings(
    tmp_path, module_id
) -> None:
    store = ArtifactStore(tmp_path)
    store.save_project("demo", {"name": "Demo"})
    for sibling in ("login", "payment"):
        ArtifactStore(tmp_path, module_id=sibling).save_module(
            "demo", {"name": sibling}
        )

    with pytest.raises(ArtifactStoreError, match="invalid module_id"):
        store.delete_module("demo", module_id)
    for sibling in ("login", "payment"):
        assert ArtifactStore(tmp_path, module_id=sibling).load_module("demo") == {
            "name": sibling
        }
    assert store.load_project("demo") == {"name": "Demo"}


def test_module_store_does_not_fall_back_for_explicit_empty_id(tmp_path) -> None:
    store = ArtifactStore(tmp_path, module_id="login")
    store.save_module("demo", {"name": "Login"})
    with pytest.raises(ArtifactStoreError, match="invalid module_id"):
        store.delete_module("demo", "")
    assert store.load_module("demo") == {"name": "Login"}


def test_store_accepts_dotted_names_and_deletes_only_requested_module(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    store.save_project("demo.v2", {"name": "Demo"})
    for module_id in ("login.v2", "payment"):
        module_store = ArtifactStore(tmp_path, module_id=module_id)
        module_store.save_module("demo.v2", {"name": module_id})

    store.delete_module("demo.v2", "login.v2")
    assert not (tmp_path / "demo.v2" / "modules" / "login.v2").exists()
    assert ArtifactStore(tmp_path, module_id="payment").load_module("demo.v2") == {
        "name": "payment"
    }
    assert store.load_project("demo.v2") == {"name": "Demo"}


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("alias_level", ["project", "modules", "module"])
def test_module_paths_reject_directory_aliases(tmp_path, alias_level) -> None:
    store = ArtifactStore(tmp_path, module_id="login")
    target = tmp_path / "sibling"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = tmp_path / "demo"
    if alias_level in {"modules", "module"}:
        link.mkdir()
        link = link / "modules"
    if alias_level == "module":
        link.mkdir()
        link = link / "login"
    _link_directory(link, target)

    with pytest.raises(ArtifactStoreError, match="direct child without aliases"):
        store.project_root("demo")
    with pytest.raises(ArtifactStoreError, match="direct child without aliases"):
        store.save_module("demo", {"name": "Invalid alias"})
    with pytest.raises(ArtifactStoreError, match="direct child without aliases"):
        store.load_module("demo")
    with pytest.raises(ArtifactStoreError, match="direct child without aliases"):
        store.delete_module("demo")
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (target / "module.json").exists()


def test_delete_module_revalidates_target_before_removal(tmp_path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path, module_id="login")
    target = store.project_root("demo")
    target.mkdir(parents=True)
    sibling = target.parent / "payment"
    sibling.mkdir()
    marker = sibling / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    original_is_dir = Path.is_dir
    replaced = False

    def replace_checked_directory(path):
        nonlocal replaced
        if path == target and not replaced:
            target.rmdir()
            _link_directory(target, sibling)
            replaced = True
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", replace_checked_directory)
    with pytest.raises(ArtifactStoreError, match="direct child without aliases"):
        store.delete_module("demo")
    assert replaced
    assert marker.read_text(encoding="utf-8") == "keep"
