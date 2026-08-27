"""Deterministic validation and Markdown rendering for test reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .schemas import ArtifactStatus, ExecutionBatch, ExecutionResult, TestReport
from .evidence import EvidenceService
from .storage import ArtifactStore
from .workflow.models import ArtifactPointer, WorkflowRun, WorkflowTransition
from .workflow.state_machine import WorkflowStateMachine
from .workflow.states import WorkflowState
from .workflow.transitions import validate_transition


class ReportValidationError(ValueError):
    """Raised when an AI-generated report disagrees with execution facts."""


@dataclass(frozen=True)
class SavedReport:
    json_path: Path
    markdown_path: Path
    transition: WorkflowTransition


class TestReportService:
    """Verify report facts, persist both formats and request human approval."""

    def __init__(self, workflow: WorkflowRun, store: ArtifactStore) -> None:
        self.workflow = workflow
        self.store = store

    def accept(self, report: TestReport) -> SavedReport:
        if self.workflow.current_state is not WorkflowState.GENERATING_REPORT:
            raise ReportValidationError(
                "test report can only be accepted while generating a report"
            )
        if report.meta.project_id != self.workflow.project_id:
            raise ReportValidationError("report project_id does not match workflow")
        if report.meta.artifact_type != "test_report":
            raise ReportValidationError("report artifact_type must be 'test_report'")
        if report.meta.status is not ArtifactStatus.PENDING:
            raise ReportValidationError("report status must be pending human approval")

        execution_pointer = self.workflow.active_artifacts.get("test_execution")
        design_pointer = self.workflow.active_artifacts.get("test_design")
        if execution_pointer is None or design_pointer is None:
            raise ReportValidationError(
                "active test_design and test_execution artifacts are required"
            )
        execution = ExecutionBatch.model_validate(
            self.store.load_artifact(
                self.workflow.project_id,
                execution_pointer.artifact_type,
                execution_pointer.artifact_id,
                execution_pointer.version,
            )
        )
        try:
            EvidenceService(self.store).verify_batch(
                self.workflow.project_id,
                execution,
            )
        except ValueError as exc:
            raise ReportValidationError(f"report evidence rejected: {exc}") from exc
        self._validate_sources(report, design_pointer, execution_pointer)
        self._validate_facts(report, execution)
        validate_transition(
            self.workflow.current_state,
            WorkflowState.WAITING_REPORT_APPROVAL,
        )

        markdown = self.render_markdown(report, execution)
        json_path = self.store.save_artifact(report)
        markdown_path = self.store.save_artifact_text(
            self.workflow.project_id,
            report.meta.artifact_type,
            report.meta.artifact_id,
            report.meta.version,
            markdown,
        )
        report_pointer = ArtifactPointer(
            artifact_id=report.meta.artifact_id,
            artifact_type=report.meta.artifact_type,
            version=report.meta.version,
        )
        machine = WorkflowStateMachine(self.workflow)
        machine.set_active_artifact("test_report", report_pointer)
        transition = machine.transition(
            WorkflowState.WAITING_REPORT_APPROVAL,
            triggered_by=report.meta.created_by,
            reason="测试报告已生成并通过执行数据校验",
            related_artifacts=[execution_pointer, report_pointer],
        )
        self.store.save_workflow(self.workflow.project_id, self.workflow)
        return SavedReport(json_path, markdown_path, transition)

    @staticmethod
    def _validate_sources(
        report: TestReport,
        design_pointer: ArtifactPointer,
        execution_pointer: ArtifactPointer,
    ) -> None:
        required = {design_pointer.artifact_id, execution_pointer.artifact_id}
        missing = required - set(report.meta.source_artifacts)
        if missing:
            raise ReportValidationError(
                "report source_artifacts missing: " + ", ".join(sorted(missing))
            )

    @staticmethod
    def _validate_facts(report: TestReport, execution: ExecutionBatch) -> None:
        counts = Counter(record.result for record in execution.records)
        expected = {
            "total_cases": len(execution.records),
            "passed": counts[ExecutionResult.PASSED],
            "failed": counts[ExecutionResult.FAILED],
            "blocked": counts[ExecutionResult.BLOCKED],
            "skipped": counts[ExecutionResult.SKIPPED],
        }
        actual = {
            "total_cases": report.total_cases,
            "passed": report.passed,
            "failed": report.failed,
            "blocked": report.blocked,
            "skipped": report.skipped,
        }
        if actual != expected:
            raise ReportValidationError(
                f"report statistics do not match execution records: "
                f"expected {expected}, got {actual}"
            )

        expected_defects = {
            defect
            for record in execution.records
            for defect in record.defect_refs
        }
        if set(report.defect_refs) != expected_defects:
            raise ReportValidationError(
                "report defect_refs do not match execution records"
            )
        trace_values = {
            ref for refs in report.trace_refs.values() for ref in refs
        }
        missing_records = {
            record.record_id for record in execution.records
        } - trace_values
        if missing_records:
            raise ReportValidationError(
                "report trace_refs missing execution records: "
                + ", ".join(sorted(missing_records))
            )
        if (report.blocked or report.skipped) and not report.risk_summary:
            raise ReportValidationError(
                "blocked or skipped cases require a risk_summary"
            )

    @staticmethod
    def render_markdown(report: TestReport, execution: ExecutionBatch) -> str:
        """Render a stable, evidence-oriented report for human approval."""

        lines = [
            f"# 测试报告：{_escape(report.scope)}",
            "",
            "## 基本信息",
            "",
            f"- 测试范围：{_escape(report.scope)}",
            f"- 测试环境：{_escape(report.environment)}",
            f"- 报告版本：v{report.meta.version}",
            f"- 执行产物：{execution.meta.artifact_id}:v{execution.meta.version}",
            "",
            "## 执行统计",
            "",
            "| 总数 | 通过 | 失败 | 阻塞 | 跳过 |",
            "| ---: | ---: | ---: | ---: | ---: |",
            f"| {report.total_cases} | {report.passed} | {report.failed} | "
            f"{report.blocked} | {report.skipped} |",
        ]
        lines.extend(_result_section("失败项", execution, ExecutionResult.FAILED))
        lines.extend(_result_section("阻塞项", execution, ExecutionResult.BLOCKED))
        lines.extend(_result_section("跳过项", execution, ExecutionResult.SKIPPED))
        lines.extend(
            [
                "",
                "## 缺陷",
                "",
                *(_bullet_list(report.defect_refs) or ["- 无"]),
                "",
                "## 风险与遗留问题",
                "",
                *(_bullet_list(report.risk_summary) or ["- 无"]),
                "",
                "## 结论",
                "",
                _escape(report.conclusion),
                "",
                "## 追溯关系",
                "",
                "| 需求/范围 | 关联项 |",
                "| --- | --- |",
            ]
        )
        lines.extend(
            f"| {_escape(key)} | {_escape(', '.join(refs))} |"
            for key, refs in sorted(report.trace_refs.items())
        )
        return "\n".join(lines).rstrip() + "\n"


def _result_section(
    title: str,
    execution: ExecutionBatch,
    result: ExecutionResult,
) -> list[str]:
    records = [record for record in execution.records if record.result is result]
    lines = ["", f"## {title}", ""]
    if not records:
        return lines + ["- 无"]
    for record in records:
        defects = ", ".join(record.defect_refs) or "无"
        evidence = ", ".join(item.path for item in record.evidence) or "无"
        lines.extend(
            [
                f"### {record.case_id}:v{record.case_version}",
                "",
                f"- 实际结果：{_escape(record.actual_result)}",
                f"- 关联缺陷：{_escape(defects)}",
                f"- 测试证据：{_escape(evidence)}",
                "",
            ]
        )
    return lines[:-1]


def _bullet_list(values: list[str]) -> list[str]:
    return [f"- {_escape(value)}" for value in values]


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
