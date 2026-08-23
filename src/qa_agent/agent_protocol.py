"""Request and response contracts between the Python harness and Codex agents."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflow.models import ArtifactPointer


class AgentRole(str, Enum):
    """Business roles available in the Butterfly QA workflow."""

    MAIN_FLOW = "main_flow"
    TEST_ANALYSIS_DESIGN = "test_analysis_design"
    TESTCASE_REVIEW = "testcase_review"


class AgentStatus(str, Enum):
    """Normalized outcome of one agent invocation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"


class AgentRequest(BaseModel):
    """Input prepared by the harness before an agent starts working."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    role: AgentRole
    task_name: str = Field(min_length=1)
    skill_name: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    input_artifacts: list[ArtifactPointer] = Field(default_factory=list)
    created_at: datetime
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class AgentResponse(BaseModel):
    """Normalized result returned to the harness after an agent invocation."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    role: AgentRole
    status: AgentStatus
    output_text: str = ""
    error_type: str | None = Field(default=None, min_length=1)
    error_message: str | None = Field(default=None, min_length=1)
    thread_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_outcome(self) -> "AgentResponse":
        if self.status is AgentStatus.SUCCEEDED and not self.output_text.strip():
            raise ValueError("output_text is required when agent succeeds")
        if self.status is AgentStatus.FAILED:
            if not self.error_type or not self.error_message:
                raise ValueError(
                    "error_type and error_message are required when agent fails"
                )
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")
        return self
