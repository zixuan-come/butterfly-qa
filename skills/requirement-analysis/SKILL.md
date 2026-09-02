---
name: requirement-analysis
description: 将已通过需求评审的需求资料拆解为结构化需求、业务流程、状态、规则、权限、前后置条件和待确认项。适用于需求分析阶段；不用于需求准入评审或直接编写测试用例。
---

# 需求分析

## 目标

把自然语言需求转换成后续测试设计可以直接使用的结构化分析结果，同时保留不确定性和需求追溯关系。

## 前置条件

- 必须有原始需求或等价的需求资料。
- 需求评审通过，或由主流程明确允许进入分析（包括人工风险接受放行）。
- 评审报告中的阻塞问题不得被静默忽略。

## 分析步骤

1. 为每条独立业务能力分配稳定的 `requirement_id`。
2. 提取需求目标、角色、业务对象和操作范围。
3. 拆解主流程、分支流程和异常流程。
4. 识别状态、状态转换、触发条件和终态。
5. 提取输入输出、字段规则、默认值、唯一性和数据关系。
6. 提取业务规则、前置条件和后置条件。
7. 提取角色权限、登录要求和资源访问范围。
8. 记录异常处理、失败重试、回滚和幂等要求（适用时）。
9. 将不能从资料确认的内容放入 `open_questions` 或 `assumptions`。
10. 检查拆解结果是否覆盖原始需求中的每个范围和验收条件。

## 事实与不确定性

使用以下原则区分信息：

- 需求明确写出的内容属于已确认事实。
- 根据多个明确规则推导出的内容，写明推导依据。
- 资料没有说明但影响测试设计的内容，放入 `open_questions`。
- 为了形成分析结构而暂时采用的解释，放入 `assumptions`，不得伪装成事实。

## 输出

输出必须符合 `RequirementAnalysis`，使用 JSON。

```json
{
  "meta": {
    "artifact_id": "requirement-analysis-001",
    "artifact_type": "requirement_analysis",
    "project_id": "demo-project",
    "version": 1,
    "status": "completed",
    "source_artifacts": ["requirement-review-001"],
    "created_by": "test-analysis-design-agent",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  },
  "requirements": [
    {
      "requirement_id": "REQ-001",
      "title": "修改收货地址",
      "description": "登录用户可以修改已有收货地址",
      "actors": ["登录用户"],
      "business_rules": [],
      "preconditions": ["用户已登录"],
      "postconditions": ["地址信息保存成功"],
      "open_questions": []
    }
  ],
  "flows": [],
  "states": [],
  "permissions": [],
  "assumptions": []
}
```

至少输出一条 `requirements`。如果流程、状态、权限或规则没有在资料中出现，使用空列表并在 `open_questions` 或 `assumptions` 中说明原因，不要编造内容。

## 边界

- 不重新替产品修改正式需求。
- 不跳过需求评审结论中的阻塞项。
- 不直接输出测试用例。
- 不把接口文档、代码实现或个人经验当作需求事实，除非它们被明确作为输入约束。


## 输入边界（强制）

- 只以当前有效需求文档作为当前业务事实来源。历史需求版本只能用于追溯，不得混入分析。
- 必须同时读取当前有效需求文档和最新的 requirement_review 产物；不能只读取评审报告。
- 需求评审通过和人工风险接受放行都允许进入分析。人工放行时，未解决风险必须显式进入 open_questions 或 assumptions。
- 输出中的来源引用应包含当前需求版本和评审报告版本，必要时保留文件哈希。
