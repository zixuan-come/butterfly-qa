"""Pydantic models for workflow artifacts exchanged by agents."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"


class ReviewDecision(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_HUMAN_DECISION = "needs_human_decision"


class ExecutionResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ArtifactMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
    source_artifacts: list[str] = Field(default_factory=list)
    created_by: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


class ApprovalType(str, Enum):
    REQUIREMENT_CLARIFICATION = "requirement_clarification"
    TESTCASE_APPROVAL = "testcase_approval"
    RISK_ACCEPTANCE = "risk_acceptance"
    REPORT_APPROVAL = "report_approval"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class HumanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    approval_type: ApprovalType
    target_artifact_id: str = Field(min_length=1)
    target_artifact_type: str = Field(min_length=1)
    target_artifact_version: int = Field(ge=1)
    decision: ApprovalDecision
    decided_by: str = Field(min_length=1)
    decided_at: datetime
    comment: str = ""

    @model_validator(mode="after")
    def require_comment_for_non_approval(self) -> "HumanApproval":
        if self.decision is not ApprovalDecision.APPROVED and not self.comment.strip():
            raise ValueError("comment is required when approval is not approved")
        return self


class RequirementReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    issue_type: str = Field(min_length=1)
    severity: Literal["blocker", "high", "medium", "low"]
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    needs_product_confirmation: bool = False


class RequirementReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    decision: ReviewDecision
    issues: list[RequirementReviewIssue] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class RequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    actors: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class RequirementAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    requirements: list[RequirementItem] = Field(min_length=1)
    flows: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class TestPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_point_id: str = Field(min_length=1)
    requirement_refs: list[str] = Field(min_length=1)
    category: Literal[
        "normal",
        "abnormal",
        "boundary",
        "permission",
        "state",
        "data",
        "compatibility",
        "other",
    ]
    description: str = Field(min_length=1)
    risk: Literal["high", "medium", "low"] = "medium"


class TestStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_no: int = Field(ge=1)
    action: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    requirement_refs: list[str] = Field(min_length=1)
    test_point_refs: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    priority: Literal["P0", "P1", "P2", "P3"]
    preconditions: list[str] = Field(default_factory=list)
    test_data: list[str] = Field(default_factory=list)
    steps: list[TestStep] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class TestDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    test_points: list[TestPoint] = Field(min_length=1)
    test_cases: list[TestCase] = Field(min_length=1)


class TestCaseReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    severity: Literal["blocker", "high", "medium", "low"]
    issue_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)


class TestCaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    decision: ReviewDecision
    issues: list[TestCaseReviewIssue] = Field(default_factory=list)
    coverage_summary: str = Field(min_length=1)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    evidence_type: Literal["screenshot", "log", "video", "file", "other"]
    path: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    environment: str = Field(min_length=1)
    executed_by: str = Field(min_length=1)
    executed_at: datetime
    result: ExecutionResult
    actual_result: str = Field(min_length=1)
    defect_refs: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ArtifactMeta
    scope: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    total_cases: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    skipped: int = Field(ge=0)
    defect_refs: list[str] = Field(default_factory=list)
    risk_summary: list[str] = Field(default_factory=list)
    conclusion: str = Field(min_length=1)
    trace_refs: dict[str, list[str]] = Field(default_factory=dict)
