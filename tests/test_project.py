import hashlib

import pytest

from qa_agent.project import InputCategory, ProjectManager
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
