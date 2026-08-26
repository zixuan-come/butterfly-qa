"""Request and response contracts between the Python harness and Codex agents."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflow.models import ArtifactPointer, InputFilePointer
from .workflow.states import WorkflowState


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


class WorkflowActionType(str, Enum):
    """Actions a main-flow agent may suggest to the harness."""

    INVOKE_AGENT = "invoke_agent"
    WAIT_HUMAN = "wait_human"
    TRANSITION = "transition"
    MANUAL_INTERVENTION = "manual_intervention"


class WorkflowAction(BaseModel):
    """Validated routing advice returned by the main-flow agent."""

    model_config = ConfigDict(extra="forbid")

    action: WorkflowActionType
    target_role: AgentRole | None = None
    skill_name: str | None = Field(default=None, min_length=1)
    target_state: WorkflowState | None = None
    reason: str = Field(min_length=1)
    input_artifact_refs: list[str] = Field(default_factory=list)
    expected_output_type: str | None = Field(default=None, min_length=1)
    human_question: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "WorkflowAction":
        if self.action is WorkflowActionType.INVOKE_AGENT:
            if self.target_role is None:
                raise ValueError("invoke_agent requires target_role")
            if not self.skill_name:
                raise ValueError("invoke_agent requires skill_name")
            if self.target_state is None:
                raise ValueError("invoke_agent requires target_state")
            if not self.expected_output_type:
                raise ValueError("invoke_agent requires expected_output_type")
        elif self.action is WorkflowActionType.TRANSITION:
            if self.target_state is None:
                raise ValueError("transition requires target_state")
        elif self.action is WorkflowActionType.WAIT_HUMAN:
            if not self.human_question:
                raise ValueError("wait_human requires human_question")
        elif self.action is WorkflowActionType.MANUAL_INTERVENTION:
            if not self.human_question:
                raise ValueError("manual_intervention requires human_question")
        return self


class AgentRequest(BaseModel):
    """Input prepared by the harness before an agent starts working."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    role: AgentRole
    task_name: str = Field(min_length=1)
    skill_name: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    input_files: list[InputFilePointer] = Field(default_factory=list)
    input_artifacts: list[ArtifactPointer] = Field(default_factory=list)
    created_at: datetime
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    model: str | None = Field(default=None, min_length=1)
    sandbox_mode: Literal["read_only"] = "read_only"
    working_directory: str | None = Field(default=None, min_length=1)
    agent_contract_path: str | None = Field(default=None, min_length=1)
    skill_path: str | None = Field(default=None, min_length=1)


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
        if self.status in {AgentStatus.FAILED, AgentStatus.NEEDS_HUMAN}:
            if not self.error_type or not self.error_message:
                raise ValueError(
                    "error_type and error_message are required for non-success responses"
                )
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")
        return self
