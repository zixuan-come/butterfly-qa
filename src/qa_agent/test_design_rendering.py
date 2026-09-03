"""Human-readable rendering for generated test design artifacts."""

from __future__ import annotations

from typing import Any


def render_test_design(test_design: Any) -> str:
    """Render a TestDesign model or mapping as review-friendly Markdown."""
    data = (
        test_design.model_dump(mode="json")
        if hasattr(test_design, "model_dump")
        else test_design
    )
    meta = data.get("meta") or {}
    lines = [
        f"# 测试用例设计：{meta.get('project_id', '')}",
        "",
        "## 基本信息",
        "",
        f"- 项目 ID：{meta.get('project_id', '')}",
        f"- 产物 ID：{meta.get('artifact_id', '')}",
        f"- 版本：v{meta.get('version', '')}",
        f"- 创建人：{meta.get('created_by', '')}",
        f"- 创建时间：{meta.get('created_at', '')}",
        "",
        "## 测试点",
        "",
        "| 测试点 ID | 关联需求 | 类型 | 风险 | 描述 |",
        "| --- | --- | --- | --- | --- |",
    ]
    points = data.get("test_points") or []
    for point in points:
        lines.append(
            "| {id} | {requirements} | {kind} | {risk} | {description} |".format(
                id=point.get("test_point_id", ""),
                requirements=", ".join(point.get("requirement_refs") or []),
                kind=point.get("category", ""),
                risk=point.get("risk", ""),
                description=_one_line(point.get("description", "")),
            )
        )
    if not points:
        lines.append("| - | - | - | - | 暂无测试点 |")

    lines.extend(["", "## 测试用例", ""])
    cases = data.get("test_cases") or []
    for index, case in enumerate(cases, start=1):
        lines.extend(
            [
                f"### {case.get('case_id', f'TC-{index:03d}')}：{case.get('title', '')}",
                "",
                f"- 版本：v{case.get('version', 1)}",
                f"- 优先级：{case.get('priority', '')}",
                f"- 关联需求：{', '.join(case.get('requirement_refs') or [])}",
                f"- 关联测试点：{', '.join(case.get('test_point_refs') or [])}",
                f"- 前置条件：{_one_line(case.get('preconditions', []))}",
                f"- 测试数据：{_one_line(case.get('test_data', []))}",
                "",
                "| 步骤 | 操作 | 预期结果 |",
                "| --- | --- | --- |",
            ]
        )
        steps = case.get("steps") or []
        for step_index, step in enumerate(steps, start=1):
            lines.append(
                f"| {step.get('step_no', step_index)} "
                f"| {_one_line(step.get('action', ''))} "
                f"| {_one_line(step.get('expected_result', ''))} |"
            )
        if not steps:
            lines.append("| - | 暂无步骤 | - |")
        lines.append("")
    if not cases:
        lines.append("暂无测试用例。")

    return "\n".join(lines).rstrip() + "\n"


def _one_line(value: Any) -> str:
    if isinstance(value, list):
        value = "；".join(str(item) for item in value)
    return (
        str(value or "")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )