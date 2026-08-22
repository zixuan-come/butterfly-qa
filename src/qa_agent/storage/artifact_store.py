"""Filesystem-backed storage for project artifacts and workflow metadata."""

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class ArtifactStoreError(ValueError):
    """Raised when a project path or artifact operation is invalid."""


class ArtifactStore:
    """Store immutable artifact versions and mutable workflow pointers locally."""

    _SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def project_root(self, project_id: str) -> Path:
        self._validate_component(project_id, "project_id")
        project_root = (self.projects_root / project_id).resolve()
        if self.projects_root not in project_root.parents:
            raise ArtifactStoreError("project path escapes projects root")
        return project_root

    def save_artifact(self, artifact: BaseModel) -> Path:
        """Save a versioned JSON artifact and update its latest pointer."""

        meta = getattr(artifact, "meta", None)
        if meta is None:
            raise ArtifactStoreError("artifact must contain meta")

        artifact_type = self._validate_component(meta.artifact_type, "artifact_type")
        artifact_id = self._validate_component(meta.artifact_id, "artifact_id")
        project_root = self.project_root(meta.project_id)
        artifact_dir = project_root / "artifacts" / artifact_type / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        version_path = artifact_dir / f"v{meta.version}.json"
        self._write_json(
            version_path,
            artifact.model_dump(mode="json"),
            overwrite=False,
        )
        self._write_json(
            artifact_dir / "latest.json",
            {"artifact_id": artifact_id, "version": meta.version, "path": version_path.name},
        )
        return version_path

    def load_artifact(
        self,
        project_id: str,
        artifact_type: str,
        artifact_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Load a specific version, or the latest version when omitted."""

        self._validate_component(artifact_type, "artifact_type")
        self._validate_component(artifact_id, "artifact_id")
        artifact_dir = self.project_root(project_id) / "artifacts" / artifact_type / artifact_id
        if version is None:
            pointer = self._read_json(artifact_dir / "latest.json")
            path = artifact_dir / pointer["path"]
        else:
            if version < 1:
                raise ArtifactStoreError("version must be positive")
            path = artifact_dir / f"v{version}.json"
        return self._read_json(path)

    def save_workflow(self, project_id: str, workflow: BaseModel | dict[str, Any]) -> Path:
        path = self.project_root(project_id) / "workflow.json"
        self._write_json(path, self._to_jsonable(workflow))
        return path

    def load_workflow(self, project_id: str) -> dict[str, Any]:
        return self._read_json(self.project_root(project_id) / "workflow.json")

    def save_decision(
        self,
        project_id: str,
        decision_id: str,
        decision: BaseModel | dict[str, Any],
    ) -> Path:
        self._validate_component(decision_id, "decision_id")
        path = self.project_root(project_id) / "decisions" / f"{decision_id}.json"
        self._write_json(path, self._to_jsonable(decision), overwrite=False)
        return path

    def load_decision(self, project_id: str, decision_id: str) -> dict[str, Any]:
        self._validate_component(decision_id, "decision_id")
        path = self.project_root(project_id) / "decisions" / f"{decision_id}.json"
        return self._read_json(path)

    @classmethod
    def _validate_component(cls, value: str, name: str) -> str:
        if not value or not cls._SAFE_COMPONENT.fullmatch(value):
            raise ArtifactStoreError(f"invalid {name}: {value!r}")
        return value

    @staticmethod
    def _to_jsonable(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    @staticmethod
    def _write_json(
        path: Path,
        value: dict[str, Any],
        *,
        overwrite: bool = True,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"

        try:
            temporary_path.write_text(payload, encoding="utf-8")
            if overwrite:
                os.replace(temporary_path, path)
            else:
                try:
                    os.link(temporary_path, path)
                except FileExistsError as exc:
                    raise ArtifactStoreError(
                        f"artifact version already exists: {path}"
                    ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ArtifactStoreError(f"artifact file does not exist: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
