import json

import pytest

from qa_agent.evaluation import evaluate_artifacts, main


def _context():
    return {
        "project_id": "demo-project",
        "module_id": "address",
        "requirement_sha256": "a" * 64,
        "test_design_id": "design-001",
        "test_design_version": 1,
    }


def make_agent():
    return {
        "schema_version": "1.0",
        "run": {**_context(), "run_id": "run-001", "agent_version": "v1"},
        "requirement_review": {"issues": [{"issue_id": "RQ-1"}, {"issue_id": "RQ-2"}, {"issue_id": "RQ-3"}]},
        "test_design": {"test_points": [{"test_point_id": "TP-1"}, {"test_point_id": "TP-2"}, {"test_point_id": "TP-X"}], "test_cases": [{"case_id": "TC-1"}, {"case_id": "TC-2"}, {"case_id": "TC-3"}]},
        "testcase_review": {"issues": [{"issue_id": "RV-1"}, {"issue_id": "RV-X"}]},
    }


def make_baseline():
    return {
        "schema_version": "1.0",
        "run": _context(),
        "valid_requirement_issue_ids": ["RQ-1", "RQ-2"],
        "invalid_requirement_issue_ids": ["RQ-3"],
        "baseline_test_point_ids": ["TP-1", "TP-2", "TP-3", "TP-4"],
        "valid_test_point_ids": ["TP-1", "TP-2"],
        "executable_case_ids": ["TC-1", "TC-2"],
        "duplicate_case_ids": ["TC-3"],
        "unsupported_item_ids": ["test_point:TP-X"],
        "review_issue_ids": ["RV-1", "RV-2"],
        "process": {"manual_revision_minutes": 12.5},
    }


def test_evaluate_artifacts_calculates_id_based_metrics():
    metrics = evaluate_artifacts(make_agent(), make_baseline())["metrics"]
    assert metrics["requirement_issue_effectiveness_rate"]["percentage"] == 66.67
    assert metrics["requirement_issue_false_positive_rate"]["percentage"] == 33.33
    assert metrics["test_point_recall"]["percentage"] == 50.0
    assert metrics["test_point_precision"]["percentage"] == 66.67
    assert metrics["test_case_executability_rate"]["percentage"] == 66.67
    assert metrics["test_case_duplication_rate"]["percentage"] == 33.33
    assert metrics["unsupported_extension_rate"]["numerator_ids"] == ["test_point:TP-X"]
    assert metrics["unsupported_extension_rate"]["denominator"] == 11
    assert metrics["review_issue_hit_rate"]["percentage"] == 50.0


def test_empty_sets_return_zero_denominator():
    agent = {"schema_version": "1.0", "run": _context(), "requirement_review": {"issues": []}, "test_design": {"test_points": [], "test_cases": []}, "testcase_review": {"issues": []}}
    baseline = {"schema_version": "1.0", "run": _context(), "valid_requirement_issue_ids": [], "invalid_requirement_issue_ids": [], "baseline_test_point_ids": [], "valid_test_point_ids": [], "executable_case_ids": [], "duplicate_case_ids": [], "unsupported_item_ids": [], "review_issue_ids": []}
    report = evaluate_artifacts(agent, baseline)
    assert all(metric["status"] == "zero_denominator" for metric in report["metrics"].values())


def test_rejects_duplicate_unknown_and_conflicting_ids():
    baseline = make_baseline()
    baseline["valid_requirement_issue_ids"] = ["RQ-1", "RQ-1"]
    with pytest.raises(ValueError, match="重复 ID"):
        evaluate_artifacts(make_agent(), baseline)
    baseline = make_baseline()
    baseline["executable_case_ids"] = ["TC-NOT-GENERATED"]
    with pytest.raises(ValueError, match="不存在的 ID"):
        evaluate_artifacts(make_agent(), baseline)
    baseline = make_baseline()
    baseline["invalid_requirement_issue_ids"] = ["RQ-2"]
    with pytest.raises(ValueError, match="不能重复"):
        evaluate_artifacts(make_agent(), baseline)


def test_rejects_unclassified_and_context_mismatch():
    baseline = make_baseline()
    baseline["invalid_requirement_issue_ids"] = []
    with pytest.raises(ValueError, match="尚未分类"):
        evaluate_artifacts(make_agent(), baseline)
    baseline = make_baseline()
    baseline["run"]["requirement_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="requirement_sha256"):
        evaluate_artifacts(make_agent(), baseline)


def test_cli_writes_report_and_reports_invalid_json(tmp_path, capsys):
    agent_path, baseline_path, report_path = tmp_path / "agent.json", tmp_path / "baseline.json", tmp_path / "report.json"
    agent_path.write_text(json.dumps(make_agent(), ensure_ascii=False), encoding="utf-8")
    baseline_path.write_text(json.dumps(make_baseline(), ensure_ascii=False), encoding="utf-8")
    assert main([str(agent_path), str(baseline_path), "-o", str(report_path)]) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["schema_version"] == "1.0"
    assert '"需求问题有效率"' in capsys.readouterr().out
    agent_path.write_text("not-json", encoding="utf-8")
    assert main([str(agent_path), str(baseline_path)]) == 1
    assert "错误：" in capsys.readouterr().err
