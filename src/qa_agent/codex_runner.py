"""Codex SDK adapter for the Butterfly QA agent protocol."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .agent_protocol import AgentRequest, AgentResponse, AgentStatus, WorkflowAction
from .agent_runner import AgentRunner
from .schemas import (
    RequirementAnalysis,
    RequirementReview,
    TestCaseReview,
    TestDesign,
    TestReport,
)


class CodexAgentRunner(AgentRunner):
    """Invoke one isolated, read-only Codex thread for each request.

    The SDK client factory is injectable so protocol and prompt assembly can be
    tested without starting a Codex runtime or spending model tokens.
    """

    _ROLE_CONTRACTS = {
        "main_flow": "agents/main-flow-agent.md",
        "test_analysis_design": "agents/test-analysis-design-agent.md",
        "testcase_review": "agents/testcase-review-agent.md",
    }
    _OUTPUT_MODELS: dict[str, type[BaseModel]] = {
        "requirement-review": RequirementReview,
        "requirement-analysis": RequirementAnalysis,
        "testcase-design": TestDesign,
        "testcase-evaluation": TestCaseReview,
        "test-report": TestReport,
    }

    def __init__(
        self,
        project_root: str | Path,
        *,
        codex_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._codex_factory = codex_factory

    def run(self, request: AgentRequest) -> AgentResponse:
        started_at = datetime.now(timezone.utc)

        try:
            prompt = self.build_prompt(request)
            working_directory = self._resolve_working_directory(
                request.working_directory
            )
            codex = self._new_codex()
            with codex:
                thread = codex.thread_start(
                    model=request.model,
                    cwd=str(working_directory),
                    sandbox=self._sandbox_read_only(),
                    developer_instructions=(
                        "你是 Butterfly QA 的一个受控专业 Agent。"
                        "只输出当前任务要求的结果，不要修改项目文件，不要推进工作流状态。"
                    ),
                )
                result = thread.run(
                    prompt,
                    model=request.model,
                    cwd=str(working_directory),
                    output_schema=self.output_schema(request),
                    sandbox=self._sandbox_read_only(),
                )

            output_text = (result.final_response or "").strip()
            if not output_text:
                raise RuntimeError(
                    f"Codex turn completed without final_response (status={result.status})"
                )

            completed_at = datetime.now(timezone.utc)
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.SUCCEEDED,
                output_text=output_text,
                thread_id=thread.id,
                model=request.model,
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK failures
            completed_at = datetime.now(timezone.utc)
            message = str(exc) or "Codex agent invocation failed"
            return AgentResponse(
                request_id=request.request_id,
                role=request.role,
                status=AgentStatus.FAILED,
                error_type=(
                    "invalid_json_schema"
                    if "invalid_json_schema" in message
                    else type(exc).__name__
                ),
                error_message=message,
                model=request.model,
                started_at=started_at,
                completed_at=completed_at,
            )

    def build_prompt(self, request: AgentRequest) -> str:
        """Build a bounded prompt from the role contract, Skill and task input."""

        contract_path = self._resolve_input_path(
            request.agent_contract_path
            or self._ROLE_CONTRACTS[request.role.value]
        )
        skill_path = None
        if request.skill_path:
            skill_path = self._resolve_input_path(request.skill_path)
        elif request.skill_name:
            skill_path = self._resolve_input_path(
                Path("skills") / request.skill_name / "SKILL.md"
            )

        sections = [
            "# Agent 职责契约\n\n" + contract_path.read_text(encoding="utf-8"),
        ]
        if skill_path:
            sections.append("# 当前 Skill\n\n" + skill_path.read_text(encoding="utf-8"))

        sections.append(
            "# 机器校验约束\n\n"
            "本次调用已通过 SDK 配置原生 JSON Schema。字段名、字段类型和枚举值必须严格一致。"
            "不要添加 Schema 未定义的字段，不要使用 Markdown 代码块，也不要在 JSON 前后添加解释。"
        )

        artifact_lines = "\n".join(
            f"- {item.artifact_id} ({item.artifact_type}) v{item.version}: "
            f"artifacts/{item.artifact_type}/{item.artifact_id}/v{item.version}.json"
            for item in request.input_artifacts
        ) or "- 无结构化输入产物"
        input_file_lines = "\n".join(
            f"- {item.input_id} ({item.category}): {item.relative_path} "
            f"[sha256={item.sha256}]"
            for item in request.input_files
        ) or "- 无原始输入文件"
        sections.append(
            "# 本次任务\n"
            f"request_id: {request.request_id}\n"
            f"project_id: {request.project_id}\n"
            f"task_name: {request.task_name}\n"
            f"原始输入文件（路径相对于工作目录）:\n{input_file_lines}\n"
            f"输入产物:\n{artifact_lines}\n\n"
            "任务要求:\n"
            f"{request.prompt}\n\n"
            "# 输出约束\n"
            "请优先输出符合对应 Schema 的 JSON。不要声称已经执行未执行的测试，"
            "不要修改文件，不要把未确认内容写成事实。"
        )
        return "\n\n".join(sections)

    def output_schema(self, request: AgentRequest) -> dict[str, Any]:
        """Return the native Codex output schema for one bounded task."""

        model = (
            self._OUTPUT_MODELS.get(request.skill_name or "")
            if request.skill_name
            else WorkflowAction
        )
        if model is None:
            raise ValueError(f"no output schema configured for task: {request.task_name}")
        return _strict_json_schema(model.model_json_schema())

    def _resolve_input_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"agent input path is outside project root: {resolved}") from exc
        if not resolved.is_file():
            raise ValueError(f"agent input path is not a file: {resolved}")
        return resolved

    def _resolve_working_directory(self, path: str | None) -> Path:
        resolved = Path(path or self.project_root).resolve(strict=True)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(
                f"working_directory is outside project root: {resolved}"
            ) from exc
        if not resolved.is_dir():
            raise ValueError(f"working_directory is not a directory: {resolved}")
        return resolved

    def _new_codex(self) -> Any:
        if self._codex_factory is not None:
            return self._codex_factory()
        try:
            from openai_codex import Codex
        except ImportError as exc:
            raise RuntimeError(
                "openai-codex is not installed; install the project dependencies first"
            ) from exc
        return Codex()

    @staticmethod
    def _sandbox_read_only() -> Any:
        try:
            from openai_codex import Sandbox
        except ImportError as exc:
            raise RuntimeError("openai-codex is required for CodexAgentRunner") from exc
        return Sandbox.read_only


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic JSON Schema to Codex's strict response-schema rules."""

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node

        result = {
            key: visit(value)
            for key, value in node.items()
            if key != "default"
        }
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties", {})
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result

    return visit(schema)
