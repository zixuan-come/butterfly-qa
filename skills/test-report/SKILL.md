---
name: test-report
description: 根据已确认用例的执行记录、缺陷、阻塞项和测试证据生成可追溯的 Markdown 测试报告和结构化报告数据。适用于人工测试执行完成后汇总结果，不用于虚构执行结果或替代发布审批。
---

# 测试报告生成

## 目标

将已确认测试用例的实际执行结果和证据汇总为准确、可追溯、可审计的测试报告。

## 输入

- 已人工确认的 `TestDesign` 版本。
- 一条或多条 `ExecutionRecord`。
- 缺陷编号、缺陷状态和影响范围（如有）。
- 截图、日志、录屏或其他 `Evidence`。
- 测试环境、执行时间和测试范围。
- 未覆盖范围、风险接受记录和阻塞说明（如有）。

没有执行记录的用例不能被统计为通过。缺少关键证据时，必须在风险或待补充项中说明。

## 生成步骤

1. 确认报告范围和用例版本。
2. 校验每条执行记录引用的用例存在且版本匹配。
3. 按 `passed`、`failed`、`blocked`、`skipped` 统计结果。
4. 检查统计总数与执行记录数量一致。
5. 汇总失败用例、缺陷、阻塞项和未覆盖范围。
6. 检查证据路径和证据类型，标记缺失或不可访问的证据。
7. 建立需求、测试点、用例、执行记录和证据的追溯关系。
8. 基于事实和风险生成测试结论，不直接替代发布决定。
9. 输出结构化 `TestReport` 和人可读 Markdown 报告。

## 结果规则

- `passed`：有执行记录，实际结果满足预期，并有足够依据。
- `failed`：实际结果与预期不一致，或确认存在缺陷。
- `blocked`：因环境、依赖、数据或权限原因无法完成执行。
- `skipped`：明确决定不执行，并记录原因。

不得把 `blocked` 统计为 `failed`，不得把 `skipped` 或缺少记录统计为 `passed`。

## 输出

结构化结果必须符合 `TestReport`：

```json
{
  "meta": {
    "artifact_id": "test-report-001",
    "artifact_type": "test_report",
    "project_id": "demo-project",
    "version": 1,
    "status": "pending",
    "source_artifacts": ["test-design-001", "execution-001"],
    "created_by": "main-flow-agent",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  },
  "scope": "修改收货地址功能",
  "environment": "测试环境",
  "total_cases": 10,
  "passed": 7,
  "failed": 1,
  "blocked": 1,
  "skipped": 1,
  "defect_refs": ["BUG-001"],
  "risk_summary": ["支付依赖不可用导致一条用例阻塞"],
  "conclusion": "本轮测试存在一个高优先级失败和一个阻塞项，需处理后再评估发布风险",
  "trace_refs": {
    "REQ-001": ["TP-001", "TC-001", "record-001"]
  }
}
```

Markdown 报告至少包含：测试范围、环境、执行统计、失败项、阻塞项、缺陷、证据、未覆盖范围、风险和结论。

## 结论边界

- 结论必须引用执行记录和证据。
- 不凭空补充实际结果、缺陷状态或环境信息。
- 不把“全部用例通过”直接等同于“可以发布”。
- 不修改执行记录和已确认用例。
- 报告发布前是否需要人工审批由主流程决定。
