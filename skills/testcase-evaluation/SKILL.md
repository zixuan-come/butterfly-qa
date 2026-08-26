---
name: testcase-evaluation
description: 独立评估测试点和功能测试用例的覆盖性、正确性、可执行性、可追溯性和风险匹配度，并输出结构化评审报告。适用于用例设计完成后的独立评审，不用于修改或重写用例。
---

# 测试用例评审

## 目标

在人工确认用例前，以独立视角发现覆盖遗漏、步骤缺陷、预期结果不明确、数据不足和风险优先级错误。

## 输入

- 已通过需求评审的 `RequirementAnalysis`。
- 当前版本的 `TestDesign`。
- 项目测试规范或评审标准（如有）。
- 历史评审问题和人工重点关注项（如有）。

输入中的版本和关联 ID 必须一致。发现版本不一致或产物缺失时，先报告输入问题，不进行猜测性评审。

## 评审步骤

1. 检查需求条目是否都有测试点覆盖。
2. 检查测试点是否都有测试用例覆盖。
3. 检查高风险和高优先级需求的场景深度。
4. 检查用例的前置条件、数据、步骤和预期结果。
5. 检查每个预期结果是否可观察、可判定。
6. 检查异常、边界、权限、状态和数据一致性场景。
7. 检查用例之间的重复、冲突和不可区分问题。
8. 检查优先级、标签和风险是否匹配。
9. 为问题记录具体用例、步骤、测试点或需求依据。
10. 汇总覆盖情况并给出评审结论。

## 问题等级

- `blocker`：核心需求没有覆盖，或用例无法执行。
- `high`：重要风险遗漏，可能导致严重缺陷逃逸。
- `medium`：明显缺口，但不一定阻塞整体测试设计。
- `low`：表达、标签或组织方式优化。

通常存在 `blocker` 或 `high` 时输出 `fail`。如果问题本质是需求含义未确认，输出 `needs_human_decision`。

## 输出

输出必须符合 `TestCaseReview`，使用 JSON。

```json
{
  "meta": {
    "artifact_id": "testcase-review-001",
    "artifact_type": "testcase_review",
    "project_id": "demo-project",
    "version": 1,
    "status": "completed",
    "source_artifacts": ["test-design-001", "requirement-analysis-001"],
    "created_by": "testcase-review-agent",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  },
  "decision": "fail",
  "issues": [
    {
      "issue_id": "CR-001",
      "case_id": "TC-003",
      "severity": "high",
      "issue_type": "missing_boundary",
      "description": "没有验证地址数量达到上限时的行为",
      "evidence": "REQ-002 和 TP-004 未被用例覆盖",
      "suggestion": "增加达到上限和超过上限两条用例"
    }
  ],
  "coverage_summary": "正常保存已覆盖，地址数量边界未覆盖"
}
```

`issues` 没有问题时使用空列表。不要为了填充报告而编造问题。

## 边界

- 只评审，不修改 `TestDesign`。
- 不生成替代用例掩盖原用例问题。
- 不修改需求或需求分析。
- 不执行测试，不伪造实际结果和测试证据。
- 不直接推进工作流状态。
- 不因用例数量多就默认覆盖充分。
