"""Agent runner abstractions used by the Python harness."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone

from .agent_protocol import AgentRequest, AgentResponse, AgentStatus


class AgentRunner(ABC):
    """Backend-neutral interface for invoking a Codex agent."""

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResponse:
        """Run one request and return a normalized response."""


class StubAgentRunner(AgentRunner):
    """Deterministic runner for tests and local workflow development."""

    def __init__(
        self,
        handler: Callable[[AgentRequest], str | AgentResponse],
    ) -> None:
        self.handler = handler

    def run(self, request: AgentRequest) -> AgentResponse:
        started_at = datetime.now(timezone.utc)

        try:
            result = self.handler(request)
        except Exception as exc:  # noqa: BLE001 - normalize backend failures
            completed_at = datetime.now(timezone.utc)
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.FAILED,
                error_type=type(exc).__name__,
                error_message=str(exc) or "agent handler failed",
                started_at=started_at,
                completed_at=completed_at,
            )

        if isinstance(result, AgentResponse):
            if result.request_id != request.request_id:
                raise ValueError("agent response request_id does not match request")
            if result.role is not request.role:
                raise ValueError("agent response role does not match request")
            return result

        completed_at = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=result,
            started_at=started_at,
            completed_at=completed_at,
        )
