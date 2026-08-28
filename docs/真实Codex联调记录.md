# 真实 Codex 联调记录

## 2026-08-25：修改收货地址

### 联调环境

- Python：Conda `py310`
- `openai-codex`：`0.147.0`
- Butterfly Agent：仓库源码运行
- 联调项目：`integration-address-change`
- 原始需求：`docs/示例-修改收货地址需求.md`

由于当前环境的 Python 安装目录和临时构建目录权限受限，本次通过 `PYTHONPATH=src` 运行仓库源码。联调运行产物保存在 Codex 可写临时工作区，不进入 Git。

### 已验证链路

1. 创建本地测试项目。
2. 导入不可覆盖的需求文件并记录 SHA-256。
3. 使用真实 Codex 调用主流程 Agent。
4. 主流程 Agent 返回结构化 `WorkflowAction`。
5. 调用测试分析与设计 Agent 执行需求评审 Skill。
6. 需求评审结果通过 Pydantic 校验并版本化保存。
7. 主流程根据评审结论进入 `waiting_product_revision`。
8. 每次 Agent 调用均生成审计记录，包括耗时、状态、线程 ID 和错误摘要。

### 联调发现与修复

| 问题 | 处理 |
| --- | --- |
| 主流程把原始输入 ID 写入 `input_artifact_refs` | 明确原始输入与结构化产物引用边界 |
| 模型使用 `type` 而 Schema 要求 `issue_type` | 使用 SDK 原生 `output_schema` 强制结构化输出 |
| Pydantic Schema 不符合 Codex strict schema | 递归补齐 `required`、禁止额外字段并移除 `default` |
| 主流程读取需求内容并越权做评审 | 主流程只接收输入摘要，不再获得原始文件；专业 Agent 保留完整输入 |
| 处理中状态缺少产物时直接转人工 | 明确重试当前阶段专业 Agent 的恢复规则 |
| 确定性的 Schema 配置错误被重复调用三次 | 将 `invalid_json_schema` 标记为不可重试错误 |
| CLI 只显示笼统失败信息 | 增加 Agent 角色、状态、错误类型和错误摘要 |

### 真实评审结果

需求评审结论为 `needs_human_decision`，主要问题包括：

- 缺少完整订单状态与可修改矩阵。
- 缺少登录、订单归属和角色权限规则。
- 缺少收货人、手机号和详细地址的格式及长度边界。
- 缺少省市区级联和失效地区处理规则。
- 保存失败、输入保留和重试行为不明确。
- 地址修改与发货、物流、订单快照之间的一致性规则不明确。
- 重复提交、并发修改和发货并发冲突策略不明确。

### 当前状态

```text
waiting_product_revision
```

继续联调前需要产品角色补充上述规则，并以新输入版本重新提交需求。系统不应由 Agent 自行编造这些业务规则。

### 当前验证结果

```text
58 passed
34 Python files syntax OK
```
