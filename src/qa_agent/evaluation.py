"""Evaluate Butterfly Agent artifacts against a human-maintained golden set.

The evaluator uses stable identifiers instead of fuzzy text matching. Human
judgement stays explicit in the baseline JSON, while arithmetic and audit details
are deterministic and reproducible.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"

_AGENT_LISTS = {
    "requirement_issue_ids": ("requirement_review", "issues", "issue_id"),
    "test_point_ids": ("test_design", "test_points", "test_point_id"),
    "test_case_ids": ("test_design", "test_cases", "case_id"),
    "review_issue_ids": ("testcase_review", "issues", "issue_id"),
}
_CONTEXT_FIELDS = ("project_id", "module_id", "requirement_sha256")


def evaluate_artifacts(
    agent_payload: Mapping[str, Any],
    baseline_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the eight objective T27 quality metrics and audit details."""

    agent = _require_mapping(agent_payload, "agent")
    baseline = _require_mapping(baseline_payload, "baseline")
    _validate_schema_version(agent, "agent")
    _validate_schema_version(baseline, "baseline")
    run = _validate_context(agent, baseline)
    agent_ids = {
        name: _extract_ids(agent, name, path)
        for name, path in _AGENT_LISTS.items()
    }

    valid_requirement = _id_set(
        baseline.get("valid_requirement_issue_ids"),
        "baseline.valid_requirement_issue_ids",
    )
    invalid_requirement = _id_set(
        baseline.get("invalid_requirement_issue_ids"),
        "baseline.invalid_requirement_issue_ids",
    )
    baseline_points = _id_set(
        baseline.get("baseline_test_point_ids"),
        "baseline.baseline_test_point_ids",
    )
    valid_points = _id_set(
        baseline.get("valid_test_point_ids"),
        "baseline.valid_test_point_ids",
    )
    executable_cases = _id_set(
        baseline.get("executable_case_ids"),
        "baseline.executable_case_ids",
    )
    duplicate_cases = _id_set(
        baseline.get("duplicate_case_ids", []),
        "baseline.duplicate_case_ids",
    )
    unsupported_items = _id_set(
        baseline.get("unsupported_item_ids", []),
        "baseline.unsupported_item_ids",
    )
    confirmed_review = _id_set(
        baseline.get("review_issue_ids"),
        "baseline.review_issue_ids",
    )

    generated_requirement = agent_ids["requirement_issue_ids"]
    generated_points = agent_ids["test_point_ids"]
    generated_cases = agent_ids["test_case_ids"]
    generated_review = agent_ids["review_issue_ids"]
    namespaced_items = _namespaced_artifact_items(agent_ids)

    _require_disjoint(
        valid_requirement,
        invalid_requirement,
        "valid_requirement_issue_ids",
        "invalid_requirement_issue_ids",
    )
    _require_subset(
        valid_requirement | invalid_requirement,
        generated_requirement,
        "baseline requirement issue",
    )
    unclassified = generated_requirement - valid_requirement - invalid_requirement
    if unclassified:
        raise ValueError(
            "人工基准必须完成所有 Agent 需求问题分类，尚未分类："
            + ", ".join(sorted(unclassified))
        )
    _require_subset(valid_points, generated_points, "baseline valid test point")
    _require_subset(
        executable_cases | duplicate_cases,
        generated_cases,
        "baseline test case",
    )
    _require_subset(
        unsupported_items,
        namespaced_items,
        "baseline unsupported item",
    )

    metrics = {
        "requirement_issue_effectiveness_rate": _metric(
            "需求问题有效率", valid_requirement, generated_requirement,
            "higher_is_better",
        ),
        "requirement_issue_false_positive_rate": _metric(
            "需求问题误报率", invalid_requirement, generated_requirement,
            "lower_is_better",
        ),
        "test_point_recall": _metric(
            "测试点召回率", generated_points & baseline_points, baseline_points,
            "higher_is_better",
        ),
        "test_point_precision": _metric(
            "测试点准确率", valid_points, generated_points, "higher_is_better",
        ),
        "test_case_executability_rate": _metric(
            "用例可执行率", executable_cases, generated_cases, "higher_is_better",
        ),
        "test_case_duplication_rate": _metric(
            "用例重复率", duplicate_cases, generated_cases, "lower_is_better",
        ),
        "unsupported_extension_rate": _metric(
            "无依据扩展率", unsupported_items, namespaced_items, "lower_is_better",
        ),
        "review_issue_hit_rate": _metric(
            "评审问题命中率", generated_review & confirmed_review, confirmed_review,
            "higher_is_better",
        ),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "run": run,
        "metrics": metrics,
        "process_metrics": _process_metrics(baseline),
        "summary": {
            "available_quality_metrics": sum(
                metric["status"] == "available" for metric in metrics.values()
            ),
            "unavailable_quality_metrics": sum(
                metric["status"] != "available" for metric in metrics.values()
            ),
            "observation_only": True,
            "note": "单次样本仅用于观察；真实版本结论需结合多次人工基准记录。",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qa_agent.evaluation",
        description="将 Agent 产物与人工基准按稳定 ID 进行量化评分",
    )
    parser.add_argument("agent", help="Agent 原始产物 JSON")
    parser.add_argument("baseline", help="人工基准 JSON")
    parser.add_argument("-o", "--output", help="评分报告 JSON；省略时仅输出到终端")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_artifacts(
            _read_json(Path(args.agent)),
            _read_json(Path(args.baseline)),
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(f"{rendered}\n", encoding="utf-8")
        print(rendered)
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def _extract_ids(
    payload: Mapping[str, Any],
    flat_name: str,
    nested_path: tuple[str, str, str],
) -> set[str]:
    if flat_name in payload:
        return _id_set(payload[flat_name], f"agent.{flat_name}")
    artifact_name, collection_name, id_name = nested_path
    artifact = _require_mapping(payload.get(artifact_name), f"agent.{artifact_name}")
    raw_items = artifact.get(collection_name)
    if not isinstance(raw_items, list):
        raise ValueError(f"agent.{artifact_name}.{collection_name} 必须是 JSON 数组")
    ids: list[str] = []
    for index, raw_item in enumerate(raw_items):
        item = _require_mapping(
            raw_item, f"agent.{artifact_name}.{collection_name}[{index}]"
        )
        ids.append(_non_empty_string(item.get(id_name), f"{id_name}[{index}]"))
    return _unique_ids(ids, f"agent.{artifact_name}.{collection_name}")


def _validate_context(
    agent: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    agent_run = _require_mapping(agent.get("run"), "agent.run")
    baseline_run = _require_mapping(baseline.get("run"), "baseline.run")
    result: dict[str, Any] = dict(agent_run)
    for field in _CONTEXT_FIELDS:
        agent_value = agent_run.get(field)
        baseline_value = baseline_run.get(field)
        if agent_value is None and baseline_value is None:
            continue
        if agent_value != baseline_value:
            raise ValueError(f"agent.run.{field} 与 baseline.run.{field} 必须一致")
        result[field] = agent_value
    for key, value in baseline_run.items():
        result.setdefault(str(key), value)
    return result


def _validate_schema_version(payload: Mapping[str, Any], path: str) -> None:
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"{path}.schema_version 仅支持 {SCHEMA_VERSION}")


def _metric(
    label: str,
    numerator_ids: set[str],
    denominator_ids: set[str],
    direction: str,
) -> dict[str, Any]:
    numerator = len(numerator_ids)
    denominator = len(denominator_ids)
    if denominator == 0:
        ratio = None
        percentage = None
        status = "zero_denominator"
    else:
        ratio = round(numerator / denominator, 6)
        percentage = round(ratio * 100, 2)
        status = "available"
    return {
        "label": label,
        "numerator": numerator,
        "denominator": denominator,
        "ratio": ratio,
        "percentage": percentage,
        "numerator_ids": sorted(numerator_ids),
        "denominator_ids": sorted(denominator_ids),
        "direction": direction,
        "status": status,
    }


def _process_metrics(baseline: Mapping[str, Any]) -> dict[str, Any]:
    process = baseline.get("process", baseline)
    process = _require_mapping(process, "baseline.process")
    result: dict[str, Any] = {}
    for name, label in (
        ("manual_revision_minutes", "人工修订耗时"),
        ("end_to_end_minutes", "端到端处理耗时"),
    ):
        value = process.get(name)
        if value is None:
            result[name] = {
                "label": label, "value": None, "unit": "minutes",
                "status": "not_provided",
            }
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"baseline.process.{name} 必须是有限非负数")
        if value < 0 or not math.isfinite(value):
            raise ValueError(f"baseline.process.{name} 必须是有限非负数")
        result[name] = {
            "label": label, "value": value, "unit": "minutes",
            "status": "available",
        }
    return result


def _namespaced_artifact_items(agent_ids: Mapping[str, set[str]]) -> set[str]:
    prefixes = {
        "requirement_issue_ids": "requirement_issue",
        "test_point_ids": "test_point",
        "test_case_ids": "test_case",
        "review_issue_ids": "review_issue",
    }
    return {
        f"{prefixes[group]}:{item_id}"
        for group, ids in agent_ids.items()
        for item_id in ids
    }


def _require_subset(values: set[str], allowed: set[str], label: str) -> None:
    unknown = values - allowed
    if unknown:
        raise ValueError(f"{label} 包含 Agent 产物中不存在的 ID：{', '.join(sorted(unknown))}")


def _require_disjoint(
    left: set[str], right: set[str], left_name: str, right_name: str
) -> None:
    overlap = left & right
    if overlap:
        raise ValueError(
            f"baseline.{left_name} 与 baseline.{right_name} 不能重复："
            + ", ".join(sorted(overlap))
        )


def _id_set(value: Any, path: str) -> set[str]:
    if not isinstance(value, list):
        raise ValueError(f"{path} 必须是 ID 字符串数组")
    ids = [_non_empty_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    return _unique_ids(ids, path)


def _unique_ids(ids: Sequence[str], path: str) -> set[str]:
    values = set(ids)
    if len(values) != len(ids):
        raise ValueError(f"{path} 不能包含重复 ID")
    return values


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} 必须是非空字符串")
    return value.strip()


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} 必须是 JSON 对象")
    return value


def _read_json(path: Path) -> Mapping[str, Any]:
    return _require_mapping(json.loads(path.read_text(encoding="utf-8")), str(path))


if __name__ == "__main__":
    raise SystemExit(main())
