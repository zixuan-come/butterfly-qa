---
name: requirement-review
description: 评审需求文档、设计资料和业务约束，识别歧义、遗漏、冲突、不可测试内容和风险，并输出结构化需求评审结果。适用于需求刚导入或产品修订后重新评审；不用于编写测试用例。
---

# 需求评审

## 目标

在测试设计开始前判断需求是否具备可测试性，并输出可追溯的问题清单和准入结论。

## 输入

- 原始需求文档。
- 设计图、流程图、接口说明或业务规则（如有）。
- 已确认的项目约束、角色权限和兼容性要求（如有）。
- 产品修订说明和上一版评审报告（重新评审时）。

输入资料不足时，先记录缺失项；不要用常识补充未提供的业务规则。

## 评审步骤

1. 明确需求目标、范围、参与角色和不包含的范围。
2. 检查主流程、分支流程、状态变化和前后置条件。
3. 检查输入、输出、字段规则、数据关系和数据生命周期。
4. 检查正常、异常、边界、空值、重复操作和失败重试行为。
5. 检查角色、权限、登录状态和越权行为。
6. 检查与已有需求、业务规则、设计资料之间的冲突。
7. 检查每个验收条件是否可观察、可执行和可判定。
8. 对问题记录精确原文定位、影响和修改建议。
9. 根据阻塞问题给出评审结论。

## 原文定位规则

- 文本资料以物理文件行为准，第一行为第 1 行，空行也计入行号；必须使用带行号的读取方式核对，不得按章节大致估算。
- 单处问题的 location 使用“相对文件路径:第 12-14 行”格式。
- conflict 必须同时标出发生冲突的两处或多处位置，使用“相对文件路径:第 12-14 行 ↔ 相对文件路径:第 38-40 行”格式。
- 遗漏类问题应定位到本应定义该规则的最近章节或验收标准行。
- PDF 使用“文件路径:第 3 页”，图片使用“文件路径:顶部表单区域”等可复核位置；不要伪造文本行号。

## 问题类型

优先识别以下问题：

- `ambiguity`：含义不明确。
- `omission`：缺少必要规则或场景。
- `conflict`：资料之间互相冲突。
- `untestable`：无法设计可判定的测试。
- `boundary_missing`：缺少边界和异常规则。
- `permission_missing`：缺少角色或权限说明。
- `dependency_missing`：外部依赖、环境或数据条件未说明。

严重程度使用 `blocker`、`high`、`medium`、`low`。涉及核心流程、权限、数据一致性或无法执行测试的问题，不能标记为低等级。

## 输出

输出必须符合 `RequirementReview`，使用 JSON，不要只返回自然语言报告。

```json
{
  "meta": {
    "artifact_id": "requirement-review-001",
    "artifact_type": "requirement_review",
    "project_id": "demo-project",
    "version": 1,
    "status": "completed",
    "source_artifacts": [],
    "created_by": "test-analysis-design-agent",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  },
  "decision": "pass",
  "issues": [
    {
      "issue_id": "RR-001",
      "issue_type": "ambiguity",
      "severity": "high",
      "location": "input/requirement-001.md:第 12-14 行",
      "description": "手机号格式未定义",
      "impact": "无法设计稳定的边界用例",
      "suggestion": "补充支持的手机号格式和示例"
    }
  ],
  "assumptions": [],
  "open_questions": ["手机号支持哪些国家或地区的格式？"]
}
```

`decision` 只能使用 `pass`、`fail` 或 `needs_human_decision`。存在未解决的核心歧义、冲突或不可测试问题时，不得输出 `pass`。

字段必须严格匹配 Schema：问题类型字段名只能是 `issue_type`，不能使用 `type`；`open_questions` 和 `assumptions` 都是字符串数组，不能输出对象数组。

## 边界

- 不修改正式需求。
- 不生成正式测试用例。
- 不把推测写成需求事实。
- 不隐藏问题来让流程通过。
- 不输出无法定位依据的严重结论。
