"""Persistent models for a project's workflow run."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .states import WorkflowState


class ArtifactPointer(BaseModel):
    """Reference a specific version of an artifact used by the workflow."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    version: int = Field(ge=1)


class WorkflowTransition(BaseModel):
    """Audit record describing one completed state transition."""

    model_config = ConfigDict(extra="forbid")

    from_state: WorkflowState
    to_state: WorkflowState
    triggered_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    occurred_at: datetime
    related_artifacts: list[ArtifactPointer] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    """Recoverable workflow state for one test project."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    current_state: WorkflowState = WorkflowState.REQUIREMENT_RECEIVED
    active_artifacts: dict[str, ArtifactPointer] = Field(default_factory=dict)
    transition_history: list[WorkflowTransition] = Field(default_factory=list)

    requirement_revision_rounds: int = Field(default=0, ge=0)
    testcase_revision_rounds: int = Field(default=0, ge=0)
    report_revision_rounds: int = Field(default=0, ge=0)

    manual_resume_state: WorkflowState | None = None
    created_at: datetime
    updated_at: datetime
