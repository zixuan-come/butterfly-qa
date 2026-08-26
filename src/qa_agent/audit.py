"""Auditable Agent invocation records and bounded retry execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from time import sleep
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent_protocol import AgentRequest, AgentResponse, AgentRole, AgentStatus
from .agent_runner import AgentRunner
from .storage import ArtifactStore


class AgentInvocationAudit(BaseModel):
    """Immutable record of one actual Agent attempt."""

    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    role: AgentRole
    attempt: int = Field(ge=1)
    status: AgentStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    thread_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    request_payload: dict = Field(default_factory=dict)
    response_payload: dict = Field(default_factory=dict)


class AgentAuditStore:
    """Persist invocation records without overwriting previous attempts."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def save(self, record: AgentInvocationAudit):
        return self.store.save_agent_run(
            record.project_id,
            record.invocation_id,
            record,
        )

    def load(self, project_id: str, invocation_id: str) -> AgentInvocationAudit:
        return AgentInvocationAudit.model_validate(
            self.store.load_agent_run(project_id, invocation_id)
        )


class ResilientAgentRunner(AgentRunner):
    """Add timeout, bounded retry and audit behavior around any AgentRunner."""

    _NON_RETRYABLE_ERRORS = {"invalid_json_schema"}

    def __init__(
        self,
        inner: AgentRunner,
        audit_store: AgentAuditStore,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.0,
        retry_timeouts: bool = False,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        self.inner = inner
        self.audit_store = audit_store
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.retry_timeouts = retry_timeouts
        self.sleep_fn = sleep_fn

    def run(self, request: AgentRequest) -> AgentResponse:
        last_response: AgentResponse | None = None
        attempts_made = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts_made = attempt
            response = self._run_with_timeout(request)
            self._audit(request, response, attempt)
            last_response = response
            if response.status is AgentStatus.SUCCEEDED:
                return response
            if response.status is AgentStatus.NEEDS_HUMAN:
                return response
            if response.error_type in self._NON_RETRYABLE_ERRORS:
                break
            if response.error_type == "timeout" and not self.retry_timeouts:
                break
            if attempt < self.max_attempts:
                self.sleep_fn(self.retry_delay_seconds)

        assert last_response is not None
        completed_at = datetime.now(timezone.utc)
        needs_human = AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.NEEDS_HUMAN,
            error_type="retry_exhausted",
            error_message=(
                f"Agent failed after {self.max_attempts} attempts: "
                if attempts_made == self.max_attempts
                else f"Agent failed after {attempts_made} attempt: "
            ) + (last_response.error_message or "unknown error"),
            thread_id=last_response.thread_id,
            model=last_response.model,
            started_at=last_response.started_at,
            completed_at=completed_at,
        )
        self._audit(request, needs_human, attempts_made + 1)
        return needs_human

    def _run_with_timeout(self, request: AgentRequest) -> AgentResponse:
        started_at = datetime.now(timezone.utc)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.inner.run, request)
        try:
            return future.result(timeout=request.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            completed_at = datetime.now(timezone.utc)
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.FAILED,
                error_type="timeout",
                error_message=(
                    f"Agent invocation exceeded {request.timeout_seconds} seconds"
                ),
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as exc:  # noqa: BLE001 - normalize runner failures
            completed_at = datetime.now(timezone.utc)
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.FAILED,
                error_type=type(exc).__name__,
                error_message=str(exc) or "agent runner failed",
                started_at=started_at,
                completed_at=completed_at,
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _audit(
        self,
        request: AgentRequest,
        response: AgentResponse,
        attempt: int,
    ) -> None:
        duration_ms = max(
            0,
            int((response.completed_at - response.started_at).total_seconds() * 1000),
        )
        record = AgentInvocationAudit(
            invocation_id=f"invoke-{uuid4().hex}",
            request_id=request.request_id,
            project_id=request.project_id,
            role=request.role,
            attempt=attempt,
            status=response.status,
            started_at=response.started_at,
            completed_at=response.completed_at,
            duration_ms=duration_ms,
            thread_id=response.thread_id,
            model=response.model or request.model,
            request_payload=request.model_dump(mode="json"),
            response_payload=response.model_dump(mode="json"),
        )
        self.audit_store.save(record)
