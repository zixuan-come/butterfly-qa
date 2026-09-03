"""Main-flow orchestration for the Butterfly Agent workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .agent_protocol import (
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    WorkflowAction,
    WorkflowActionType,
)
from .agent_runner import AgentRunner
from .reporting import TestReportService
from .schemas import (
    RequirementAnalysis,
    RequirementReview,
    TestCaseReview,
    TestDesign,
    TestReport,
)
from .storage.artifact_store import ArtifactStore
from .test_design_rendering import render_test_design
from .validation.artifact_validator import validate_artifact
from .workflow.models import ArtifactPointer, InputFilePointer, WorkflowRun, WorkflowTransition
from .workflow.state_machine import WorkflowStateMachine
from .workflow.states import WorkflowState


class OrchestrationError(ValueError):
    """Raised when a main-flow action cannot be safely executed."""


@dataclass(frozen=True)
class OrchestrationResult:
    """Outcome of one bounded orchestration step."""

    main_response: AgentResponse
    action: WorkflowAction | None = None
    specialist_response: AgentResponse | None = None
    artifact_path: Path | None = None
    markdown_path: Path | None = None
    transition: WorkflowTransition | None = None
    error: str | None = None


class WorkflowOrchestrator:
    """Ask the main-flow agent for one action and execute it through the Harness."""

    _SKILL_ALIASES: dict[str, str] = {
        # Keep old prompts/workflow records compatible with the canonical directory name.
        "testcase-review": "testcase-evaluation",
    }

    _ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
        "requirement_review": RequirementReview,
        "requirement_analysis": RequirementAnalysis,
        "test_design": TestDesign,
        "testcase_review": TestCaseReview,
        "test_report": TestReport,
    }

    def __init__(
        self,
        workflow: WorkflowRun,
        runner: AgentRunner,
        *,
        artifact_store: ArtifactStore | None = None,
        project_root: str | Path | None = None,
        model: str | None = None,
    ) -> None:
        self.workflow = workflow
        self.runner = runner
        self.artifact_store = artifact_store
        self.project_root = Path(project_root).resolve() if project_root else None
        self.model = model

    def step(self, *, trigger: str = "main-flow-harness") -> OrchestrationResult:
        """Execute one main-flow decision and at most one specialist invocation."""

        main_request = self._main_request()
        main_response = self.runner.run(main_request)
        if main_response.status is not AgentStatus.SUCCEEDED:
            return OrchestrationResult(
                main_response=main_response,
                error="main-flow agent invocation failed; workflow state was preserved",
            )

        try:
            action = self.parse_action(main_response.output_text)
            action = self._normalize_expected_output_type(action)
            action = self._normalize_skill_name(action)
            action = self._normalize_processing_action(action)
            action = self._normalize_testcase_review_action(action)
            self._validate_action_target(action)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return OrchestrationResult(
                main_response=main_response,
                error=f"invalid workflow action: {exc}",
            )

        if action.action is WorkflowActionType.WAIT_HUMAN:
            transition = self._transition_if_needed(
                action.target_state,
                trigger,
                action.reason,
            )
            return OrchestrationResult(
                main_response=main_response,
                action=action,
                transition=transition,
            )

        if action.action in {
            WorkflowActionType.TRANSITION,
            WorkflowActionType.MANUAL_INTERVENTION,
        }:
            target = (
                WorkflowState.MANUAL_INTERVENTION_REQUIRED
                if action.action is WorkflowActionType.MANUAL_INTERVENTION
                else action.target_state
            )
            if target is self.workflow.current_state:
                return OrchestrationResult(
                    main_response=main_response,
                    action=action,
                )
            transition = self._transition(target, trigger, action.reason)
            return OrchestrationResult(
                main_response=main_response,
                action=action,
                transition=transition,
            )

        try:
            specialist_request = self._specialist_request(action)
        except ValueError as exc:
            return OrchestrationResult(
                main_response=main_response,
                action=action,
                error=f"specialist request rejected: {exc}",
            )

        transition = self._transition_if_needed(action.target_state, trigger, action.reason)
        specialist_response = self.runner.run(specialist_request)
        if specialist_response.status is not AgentStatus.SUCCEEDED:
            return OrchestrationResult(
                main_response=main_response,
                action=action,
                specialist_response=specialist_response,
                transition=transition,
                error="specialist agent invocation failed; workflow state was preserved",
            )

        try:
            artifact, artifact_path, markdown_path, accepted_transition = self._accept_artifact(
                action.expected_output_type or "",
                specialist_response.output_text,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return OrchestrationResult(
                main_response=main_response,
                action=action,
                specialist_response=specialist_response,
                transition=transition,
                error=f"specialist output rejected: {exc}",
            )

        return OrchestrationResult(
            main_response=main_response,
            action=action,
            specialist_response=specialist_response,
            artifact_path=artifact_path,
            markdown_path=markdown_path,
            transition=accepted_transition or transition,
        )

    @staticmethod
    def parse_action(output_text: str) -> WorkflowAction:
        """Parse a JSON action, accepting a Markdown JSON fence from the model."""

        payload = _parse_json_object(output_text)
        return WorkflowAction.model_validate(payload)

    def _main_request(self) -> AgentRequest:
        refs = list(self.workflow.active_artifacts.values())
        input_summary = ", ".join(
            f"{item.input_id}({item.category})" for item in self.workflow.input_files
        ) or "无"
        artifact_summary = ", ".join(
            f"{name}={pointer.artifact_id}:v{pointer.version}"
            for name, pointer in self.workflow.active_artifacts.items()
        ) or "无"
        routing_context = self._routing_context(refs)
        return AgentRequest(
            request_id=f"main-{uuid4().hex}",
            project_id=self.workflow.project_id,
            role=AgentRole.MAIN_FLOW,
            task_name=f"route:{self.workflow.current_state.value}",
            prompt=(
                f"当前工作流状态为 {self.workflow.current_state.value}。\n"
                f"允许的下一状态为：{', '.join(state.value for state in WorkflowStateMachine(self.workflow).available_states()) or '无'}。\n"
                f"已导入的原始输入摘要：{input_summary}。\n"
                f"当前活动结构化产物：{artifact_summary}。\n"
                "以下是 Harness 已读取并校验过的路由摘要。你必须以该摘要为准，不要尝试通过文件系统重新读取产物，也不要因为无法访问工作区而猜测。\n"
                f"路由摘要：\n{routing_context}\n"
                "如果当前状态是 requirement_analyzing，必须继续当前阶段的需求分析；"
                "不得因为上一阶段评审报告仍有风险而重新退回需求评审。\n"
                "你只负责路由，不得读取或评审原始需求内容。处理中状态缺少当前阶段产物时，"
                "应重新调用该阶段的专业 Agent，目标状态保持当前状态。\n"
                "请根据当前产物和状态返回一个严格 JSON 的 WorkflowAction。\n"
                "注意：input_artifact_refs 只能填写结构化产物引用；原始输入文件已经通过 input_files 自动提供，"
                "不要把 input_id（例如 requirement-001）填写到 input_artifact_refs。"
            ),
            input_files=[],
            input_artifacts=refs,
            created_at=datetime.now(timezone.utc),
            model=self.model,
            working_directory=str(self.project_root) if self.project_root else None,
        )

    def _routing_context(self, refs: list[ArtifactPointer]) -> str:
        """Provide bounded, Harness-read routing facts to the main-flow agent."""

        if self.artifact_store is None or not refs:
            return "- 无可读取的结构化产物摘要"

        summaries: list[dict[str, Any]] = []
        for pointer in refs:
            try:
                artifact = self.artifact_store.load_artifact(
                    self.workflow.project_id,
                    pointer.artifact_type,
                    pointer.artifact_id,
                    pointer.version,
                )
            except Exception as exc:  # noqa: BLE001
                summaries.append(
                    {
                        "artifact_type": pointer.artifact_type,
                        "artifact_id": pointer.artifact_id,
                        "version": pointer.version,
                        "read_status": "failed",
                        "read_error": type(exc).__name__,
                    }
                )
                continue

            summary: dict[str, Any] = {
                "artifact_type": pointer.artifact_type,
                "artifact_id": pointer.artifact_id,
                "version": pointer.version,
                "read_status": "ok",
            }
            if pointer.artifact_type == "requirement_review":
                issues = artifact.get("issues") or []
                summary.update(
                    {
                        "decision": artifact.get("decision"),
                        "issue_count": len(issues),
                        "product_confirmation_issue_count": sum(
                            1
                            for issue in issues
                            if issue.get("needs_product_confirmation") is True
                        ),
                        "open_question_count": len(artifact.get("open_questions") or []),
                    }
                )
            elif pointer.artifact_type == "testcase_review":
                issues = artifact.get("issues") or []
                severity_counts: dict[str, int] = {}
                for issue in issues:
                    severity = str(issue.get("severity") or "unknown")
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1
                summary.update(
                    {
                        "decision": artifact.get("decision"),
                        "issue_count": len(issues),
                        "severity_counts": severity_counts,
                        "coverage_summary": artifact.get("coverage_summary"),
                        "routing_effect": (
                            "AI review is advisory. A completed testcase review must "
                            "enter waiting_testcase_approval so the test owner can "
                            "approve with recorded risk or request changes."
                        ),
                    }
                )
            elif pointer.artifact_type == "product_confirmation_checklist":
                items = artifact.get("items") or []
                summary.update(
                    {
                        "item_count": len(items),
                        "pending_item_count": sum(
                            1
                            for item in items
                            if item.get("status", "pending") == "pending"
                        ),
                        "confirmed_item_count": sum(
                            1 for item in items if item.get("status") == "confirmed"
                        ),
                    }
                )
            summaries.append(summary)

        if self._has_accepted_current_review_risk():
            summaries.append(
                {
                    "fact_type": "human_risk_acceptance",
                    "decision": "approved",
                    "requirement_review": (
                        self.workflow.active_artifacts["requirement_review"].model_dump(
                            mode="json"
                        )
                    ),
                    "routing_effect": (
                        "review risks remain traceable but do not block the current "
                        "requirement analysis stage"
                    ),
                }
            )

        return json.dumps(summaries, ensure_ascii=False, indent=2)
    def _current_input_files(self) -> list[InputFilePointer]:
        """Return the current requirement plus non-requirement source material."""
        requirement_inputs = [
            item
            for item in self.workflow.input_files
            if item.category == "requirement"
        ]
        current = None
        if self.workflow.current_requirement_input_id:
            current = next(
                (
                    item
                    for item in requirement_inputs
                    if item.input_id == self.workflow.current_requirement_input_id
                ),
                None,
            )
        if current is None and requirement_inputs:
            # Older workflow files have no pointer; the last imported requirement wins.
            current = requirement_inputs[-1]

        non_requirement_inputs = [
            item for item in self.workflow.input_files if item.category != "requirement"
        ]
        return ([current] if current else []) + non_requirement_inputs

    @staticmethod
    def _normalize_expected_output_type(action: WorkflowAction) -> WorkflowAction:
        """Accept the older testcase_design name for the canonical test_design artifact."""
        if action.expected_output_type != "testcase_design":
            return action
        return action.model_copy(update={"expected_output_type": "test_design"})

    @classmethod
    def _normalize_skill_name(cls, action: WorkflowAction) -> WorkflowAction:
        """Normalize legacy Skill names before the Harness resolves SKILL.md."""
        skill_name = action.skill_name
        if not skill_name:
            return action
        canonical_name = cls._SKILL_ALIASES.get(skill_name, skill_name)
        if canonical_name == skill_name:
            return action
        return action.model_copy(update={"skill_name": canonical_name})

    def _normalize_testcase_review_action(
        self,
        action: WorkflowAction,
    ) -> WorkflowAction:
        """Make the test owner, rather than the AI review, decide the quality gate."""
        review = self.workflow.active_artifacts.get("testcase_review")
        if review is None:
            return action
        if self.workflow.current_state is WorkflowState.TESTCASE_REVIEWING:
            pass
        elif (
            self.workflow.current_state is WorkflowState.MANUAL_INTERVENTION_REQUIRED
            and self.workflow.manual_resume_state is WorkflowState.TESTCASE_REVIEWING
        ):
            pass
        else:
            return action

        return WorkflowAction(
            action=WorkflowActionType.TRANSITION,
            target_state=WorkflowState.WAITING_TESTCASE_APPROVAL,
            reason=(
                "测试用例评审已完成。AI 评审结论仅作为质量建议，"
                "由测试负责人决定带风险批准或退回修订。"
            ),
        )

    def _normalize_processing_action(self, action: WorkflowAction) -> WorkflowAction:
        """Prevent a previously accepted review risk from re-blocking analysis."""
        if not self._has_accepted_current_review_risk():
            return action

        if self.workflow.current_state in {
            WorkflowState.WAITING_PRODUCT_REVISION,
            WorkflowState.MANUAL_INTERVENTION_REQUIRED,
        }:
            return WorkflowAction(
                action=WorkflowActionType.TRANSITION,
                target_state=WorkflowState.REQUIREMENT_ANALYZING,
                reason=(
                    "当前需求评审风险已完成人工强制通过；恢复工作流到需求分析阶段，"
                    "不得再次退回需求评审。"
                ),
            )

        if self.workflow.current_state is not WorkflowState.REQUIREMENT_ANALYZING:
            return action

        if (
            action.action is WorkflowActionType.INVOKE_AGENT
            and action.skill_name == "requirement-analysis"
            and action.expected_output_type == "requirement_analysis"
            and action.target_state is WorkflowState.TESTCASE_DESIGNING
        ):
            return action

        return WorkflowAction(
            action=WorkflowActionType.INVOKE_AGENT,
            target_role=AgentRole.TEST_ANALYSIS_DESIGN,
            skill_name="requirement-analysis",
            target_state=WorkflowState.TESTCASE_DESIGNING,
            reason=(
                "当前已处于 requirement_analyzing，且上一阶段风险已完成人工接受；"
                "不得重新评估历史评审风险或退回需求评审，直接执行需求分析。"
            ),
            expected_output_type="requirement_analysis",
        )

    def _has_accepted_current_review_risk(self) -> bool:
        """Return whether the active review was explicitly accepted by a human."""
        review = self.workflow.active_artifacts.get("requirement_review")
        if review is None:
            return False

        accepted = self.workflow.accepted_requirement_review
        if (
            accepted is not None
            and accepted.artifact_type == review.artifact_type
            and accepted.artifact_id == review.artifact_id
            and accepted.version == review.version
        ):
            return True

        for transition in reversed(self.workflow.transition_history):
            if transition.to_state is not WorkflowState.REQUIREMENT_ANALYZING:
                continue
            related = {
                (pointer.artifact_type, pointer.artifact_id, pointer.version)
                for pointer in transition.related_artifacts
            }
            if (
                ("requirement_review", review.artifact_id, review.version) in related
                and any(artifact_type == "human_approval" for artifact_type, _, _ in related)
            ):
                return True
        return False
    def _specialist_request(self, action: WorkflowAction) -> AgentRequest:
        refs = self._resolve_artifact_refs(action.input_artifact_refs)
        prompt = action.reason
        if action.skill_name == "requirement-analysis":
            review = self.workflow.active_artifacts.get("requirement_review")
            if review is None:
                raise OrchestrationError(
                    "requirement_review is required for requirement analysis"
                )
            if not any(
                ref.artifact_type == review.artifact_type
                and ref.artifact_id == review.artifact_id
                and ref.version == review.version
                for ref in refs
            ):
                refs.append(review)
            prompt = (
                action.reason
                + chr(10)
                + chr(10)
                + "Requirement analysis must use both the current effective requirement document "
                + "and the latest requirement_review artifact. The current requirement file is "
                + "the only source of current business facts; historical requirement versions are "
                + "for traceability only and must not be mixed into the analysis. "
                + "When human risk acceptance was used, unresolved review risks must be recorded "
                + "explicitly in open_questions or assumptions."
            )
        return AgentRequest(
            request_id=f"specialist-{uuid4().hex}",
            project_id=self.workflow.project_id,
            role=action.target_role,
            task_name=action.skill_name or action.expected_output_type or "specialist-task",
            skill_name=action.skill_name,
            prompt=prompt,
            input_files=self._current_input_files(),
            input_artifacts=refs,
            created_at=datetime.now(timezone.utc),
            model=self.model,
            working_directory=str(self.project_root) if self.project_root else None,
        )

    def _validate_action_target(self, action: WorkflowAction) -> None:
        if action.action is WorkflowActionType.INVOKE_AGENT:
            expected_model = self._ARTIFACT_MODELS.get(action.expected_output_type or "")
            if expected_model is None:
                raise OrchestrationError(
                    f"unsupported expected_output_type: {action.expected_output_type!r}"
                )
            if action.expected_output_type == "test_report":
                if action.target_role is not AgentRole.MAIN_FLOW:
                    raise OrchestrationError(
                        "test_report must be generated by the main_flow agent"
                    )
                if action.skill_name != "test-report":
                    raise OrchestrationError(
                        "test_report requires the test-report skill"
                    )
            elif action.target_role is AgentRole.MAIN_FLOW:
                raise OrchestrationError(
                    "main_flow may only invoke the test-report skill"
                )
            allowed_targets = WorkflowStateMachine(self.workflow).available_states() | {
                self.workflow.current_state
            }
            if action.target_state not in allowed_targets:
                raise OrchestrationError(
                    f"invoke target state is not reachable: {action.target_state.value}"
                )
        elif action.action in {
            WorkflowActionType.TRANSITION,
            WorkflowActionType.WAIT_HUMAN,
        }:
            if action.target_state is None:
                return
            allowed_targets = WorkflowStateMachine(self.workflow).available_states()
            if action.target_state is not self.workflow.current_state and action.target_state not in allowed_targets:
                raise OrchestrationError(
                    f"target state is not allowed: {action.target_state.value}"
                )

    def _transition_if_needed(
        self,
        target: WorkflowState | None,
        trigger: str,
        reason: str,
    ) -> WorkflowTransition | None:
        if target is None or target is self.workflow.current_state:
            return None
        return self._transition(target, trigger, reason)

    def _transition(
        self,
        target: WorkflowState | None,
        trigger: str,
        reason: str,
    ) -> WorkflowTransition:
        if target is None:
            raise OrchestrationError("transition target state is missing")
        transition = WorkflowStateMachine(self.workflow).transition(
            target,
            triggered_by=trigger,
            reason=reason,
        )
        self._save_workflow()
        return transition

    def _accept_artifact(
        self,
        artifact_type: str,
        output_text: str,
    ) -> tuple[
        BaseModel,
        Path | None,
        Path | None,
        WorkflowTransition | None,
    ]:
        model_type = self._ARTIFACT_MODELS.get(artifact_type)
        if model_type is None:
            raise OrchestrationError(f"unsupported artifact type: {artifact_type!r}")
        artifact = validate_artifact(model_type, _parse_json_object(output_text))
        if artifact.meta.project_id != self.workflow.project_id:
            raise OrchestrationError("artifact project_id does not match workflow")
        if artifact.meta.artifact_type != artifact_type:
            raise OrchestrationError(
                f"artifact_type mismatch: expected {artifact_type!r}, got {artifact.meta.artifact_type!r}"
            )
        if artifact_type == "test_report":
            if self.artifact_store is None:
                raise OrchestrationError(
                    "artifact_store is required to validate and save test_report"
                )
            saved = TestReportService(
                self.workflow,
                self.artifact_store,
            ).accept(artifact)
            return artifact, saved.json_path, saved.markdown_path, saved.transition
        artifact_path = self.artifact_store.save_artifact(artifact) if self.artifact_store else None
        markdown_path = None
        if artifact_type == "test_design" and self.artifact_store is not None:
            markdown_path = self.artifact_store.save_artifact_text(
                self.workflow.project_id,
                artifact.meta.artifact_type,
                artifact.meta.artifact_id,
                artifact.meta.version,
                render_test_design(artifact),
            )
        pointer = ArtifactPointer(
            artifact_id=artifact.meta.artifact_id,
            artifact_type=artifact.meta.artifact_type,
            version=artifact.meta.version,
        )
        self.workflow.active_artifacts[artifact_type] = pointer
        self.workflow.updated_at = datetime.now(timezone.utc)
        self._save_workflow()
        return artifact, artifact_path, markdown_path, None

    def _resolve_artifact_refs(self, refs: list[str]) -> list[ArtifactPointer]:
        if not refs:
            return list(self.workflow.active_artifacts.values())
        resolved: list[ArtifactPointer] = []
        for ref in refs:
            match = next(
                (
                    pointer
                    for key, pointer in self.workflow.active_artifacts.items()
                    if ref in {key, pointer.artifact_id, f"{pointer.artifact_id}:v{pointer.version}"}
                ),
                None,
            )
            if match is None:
                raise OrchestrationError(f"unknown input artifact reference: {ref}")
            resolved.append(match)
        return resolved

    def _save_workflow(self) -> None:
        if self.artifact_store:
            self.artifact_store.save_workflow(self.workflow.project_id, self.workflow)


def _parse_json_object(output_text: str) -> dict[str, Any]:
    text = output_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("JSON output must be an object")
    return payload
