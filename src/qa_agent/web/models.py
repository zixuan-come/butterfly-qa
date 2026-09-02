"""Request and response models shared by Butterfly Agent HTTP endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas import ApprovalDecision, ApprovalType, ExecutionRecord


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Stable response envelope used by every JSON endpoint."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    data: T | None = None
    request_id: str = Field(min_length=1)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    created_by: str = Field(min_length=1, max_length=120)


class UpdateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)

class CreateFeatureModuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    created_by: str = Field(min_length=1, max_length=120)


class UpdateFeatureModuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class DeletionData(BaseModel):
    resource_type: Literal["project", "feature_module"]
    resource_id: str

class FeatureModuleSummary(BaseModel):
    module_id: str
    project_id: str
    name: str
    state: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    input_count: int = Field(ge=0)
    active_artifact_count: int = Field(ge=0)


class FeatureModuleDetail(FeatureModuleSummary):
    workflow_id: str
    active_artifacts: dict[str, dict[str, Any]]
    revision_rounds: dict[str, int]


class FeatureModuleListData(BaseModel):
    items: list[FeatureModuleSummary]
    total: int = Field(ge=0)


class HealthData(BaseModel):
    status: str
    service: str
    version: str


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    state: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    input_count: int = Field(ge=0)
    active_artifact_count: int = Field(ge=0)


class ProjectDetail(ProjectSummary):
    workflow_id: str
    active_artifacts: dict[str, dict[str, Any]]
    revision_rounds: dict[str, int]


class ProjectListData(BaseModel):
    items: list[ProjectSummary]
    total: int = Field(ge=0)


class ValidationErrorData(BaseModel):
    errors: list[dict[str, Any]]


class ProjectInputData(BaseModel):
    input_id: str
    category: str
    original_name: str
    relative_path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    imported_by: str
    imported_at: datetime


class ProjectInputPreviewData(BaseModel):
    input: ProjectInputData
    preview_kind: Literal["text", "image", "pdf", "unsupported"]
    content: str | None
    content_url: str
    truncated: bool = False


class WorkflowStatusData(BaseModel):
    project_id: str
    module_id: str | None = None
    workflow_id: str
    state: str
    awaiting_human: bool
    available_states: list[str]
    input_files: list[dict[str, Any]]
    current_requirement_input_id: str | None = None
    active_artifacts: dict[str, dict[str, Any]]
    transition_history: list[dict[str, Any]]
    revision_rounds: dict[str, int]
    updated_at: datetime


class RunWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=120)


class AgentRunSummary(BaseModel):
    role: str
    status: str
    error_type: str | None = None
    error_message: str | None = None


class WorkflowStepData(BaseModel):
    state: str
    action: dict[str, Any] | None
    artifact_path: str | None
    markdown_path: str | None
    thread_id: str | None
    agent: AgentRunSummary
    transition: dict[str, Any] | None
    error: str | None


class SubmitApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_type: ApprovalType
    decision: ApprovalDecision
    decided_by: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_comment_for_non_approval(self) -> "SubmitApprovalRequest":
        if (
            self.decision is not ApprovalDecision.APPROVED
            or self.approval_type is ApprovalType.RISK_ACCEPTANCE
        ) and not self.comment.strip():
            raise ValueError(
                "comment is required for non-approval decisions and risk acceptance"
            )
        return self


class ApprovalData(BaseModel):
    approval_id: str
    approval_type: ApprovalType
    decision: ApprovalDecision
    target_artifact: dict[str, Any]
    state: str
    transition: dict[str, Any]


class SubmitExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submitted_by: str = Field(min_length=1, max_length=120)
    records: list[ExecutionRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_results(self) -> "SubmitExecutionRequest":
        case_refs = [(record.case_id, record.case_version) for record in self.records]
        if len(case_refs) != len(set(case_refs)):
            raise ValueError("records must contain one result per case version")
        return self


class ExecutionData(BaseModel):
    execution_id: str
    artifact_path: str
    state: str
    transition: dict[str, Any]


class EvidenceData(BaseModel):
    evidence_id: str
    evidence_type: str
    path: str
    description: str
    sha256: str | None
    size_bytes: int | None = Field(default=None, ge=0)
    media_type: str | None


class ActiveArtifactData(BaseModel):
    artifact_id: str
    artifact_type: str
    version: int = Field(ge=1)
    content: dict[str, Any]
    markdown: str | None
    markdown_path: str | None
