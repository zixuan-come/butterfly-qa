"""Safe import and verification of test evidence files."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from pathlib import Path
from uuid import uuid4

from .schemas import Evidence, ExecutionBatch
from .storage import ArtifactStore, ArtifactStoreError


class EvidenceError(ValueError):
    """Raised when evidence cannot be safely imported or verified."""


class EvidenceService:
    """Manage immutable evidence files within one project's evidence directory."""

    _SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
    _MEDIA_TYPES = {
        ".log": "text/plain",
        ".txt": "text/plain",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
    }

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def import_file(
        self,
        project_id: str,
        source_path: str | Path,
        evidence_type: str,
        *,
        description: str,
        evidence_id: str | None = None,
    ) -> Evidence:
        source = Path(source_path).resolve(strict=True)
        if not source.is_file():
            raise EvidenceError(f"evidence source is not a file: {source}")
        if evidence_type not in {"screenshot", "log", "video", "file", "other"}:
            raise EvidenceError(f"unsupported evidence_type: {evidence_type}")

        resolved_id = evidence_id or f"evidence-{uuid4().hex}"
        if not self._SAFE_ID.fullmatch(resolved_id):
            raise EvidenceError(f"invalid evidence_id: {resolved_id!r}")
        suffix = source.suffix.lower()
        relative_path = Path("evidence") / f"{resolved_id}{suffix}"
        target = self.store.project_root(project_id) / relative_path
        digest, size = self._copy_immutable(source, target)
        evidence = Evidence(
            evidence_id=resolved_id,
            evidence_type=evidence_type,
            path=relative_path.as_posix(),
            description=description,
            sha256=digest,
            size_bytes=size,
            media_type=self._media_type(source),
        )
        try:
            self.store.save_evidence_manifest(project_id, resolved_id, evidence)
        except ArtifactStoreError:
            target.unlink(missing_ok=True)
            raise
        return evidence

    def verify(self, project_id: str, evidence: Evidence) -> None:
        """Verify path containment, existence, size and digest for one file."""

        manifest = self._load_manifest_if_present(project_id, evidence.evidence_id)
        if manifest is not None:
            if manifest.path != evidence.path:
                raise EvidenceError(
                    f"evidence path does not match manifest: {evidence.evidence_id}"
                )
            expected_sha256 = manifest.sha256
            expected_size = manifest.size_bytes
        else:
            expected_sha256 = evidence.sha256
            expected_size = evidence.size_bytes

        project_root = self.store.project_root(project_id)
        path = Path(evidence.path.replace("\\", "/"))
        resolved = (project_root / path).resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise EvidenceError(f"evidence path escapes project root: {evidence.path}") from exc
        if not resolved.is_file():
            raise EvidenceError(f"evidence file does not exist: {evidence.path}")
        digest, size = self._digest(resolved)
        if expected_sha256 and expected_sha256 != digest:
            raise EvidenceError(f"evidence sha256 mismatch: {evidence.evidence_id}")
        if expected_size is not None and expected_size != size:
            raise EvidenceError(f"evidence size mismatch: {evidence.evidence_id}")

    def _load_manifest_if_present(
        self,
        project_id: str,
        evidence_id: str,
    ) -> Evidence | None:
        try:
            payload = self.store.load_evidence_manifest(project_id, evidence_id)
        except ArtifactStoreError:
            return None
        return Evidence.model_validate(payload)

    def verify_batch(self, project_id: str, execution: ExecutionBatch) -> None:
        for record in execution.records:
            for evidence in record.evidence:
                self.verify(project_id, evidence)

    @classmethod
    def _media_type(cls, source: Path) -> str:
        return (
            cls._MEDIA_TYPES.get(source.suffix.lower())
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream"
        )

    @staticmethod
    def _digest(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as reader:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @classmethod
    def _copy_immutable(cls, source: Path, target: Path) -> tuple[str, int]:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                digest = hashlib.sha256()
                size = 0
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise EvidenceError(f"evidence file already exists: {target}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return digest.hexdigest(), size
