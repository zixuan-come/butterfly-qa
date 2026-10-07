from datetime import datetime, timezone

import pytest

from qa_agent.schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    HumanApproval,
    TestCase as CaseModel,
    TestDesign as DesignModel,
    TestStep as StepModel,
)
from qa_agent.validation import ArtifactValidationError, validate_artifact


def make_meta() -> dict:
    timestamp = datetime.now(timezone.utc)
    return {
        "artifact_id": "design-001",
        "artifact_type": "test_design",
        "project_id": "demo-project",
        "status": ArtifactStatus.DRAFT,
        "created_by": "test-agent",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_artifact_meta_accepts_valid_payload() -> None:
    artifact = validate_artifact(ArtifactMeta, make_meta())

    assert artifact.project_id == "demo-project"
    assert artifact.version == 1


def test_test_case_requires_a_test_step() -> None:
    payload = {
        "case_id": "TC-001",
        "requirement_refs": ["REQ-001"],
        "test_point_refs": ["TP-001"],
        "title": "保存有效收货地址",
        "priority": "P1",
        "steps": [],
    }

    with pytest.raises(ArtifactValidationError, match="steps"):
        validate_artifact(CaseModel, payload)


def test_test_case_keeps_step_to_expected_result_mapping() -> None:
    payload = {
        "case_id": "TC-001",
        "requirement_refs": ["REQ-001"],
        "test_point_refs": ["TP-001"],
        "title": "保存有效收货地址",
        "priority": "P1",
        "steps": [
            {
                "step_no": 1,
                "action": "输入完整有效地址并点击保存",
                "expected_result": "地址保存成功并展示在地址列表",
            }
        ],
    }

    case = validate_artifact(CaseModel, payload)

    assert isinstance(case.steps[0], StepModel)
    assert case.steps[0].step_no == 1
    assert case.steps[0].expected_result.startswith("地址保存成功")


def test_rejected_human_approval_requires_comment() -> None:
    timestamp = datetime.now(timezone.utc)
    payload = {
        "meta": {
            **make_meta(),
            "artifact_id": "approval-001",
            "artifact_type": "human_approval",
        },
        "approval_type": ApprovalType.TESTCASE_APPROVAL,
        "target_artifact_id": "design-001",
        "target_artifact_type": "test_design",
        "target_artifact_version": 1,
        "decision": ApprovalDecision.CHANGES_REQUESTED,
        "decided_by": "tester-001",
        "decided_at": timestamp,
        "comment": "",
    }

    with pytest.raises(ArtifactValidationError, match="comment is required"):
        validate_artifact(HumanApproval, payload)


def test_approved_human_approval_can_omit_comment() -> None:
    timestamp = datetime.now(timezone.utc)
    approval = HumanApproval(
        meta=ArtifactMeta(
            **{
                **make_meta(),
                "artifact_id": "approval-001",
                "artifact_type": "human_approval",
            }
        ),
        approval_type=ApprovalType.TESTCASE_APPROVAL,
        target_artifact_id="design-001",
        target_artifact_type="test_design",
        target_artifact_version=1,
        decision=ApprovalDecision.APPROVED,
        decided_by="tester-001",
        decided_at=timestamp,
    )

    assert approval.comment == ""


def make_design_payload() -> dict:
    return {
        "meta": make_meta(),
        "test_points": [
            {
                "test_point_id": "TP-001",
                "requirement_refs": ["REQ-001"],
                "category": "normal",
                "description": "Save a valid address",
            }
        ],
        "test_cases": [
            {
                "case_id": "TC-001",
                "version": 1,
                "requirement_refs": ["REQ-001"],
                "test_point_refs": ["TP-001"],
                "title": "Save a valid address",
                "priority": "P1",
                "steps": [
                    {
                        "step_no": 1,
                        "action": "Save the address",
                        "expected_result": "The address is saved",
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize("second_version", [1, 2])
def test_design_rejects_duplicate_case_ids_even_across_versions(second_version) -> None:
    payload = make_design_payload()
    payload["test_cases"].append(
        {**payload["test_cases"][0], "title": "Different case", "version": second_version}
    )
    with pytest.raises(ArtifactValidationError, match="unique case_id"):
        validate_artifact(DesignModel, payload)


def test_design_rejects_duplicate_test_point_ids() -> None:
    payload = make_design_payload()
    payload["test_points"].append(
        {**payload["test_points"][0], "description": "Different test point"}
    )
    with pytest.raises(ArtifactValidationError, match="unique test_point_id"):
        validate_artifact(DesignModel, payload)


def test_design_accepts_distinct_ids_and_reuses_them_in_new_artifact_versions() -> None:
    payload = make_design_payload()
    payload["test_points"].append(
        {**payload["test_points"][0], "test_point_id": "TP-002"}
    )
    payload["test_cases"].append(
        {**payload["test_cases"][0], "case_id": "TC-002", "test_point_refs": ["TP-002"]}
    )
    first = validate_artifact(DesignModel, payload)
    payload["meta"]["version"] = 2
    second = validate_artifact(DesignModel, payload)

    assert [case.case_id for case in second.test_cases] == ["TC-001", "TC-002"]
    assert [point.test_point_id for point in second.test_points] == ["TP-001", "TP-002"]
    assert first.meta.version == 1
    assert second.meta.version == 2
