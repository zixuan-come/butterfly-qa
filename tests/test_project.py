import hashlib

import pytest

from qa_agent.project import FeatureModuleManager, InputCategory, ProjectManager
from qa_agent.storage import ArtifactStoreError
from qa_agent.workflow.states import WorkflowState


def test_create_project_initializes_directories_and_workflow(tmp_path):
    manager = ProjectManager(tmp_path / "projects")

    project, workflow = manager.create_project(
        "address-change",
        "修改收货地址",
        created_by="tester-001",
    )

    project_root = manager.store.project_root("address-change")
    assert project.project_id == "address-change"
    assert workflow.current_state is WorkflowState.REQUIREMENT_RECEIVED
    assert (project_root / "project.json").is_file()
    assert (project_root / "workflow.json").is_file()
    assert (project_root / "input").is_dir()
    assert (project_root / "evidence").is_dir()


def test_create_project_rejects_existing_project(tmp_path):
    manager = ProjectManager(tmp_path)
    manager.create_project("demo", "演示项目", created_by="tester-001")

    with pytest.raises(ArtifactStoreError, match="already exists"):
        manager.create_project("demo", "重复项目", created_by="tester-001")


def test_import_input_copies_file_and_updates_manifest_and_workflow(tmp_path):
    source = tmp_path / "修改收货地址.md"
    content = "# 修改收货地址\n\n用户可以修改默认收货地址。\n"
    source.write_text(content, encoding="utf-8")
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo", "演示项目", created_by="tester-001")

    imported = manager.import_input(
        "demo",
        source,
        InputCategory.REQUIREMENT,
        imported_by="tester-001",
        input_id="requirement-001",
    )

    stored = manager.store.project_root("demo") / imported.relative_path
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert stored.read_text(encoding="utf-8") == content
    assert imported.sha256 == source_digest
    assert imported.media_type == "text/markdown"
    assert manager.load_project("demo").inputs == [imported]
    assert manager.load_workflow("demo").input_files == [imported.pointer()]


def test_import_input_never_overwrites_existing_file(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo", "演示项目", created_by="tester-001")
    manager.import_input(
        "demo",
        first,
        InputCategory.REQUIREMENT,
        imported_by="tester-001",
        input_id="requirement-001",
    )

    with pytest.raises(ArtifactStoreError, match="input_id already exists"):
        manager.import_input(
            "demo",
            second,
            InputCategory.REQUIREMENT,
            imported_by="tester-001",
            input_id="requirement-001",
        )

    stored = manager.store.project_root("demo") / "input" / "requirement-001.md"
    assert stored.read_text(encoding="utf-8") == "first"


def test_import_input_rejects_unsafe_id_before_copying(tmp_path):
    source = tmp_path / "requirement.md"
    source.write_text("requirement", encoding="utf-8")
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo", "演示项目", created_by="tester-001")

    with pytest.raises(ArtifactStoreError, match="invalid input_id"):
        manager.import_input(
            "demo",
            source,
            InputCategory.REQUIREMENT,
            imported_by="tester-001",
            input_id="../outside",
        )

    assert not (tmp_path / "projects" / "demo" / "outside.md").exists()
def test_create_feature_modules_initializes_independent_workflows(tmp_path):
    projects_root = tmp_path / "projects"
    project_manager = ProjectManager(projects_root)
    _, root_workflow = project_manager.create_project(
        "commerce",
        "电商平台",
        created_by="tester-001",
    )

    checkout_manager = FeatureModuleManager(projects_root, "commerce", "checkout")
    profile_manager = FeatureModuleManager(projects_root, "commerce", "profile")
    checkout, checkout_workflow = checkout_manager.create_module(
        "结算",
        created_by="tester-001",
    )
    profile, profile_workflow = profile_manager.create_module(
        "个人资料",
        created_by="tester-002",
    )

    project_root = project_manager.store.project_root("commerce")
    assert checkout.project_id == "commerce"
    assert checkout.module_id == "checkout"
    assert profile.module_id == "profile"
    assert checkout_workflow.workflow_id != profile_workflow.workflow_id
    assert checkout_workflow.workflow_id != root_workflow.workflow_id
    assert (project_root / "modules" / "checkout" / "module.json").is_file()
    assert (project_root / "modules" / "checkout" / "workflow.json").is_file()
    assert (project_root / "modules" / "profile" / "artifacts").is_dir()
    assert project_manager.load_workflow("commerce") == root_workflow


def test_feature_module_inputs_are_isolated_from_other_modules_and_v1(tmp_path):
    projects_root = tmp_path / "projects"
    source = tmp_path / "checkout.md"
    source.write_text("# 结算需求", encoding="utf-8")
    project_manager = ProjectManager(projects_root)
    project_manager.create_project("commerce", "电商平台", created_by="tester-001")
    checkout_manager = FeatureModuleManager(projects_root, "commerce", "checkout")
    profile_manager = FeatureModuleManager(projects_root, "commerce", "profile")
    checkout_manager.create_module("结算", created_by="tester-001")
    profile_manager.create_module("个人资料", created_by="tester-001")

    imported = checkout_manager.import_input(
        "commerce",
        source,
        InputCategory.REQUIREMENT,
        imported_by="tester-001",
        input_id="checkout-requirement",
    )

    checkout_root = checkout_manager.store.project_root("commerce")
    assert (checkout_root / imported.relative_path).is_file()
    assert checkout_manager.load_module().inputs == [imported]
    assert checkout_manager.load_workflow("commerce").input_files == [imported.pointer()]
    assert profile_manager.load_module().inputs == []
    assert profile_manager.load_workflow("commerce").input_files == []
    assert project_manager.load_project("commerce").inputs == []
    assert project_manager.load_workflow("commerce").input_files == []
