from pathlib import Path

import pytest

from qa_agent.evidence import EvidenceError, EvidenceService
from qa_agent.schemas import Evidence, ExecutionBatch
from qa_agent.storage import ArtifactStore, ArtifactStoreError


def test_import_evidence_records_hash_and_manifest(tmp_path):
    source = tmp_path / "failure.log"
    source.write_text("timeout from payment service\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "projects")
    store.project_root("demo")
    service = EvidenceService(store)

    evidence = service.import_file(
        "demo",
        source,
        "log",
        description="支付超时日志",
        evidence_id="payment-timeout",
    )

    target = store.project_root("demo") / evidence.path
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert evidence.media_type == "text/plain"
    assert evidence.size_bytes == target.stat().st_size
    assert store.load_evidence_manifest("demo", "payment-timeout")["sha256"] == evidence.sha256


def test_import_evidence_never_overwrites_existing_file(tmp_path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    store = ArtifactStore(tmp_path / "projects")
    store.project_root("demo")
    service = EvidenceService(store)
    service.import_file("demo", first, "log", description="first", evidence_id="same")

    with pytest.raises((EvidenceError, ArtifactStoreError)):
        service.import_file("demo", second, "log", description="second", evidence_id="same")

    assert (store.project_root("demo") / "evidence" / "same.log").read_text() == "first"


def test_verify_rejects_tampered_evidence(tmp_path):
    source = tmp_path / "screen.png"
    source.write_bytes(b"original")
    store = ArtifactStore(tmp_path / "projects")
    store.project_root("demo")
    service = EvidenceService(store)
    evidence = service.import_file(
        "demo",
        source,
        "screenshot",
        description="截图",
        evidence_id="screen-001",
    )
    (store.project_root("demo") / evidence.path).write_bytes(b"tampered")

    with pytest.raises(EvidenceError, match="sha256 mismatch"):
        service.verify("demo", evidence)


def test_verify_uses_manifest_when_execution_record_omits_digest(tmp_path):
    source = tmp_path / "service.log"
    source.write_text("original\n", encoding="utf-8")
    store = ArtifactStore(tmp_path / "projects")
    store.project_root("demo")
    service = EvidenceService(store)
    imported = service.import_file(
        "demo",
        source,
        "log",
        description="服务日志",
        evidence_id="service-log",
    )
    reference = imported.model_copy(update={"sha256": None, "size_bytes": None})
    (store.project_root("demo") / imported.path).write_text(
        "tampered\n", encoding="utf-8"
    )

    with pytest.raises(EvidenceError, match="sha256 mismatch"):
        service.verify("demo", reference)


def test_verify_rejects_path_traversal(tmp_path):
    store = ArtifactStore(tmp_path / "projects")
    store.project_root("demo")

    with pytest.raises(ValueError, match="evidence path"):
        Evidence(
            evidence_id="bad",
            evidence_type="file",
            path="../outside.log",
            description="bad",
        )
