"""Project initialization and immutable source-file import."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .storage import ArtifactStore, ArtifactStoreError
from .workflow.models import InputFilePointer, WorkflowRun


class InputCategory(str, Enum):
    """Kinds of source material accepted by a Butterfly Agent project."""

    REQUIREMENT = "requirement"
    DESIGN = "design"
    BUSINESS_RULE = "business_rule"
    CONSTRAINT = "constraint"
    ATTACHMENT = "attachment"


class ProjectInput(BaseModel):
    """Audit metadata for one immutable imported source file."""

    model_config = ConfigDict(extra="forbid")

    input_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    category: InputCategory
    original_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_by: str = Field(min_length=1)
    imported_at: datetime

    def pointer(self) -> InputFilePointer:
        return InputFilePointer(
            input_id=self.input_id,
            category=self.category.value,
            relative_path=self.relative_path,
            sha256=self.sha256,
        )


class ProjectRecord(BaseModel):
    """Mutable project manifest; imported source files remain immutable."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    inputs: list[ProjectInput] = Field(default_factory=list)


class FeatureModuleRecord(BaseModel):
    """Independent workflow context nested under a project."""

    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    project_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    inputs: list[ProjectInput] = Field(default_factory=list)


class ProjectManager:
    """Create local projects and import source material without overwriting it."""

    _MEDIA_TYPES = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".json": "application/json",
    }
    _SAFE_INPUT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

    def __init__(self, projects_root: str | Path) -> None:
        self.store = ArtifactStore(projects_root)

    def create_project(
        self,
        project_id: str,
        name: str,
        *,
        created_by: str,
        created_at: datetime | None = None,
    ) -> tuple[ProjectRecord, WorkflowRun]:
        now = created_at or datetime.now(timezone.utc)
        project_root = self.store.project_root(project_id)
        manifest_path = project_root / "project.json"
        workflow_path = project_root / "workflow.json"
        if manifest_path.exists() or workflow_path.exists():
            raise ArtifactStoreError(f"project already exists: {project_id}")

        project = ProjectRecord(
            project_id=project_id,
            name=name,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        workflow = WorkflowRun(
            workflow_id=f"wf-{uuid4().hex}",
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )

        for directory in ("input", "artifacts", "evidence", "decisions"):
            (project_root / directory).mkdir(parents=True, exist_ok=True)
        self.store.save_project(project_id, project, overwrite=False)
        self.store.save_workflow(project_id, workflow)
        return project, workflow

    def import_input(
        self,
        project_id: str,
        source_path: str | Path,
        category: InputCategory,
        *,
        imported_by: str,
        input_id: str | None = None,
        original_name: str | None = None,
        imported_at: datetime | None = None,
    ) -> ProjectInput:
        source = Path(source_path).resolve(strict=True)
        if not source.is_file():
            raise ArtifactStoreError(f"input source is not a file: {source}")

        project = self._load_record(project_id)
        workflow = WorkflowRun.model_validate(self.store.load_workflow(project_id))
        resolved_input_id = input_id or f"input-{uuid4().hex}"
        if not self._SAFE_INPUT_ID.fullmatch(resolved_input_id):
            raise ArtifactStoreError(f"invalid input_id: {resolved_input_id!r}")
        if any(item.input_id == resolved_input_id for item in project.inputs):
            raise ArtifactStoreError(f"input_id already exists: {resolved_input_id}")

        suffix = source.suffix.lower()
        target_name = f"{resolved_input_id}{suffix}"
        relative_path = Path("input") / target_name
        target = self.store.project_root(project_id) / relative_path
        digest, size = self._copy_immutable(source, target)
        now = imported_at or datetime.now(timezone.utc)
        imported = ProjectInput(
            input_id=resolved_input_id,
            category=category,
            original_name=Path(original_name).name if original_name else source.name,
            relative_path=relative_path.as_posix(),
            media_type=self._detect_media_type(source),
            size_bytes=size,
            sha256=digest,
            imported_by=imported_by,
            imported_at=now,
        )

        project.inputs.append(imported)
        project.updated_at = now
        workflow.input_files.append(imported.pointer())
        workflow.updated_at = now
        self._save_record(project_id, project)
        self.store.save_workflow(project_id, workflow)
        return imported

    def load_project(self, project_id: str) -> ProjectRecord:
        return self._load_record(project_id)

    def load_workflow(self, project_id: str) -> WorkflowRun:
        return WorkflowRun.model_validate(self.store.load_workflow(project_id))

    def update_project(self, project_id: str, *, name: str) -> ProjectRecord:
        project = self._load_record(project_id)
        now = datetime.now(timezone.utc)
        project.name = name
        project.updated_at = now
        self._save_record(project_id, project)
        return project

    def delete_project(self, project_id: str) -> None:
        self._load_record(project_id)
        self.store.delete_project(project_id)
    def _load_record(self, project_id: str) -> ProjectRecord:
        return ProjectRecord.model_validate(self.store.load_project(project_id))

    def _save_record(self, project_id: str, project: ProjectRecord) -> None:
        self.store.save_project(project_id, project)

    @staticmethod
    def _copy_immutable(source: Path, target: Path) -> tuple[str, int]:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ArtifactStoreError(f"input file already exists: {target}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return digest.hexdigest(), size

    @classmethod
    def _detect_media_type(cls, source: Path) -> str:
        return (
            cls._MEDIA_TYPES.get(source.suffix.lower())
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream"
        )


class FeatureModuleManager(ProjectManager):
    """Create and load isolated feature workflows within one project."""

    def __init__(
        self,
        projects_root: str | Path,
        project_id: str,
        module_id: str,
    ) -> None:
        parent_store = ArtifactStore(projects_root)
        parent_store.load_project(project_id)
        self.project_id = project_id
        self.store = ArtifactStore(projects_root, module_id=module_id)
        self.module_id = self.store.module_id

    def create_module(
        self,
        name: str,
        *,
        created_by: str,
        created_at: datetime | None = None,
    ) -> tuple[FeatureModuleRecord, WorkflowRun]:
        now = created_at or datetime.now(timezone.utc)
        module_root = self.store.project_root(self.project_id)
        manifest_path = module_root / "module.json"
        workflow_path = module_root / "workflow.json"
        if manifest_path.exists() or workflow_path.exists():
            raise ArtifactStoreError(
                f"feature module already exists: {self.module_id}"
            )

        module = FeatureModuleRecord(
            module_id=self.module_id,
            project_id=self.project_id,
            name=name,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        workflow = WorkflowRun(
            workflow_id=f"wf-{uuid4().hex}",
            project_id=self.project_id,
            created_at=now,
            updated_at=now,
        )
        for directory in ("input", "artifacts", "evidence", "decisions"):
            (module_root / directory).mkdir(parents=True, exist_ok=True)
        self.store.save_module(self.project_id, module)
        self.store.save_workflow(self.project_id, workflow)
        return module, workflow

    def load_module(self) -> FeatureModuleRecord:
        return self._load_record(self.project_id)

    def update_module(self, *, name: str) -> FeatureModuleRecord:
        module = self.load_module()
        now = datetime.now(timezone.utc)
        module.name = name
        module.updated_at = now
        self._save_record(self.project_id, module)
        return module

    def delete_module(self) -> None:
        self.load_module()
        self.store.delete_module(self.project_id, self.module_id)
    def load_project(self, project_id: str) -> FeatureModuleRecord:
        self._require_project(project_id)
        return self._load_record(project_id)

    def load_workflow(self, project_id: str) -> WorkflowRun:
        self._require_project(project_id)
        return super().load_workflow(project_id)

    def _load_record(self, project_id: str) -> FeatureModuleRecord:
        self._require_project(project_id)
        return FeatureModuleRecord.model_validate(self.store.load_module(project_id))

    def _save_record(
        self,
        project_id: str,
        project: FeatureModuleRecord,
    ) -> None:
        self._require_project(project_id)
        self.store.save_module(project_id, project)

    def _require_project(self, project_id: str) -> None:
        if project_id != self.project_id:
            raise ArtifactStoreError(
                f"feature module belongs to project: {self.project_id}"
            )
