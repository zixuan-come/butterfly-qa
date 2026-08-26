from datetime import datetime, timezone
from time import sleep

from qa_agent.agent_protocol import (
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
)
from qa_agent.agent_runner import AgentRunner
from qa_agent.audit import AgentAuditStore, ResilientAgentRunner
from qa_agent.storage import ArtifactStore


def make_request(**overrides):
    values = {
        "request_id": "req-001",
        "project_id": "demo-project",
        "role": AgentRole.TEST_ANALYSIS_DESIGN,
        "task_name": "需求评审",
        "prompt": "执行需求评审",
        "created_at": datetime.now(timezone.utc),
        "timeout_seconds": 1,
    }
    values.update(overrides)
    return AgentRequest(**values)


class SequenceRunner(AgentRunner):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def run(self, request):
        self.calls += 1
        result = next(self.responses)
        now = datetime.now(timezone.utc)
        if isinstance(result, AgentResponse):
            return result
        if isinstance(result, Exception):
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.FAILED,
                error_type=type(result).__name__,
                error_message=str(result),
                started_at=now,
                completed_at=now,
            )
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text=result,
            started_at=now,
            completed_at=now,
        )


def test_success_is_audited(tmp_path):
    store = ArtifactStore(tmp_path)
    runner = ResilientAgentRunner(
        SequenceRunner(["ok"]),
        AgentAuditStore(store),
    )

    response = runner.run(make_request())

    assert response.status is AgentStatus.SUCCEEDED
    runs = list((store.project_root("demo-project") / "agent-runs").glob("*.json"))
    assert len(runs) == 1
    audit = AgentAuditStore(store).load("demo-project", runs[0].stem)
    assert audit.attempt == 1
    assert audit.response_payload["status"] == "succeeded"


def test_failed_attempt_is_retried_and_then_succeeds(tmp_path):
    store = ArtifactStore(tmp_path)
    inner = SequenceRunner([RuntimeError("temporary"), "ok"])
    runner = ResilientAgentRunner(
        inner,
        AgentAuditStore(store),
        max_attempts=2,
    )

    response = runner.run(make_request())

    assert response.status is AgentStatus.SUCCEEDED
    assert inner.calls == 2
    runs = list((store.project_root("demo-project") / "agent-runs").glob("*.json"))
    assert len(runs) == 2


def test_exhausted_retries_require_human_and_are_audited(tmp_path):
    store = ArtifactStore(tmp_path)
    runner = ResilientAgentRunner(
        SequenceRunner([RuntimeError("broken"), RuntimeError("still broken")]),
        AgentAuditStore(store),
        max_attempts=2,
    )

    response = runner.run(make_request())

    assert response.status is AgentStatus.NEEDS_HUMAN
    assert response.error_type == "retry_exhausted"
    runs = list((store.project_root("demo-project") / "agent-runs").glob("*.json"))
    assert len(runs) == 3
    statuses = sorted(
        AgentAuditStore(store).load("demo-project", path.stem).status.value
        for path in runs
    )
    assert statuses == ["failed", "failed", "needs_human"]


def test_non_retryable_schema_error_stops_after_one_attempt(tmp_path):
    store = ArtifactStore(tmp_path)
    now = datetime.now(timezone.utc)
    response = AgentResponse(
        request_id="req-001",
        role=AgentRole.TEST_ANALYSIS_DESIGN,
        status=AgentStatus.FAILED,
        error_type="invalid_json_schema",
        error_message="schema rejected",
        started_at=now,
        completed_at=now,
    )
    inner = SequenceRunner([response])
    runner = ResilientAgentRunner(
        inner,
        AgentAuditStore(store),
        max_attempts=3,
    )

    result = runner.run(make_request())

    assert result.status is AgentStatus.NEEDS_HUMAN
    assert inner.calls == 1
    assert "after 1 attempt" in result.error_message


class SlowRunner(AgentRunner):
    def run(self, request):
        sleep(1.05)
        now = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=request.role,
            status=AgentStatus.SUCCEEDED,
            output_text="late",
            started_at=now,
            completed_at=now,
        )


def test_timeout_is_normalized_and_can_be_retried(tmp_path):
    store = ArtifactStore(tmp_path)
    runner = ResilientAgentRunner(
        SlowRunner(),
        AgentAuditStore(store),
        max_attempts=1,
    )

    response = runner.run(make_request(timeout_seconds=1))

    assert response.status is AgentStatus.NEEDS_HUMAN
    assert "exceeded 1 seconds" in response.error_message
    runs = list((store.project_root("demo-project") / "agent-runs").glob("*.json"))
    assert len(runs) == 2
