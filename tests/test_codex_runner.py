from datetime import datetime, timezone
from types import SimpleNamespace

from qa_agent.agent_protocol import AgentRequest, AgentRole, AgentStatus
from qa_agent.codex_runner import CodexAgentRunner
from qa_agent.workflow.models import ArtifactPointer, InputFilePointer


def schema_contains_key(value, target):
    if isinstance(value, dict):
        return target in value or any(
            schema_contains_key(item, target) for item in value.values()
        )
    if isinstance(value, list):
        return any(schema_contains_key(item, target) for item in value)
    return False


def make_request(**overrides):
    values = {
        "request_id": "req-001",
        "project_id": "demo-project",
        "role": AgentRole.TEST_ANALYSIS_DESIGN,
        "task_name": "需求评审",
        "skill_name": "requirement-review",
        "prompt": "评审修改收货地址需求，并输出结构化结果。",
        "created_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return AgentRequest(**values)


class FakeThread:
    id = "thread-001"

    def __init__(self, result):
        self.result = result
        self.inputs = []

    def run(self, prompt, **kwargs):
        self.inputs.append((prompt, kwargs))
        return self.result


class FakeCodex:
    def __init__(self, thread):
        self.thread = thread
        self.thread_start_kwargs = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def thread_start(self, **kwargs):
        self.thread_start_kwargs = kwargs
        return self.thread


def test_codex_runner_builds_prompt_and_normalizes_success(tmp_path):
    thread = FakeThread(SimpleNamespace(final_response='{"decision":"pass"}', status="completed"))
    client = FakeCodex(thread)
    runner = CodexAgentRunner(tmp_path, codex_factory=lambda: client)

    contract = tmp_path / "agents" / "test-analysis-design-agent.md"
    skill = tmp_path / "skills" / "requirement-review" / "SKILL.md"
    contract.parent.mkdir()
    skill.parent.mkdir(parents=True)
    contract.write_text("agent contract", encoding="utf-8")
    skill.write_text("skill instructions", encoding="utf-8")

    response = runner.run(
        make_request(
            input_files=[
                InputFilePointer(
                    input_id="requirement-001",
                    category="requirement",
                    relative_path="input/requirement-001.md",
                    sha256="a" * 64,
                )
            ]
        )
    )

    assert response.status is AgentStatus.SUCCEEDED
    assert response.output_text == '{"decision":"pass"}'
    assert response.thread_id == "thread-001"
    assert "agent contract" in thread.inputs[0][0]
    assert "skill instructions" in thread.inputs[0][0]
    assert "input/requirement-001.md" in thread.inputs[0][0]
    assert "机器校验约束" in thread.inputs[0][0]
    assert "原文行号定位规则" in thread.inputs[0][0]
    assert "冲突问题使用" in thread.inputs[0][0]
    assert "空行也计入行号" in thread.inputs[0][0]
    output_schema = thread.inputs[0][1]["output_schema"]
    assert "RequirementReviewIssue" in output_schema["$defs"]
    assert set(output_schema["required"]) == set(output_schema["properties"])
    assert set(output_schema["$defs"]["RequirementReviewIssue"]["required"]) == set(
        output_schema["$defs"]["RequirementReviewIssue"]["properties"]
    )
    assert not schema_contains_key(output_schema, "default")
    assert client.thread_start_kwargs["sandbox"].value == "read-only"


def test_codex_runner_includes_workflow_action_schema_for_main_flow(tmp_path):
    thread = FakeThread(SimpleNamespace(final_response='{"action":"wait_human"}', status="completed"))
    runner = CodexAgentRunner(tmp_path, codex_factory=lambda: FakeCodex(thread))
    contract = tmp_path / "agents" / "main-flow-agent.md"
    contract.parent.mkdir()
    contract.write_text("main contract", encoding="utf-8")

    response = runner.run(
        make_request(
            role=AgentRole.MAIN_FLOW,
            skill_name=None,
            task_name="route:requirement_received",
        )
    )

    assert response.status is AgentStatus.SUCCEEDED
    schema = thread.inputs[0][1]["output_schema"]
    assert "WorkflowActionType" in schema["$defs"]
    assert set(schema["required"]) == set(schema["properties"])


def test_codex_runner_normalizes_sdk_failure(tmp_path):
    class BrokenCodex:
        def __enter__(self):
            raise RuntimeError("runtime unavailable")

        def __exit__(self, *_args):
            return None

    runner = CodexAgentRunner(tmp_path, codex_factory=BrokenCodex)
    contract = tmp_path / "agents" / "test-analysis-design-agent.md"
    skill = tmp_path / "skills" / "requirement-review" / "SKILL.md"
    contract.parent.mkdir()
    skill.parent.mkdir(parents=True)
    contract.write_text("agent contract", encoding="utf-8")
    skill.write_text("skill instructions", encoding="utf-8")

    response = runner.run(make_request())

    assert response.status is AgentStatus.FAILED
    assert response.error_type == "RuntimeError"
    assert "runtime unavailable" in response.error_message


def test_codex_runner_classifies_invalid_output_schema_as_non_retryable(tmp_path):
    class BrokenCodex:
        def __enter__(self):
            raise RuntimeError('{"code":"invalid_json_schema"}')

        def __exit__(self, *_args):
            return None

    runner = CodexAgentRunner(tmp_path, codex_factory=BrokenCodex)
    contract = tmp_path / "agents" / "main-flow-agent.md"
    contract.parent.mkdir()
    contract.write_text("main contract", encoding="utf-8")

    response = runner.run(
        make_request(role=AgentRole.MAIN_FLOW, skill_name=None)
    )

    assert response.error_type == "invalid_json_schema"


def test_codex_runner_rejects_input_outside_project_root(tmp_path):
    runner = CodexAgentRunner(tmp_path, codex_factory=lambda: None)

    request = make_request(
        agent_contract_path=str(tmp_path.parent / "outside.md"),
    )

    response = runner.run(request)

    assert response.status is AgentStatus.FAILED
    assert response.error_type == "FileNotFoundError"


def test_codex_runner_rejects_working_directory_outside_project_root(tmp_path):
    thread = FakeThread(SimpleNamespace(final_response="{}", status="completed"))
    runner = CodexAgentRunner(tmp_path, codex_factory=lambda: FakeCodex(thread))
    contract = tmp_path / "agents" / "test-analysis-design-agent.md"
    skill = tmp_path / "skills" / "requirement-review" / "SKILL.md"
    contract.parent.mkdir()
    skill.parent.mkdir(parents=True)
    contract.write_text("agent contract", encoding="utf-8")
    skill.write_text("skill instructions", encoding="utf-8")

    response = runner.run(make_request(working_directory=str(tmp_path.parent)))

    assert response.status is AgentStatus.FAILED
    assert response.error_type == "ValueError"
    assert "outside project root" in response.error_message


def test_requirement_analysis_prompt_declares_current_requirement_scope(tmp_path):
    runner = CodexAgentRunner(tmp_path, codex_factory=lambda: None)
    contract = tmp_path / "agents" / "test-analysis-design-agent.md"
    skill = tmp_path / "skills" / "requirement-analysis" / "SKILL.md"
    contract.parent.mkdir()
    skill.parent.mkdir(parents=True)
    contract.write_text("agent contract", encoding="utf-8")
    skill.write_text("skill instructions", encoding="utf-8")

    prompt = runner.build_prompt(
        make_request(
            skill_name="requirement-analysis",
            task_name="requirement-analysis",
            input_files=[
                InputFilePointer(
                    input_id="requirement-v2",
                    category="requirement",
                    relative_path="input/requirement-v2.md",
                    sha256="b" * 64,
                )
            ],
            input_artifacts=[
                ArtifactPointer(
                    artifact_id="review-v2",
                    artifact_type="requirement_review",
                    version=2,
                )
            ],
        )
    )

    assert "需求版本约束" in prompt
    assert "当前有效需求版本" in prompt
    assert "历史 requirement 文件" in prompt
    assert "requirement_review" in prompt
