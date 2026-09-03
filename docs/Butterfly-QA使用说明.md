# Butterfly Agent 使用说明

本文档用于指导测试人员接收并导入产品经理或需求方提供的需求资料，借助 Butterfly Agent 完成需求评审、需求分析、测试设计、人工执行和测试报告审批的完整流程。

流程中的职责边界如下：

- 产品经理或需求方：提供正式需求，回答需求评审问题，并负责修改、补充正式需求。
- 测试人员：接收和导入需求资料，检查 AI 产物，审批测试用例，执行测试、关联证据并核对测试报告。
- Butterfly Agent Agent：辅助需求评审、需求分析、测试点与用例设计、用例评审和报告生成，不代替产品修改需求，也不代替测试人员执行测试。
- Python Harness：校验结构化产物、控制状态转换、保存版本和审计记录。

## 1. 使用方式

日常使用优先进入 Web 工作台，不需要记忆后文中的 CLI 命令：

```powershell
conda activate butterfly-qa
cd "C:\Users\14534\Desktop\测试全流程Agent系统"
butterfly-qa web
```

服务就绪后会自动打开 `http://127.0.0.1:8000/`。在页面中创建项目、上传产品需求，再按当前阶段完成审批、人工执行和报告归档。关闭服务时回到 PowerShell 按 `Ctrl+C`。

Butterfly Agent 不是点击一次就自动跑到底的黑盒。页面中的“继续流程”每次只推进一个受控步骤，正确使用方式是：

```text
点击一次“继续流程”
-> 查看返回状态和产物
-> 人工确认结果
-> 再决定是否继续
```

遇到错误、人工审批状态或资料不足状态时应立即停止自动推进。后文保留 CLI 操作，主要用于开发调试、故障排查和自动化脚本。

## 2. 首次安装

首次拿到项目后，打开 PowerShell 并进入项目目录：

```powershell
cd "C:\Users\14534\Desktop\测试全流程Agent系统"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装脚本默认完成以下工作：

- 检查本机是否可以使用 Conda。
- 创建独立的 `butterfly-qa` Conda 环境；环境已存在时直接复用。
- 安装 Python 3.10、Butterfly Agent 和运行依赖。
- 为该 Conda 环境持久化 `PYTHONUTF8=1`，避免 Windows 中文乱码。
- 验证 `butterfly-qa` CLI 是否可以运行。

你当前电脑已经使用 `py310`，也可以直接复用：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -EnvironmentName py310
```

安装只需要执行一次。以后每次重新打开 PowerShell，只需：

```powershell
conda activate butterfly-qa
cd "C:\Users\14534\Desktop\测试全流程Agent系统"
butterfly-qa web
```

如果安装时使用的是 `py310`，则执行 `conda activate py310`。

确认 CLI 和 Codex 登录状态：

```powershell
butterfly-qa --help
codex login status
```

后续不需要设置 `PYTHONPATH`，也不需要每次手工设置 `PYTHONUTF8`。

## 3. 接收并准备产品需求资料

第一次试用时，建议测试人员从产品经理或需求方已经提供的需求中，选择一个规模较小、规则相对完整的功能需求。需求文件推荐使用 Markdown，并至少包含：

- 背景和目标。
- 功能范围与不包含范围。
- 用户角色和权限。
- 主流程与异常流程。
- 状态及状态转换规则。
- 字段格式、长度和边界。
- 失败、重试和并发规则。
- 可观察、可判定的验收标准。

如果产品或需求方还提供了设计图、业务规则和约束，可以作为独立附件导入，不必由测试人员重新整理进同一个文件。资料缺失时，应通过需求评审要求需求方补充，而不是由测试人员或 Agent 擅自补写业务规则。

## 4. 创建项目

设置本次测试使用的变量：

```powershell
$ProjectId = "my-first-requirement"
$Tester = "tester-001"
```

`ProjectId` 只能使用英文字母、数字、点、下划线和连字符，不能使用中文或空格。

创建项目：

```powershell
butterfly-qa project create $ProjectId `
  --name "首个产品需求测试" `
  --created-by $Tester
```

项目运行数据将保存在：

```text
projects/<ProjectId>/
```

同一个 `ProjectId` 不能重复创建。

## 5. 导入需求和附件

导入主需求：

```powershell
butterfly-qa input add $ProjectId `
  "C:\你的目录\需求文档.md" `
  --category requirement `
  --imported-by $Tester `
  --input-id requirement-v1
```

按需导入其他资料：

```powershell
butterfly-qa input add $ProjectId "C:\你的目录\设计说明.md" `
  --category design --imported-by $Tester --input-id design-v1

butterfly-qa input add $ProjectId "C:\你的目录\业务规则.md" `
  --category business_rule --imported-by $Tester --input-id rules-v1

butterfly-qa input add $ProjectId "C:\你的目录\测试约束.md" `
  --category constraint --imported-by $Tester --input-id constraint-v1

butterfly-qa input add $ProjectId "C:\你的目录\原型图.png" `
  --category attachment --imported-by $Tester --input-id prototype-v1
```

可用分类：

| 分类 | 用途 |
| --- | --- |
| `requirement` | 需求、PRD、用户故事 |
| `design` | 设计说明、流程设计 |
| `business_rule` | 独立业务规则 |
| `constraint` | 技术或测试约束 |
| `attachment` | 原型图、截图等附件 |

导入文件会复制到项目的 `input/` 目录并记录 SHA-256，不会修改原文件。

## 6. 查看状态

每次操作前后都可以运行：

```powershell
butterfly-qa status $ProjectId
butterfly-qa status $ProjectId --json
```

JSON 状态中的关键字段：

- `state`：当前流程状态。
- `input_count`：已导入文件数量。
- `active_artifacts`：当前有效产物及其版本。
- `revision_rounds`：需求、用例和报告的修订次数。

## 7. 运行需求评审到用例评审

执行一步：

```powershell
butterfly-qa run $ProjectId
```

命令输出中需要检查：

- `state`：执行后的状态。
- `action`：主流程选择了什么动作。
- `artifact_path`：本次生成的产物文件。
- `agent.status`：真实 Agent 是否成功。
- `error`：是否被 Harness 拒绝。

只要 `error` 不是 `null`，就不要继续执行下一次 `run`。

正常情况下，系统将依次完成：

```text
requirement_received
-> requirement_reviewing
-> requirement_analyzing
-> testcase_designing
-> testcase_reviewing
-> waiting_testcase_approval
```

一次 `run` 可能同时完成状态转换和一个专业 Agent 任务，因此不要依赖固定的运行次数，只依据 `state` 决定下一步。

每次生成产物后，先打开 `artifact_path` 检查内容。例如：

```text
projects/<ProjectId>/artifacts/
├── requirement_review/
├── requirement_analysis/
├── test_design/
└── testcase_review/
```

## 8. 处理需求退回

如果状态变为：

```text
waiting_product_revision
```

说明需求存在需要产品或业务人员确认的问题。此时：

1. 打开最新的 `requirement_review` 产物。
2. 由产品或需求负责人补充规则。
3. 新建修订版需求文件，不要修改项目 `input/` 中的历史文件。
4. 在修订版开头注明“本版本替代 requirement-v1”，并写明变更内容。
5. 使用新的 `input-id` 导入。

示例：

```powershell
butterfly-qa input add $ProjectId `
  "C:\你的目录\需求文档-v2.md" `
  --category requirement `
  --imported-by $Tester `
  --input-id requirement-v2
```

然后查看状态并执行一步：

```powershell
butterfly-qa status $ProjectId --json
butterfly-qa run $ProjectId
```

默认情况下，重新评审通过前不进入测试用例设计。如果产品或需求负责人确认问题属于可接受风险，可以在页面的“人工决策”区域选择“已知风险，强制进入需求分析”，填写风险接受理由后放行。该操作不会删除或覆盖 AI 评审产物，后续人员仍可从左侧“需求评审”阶段查看完整风险清单。

## 9. 检查并审批测试用例

当状态为：

```text
waiting_testcase_approval
```

先从 `active_artifacts.test_design` 找到当前测试设计版本，并检查：

- 需求是否都有对应测试点。
- 正常、异常、边界、权限、状态和数据场景是否合理。
- 用例步骤是否能执行。
- 每个步骤的预期结果是否可观察、可判定。
- 用例是否引用了正确的需求和测试点 ID。
- 用例评审 Agent 提出的问题是否已经处理。

确认通过：

```powershell
butterfly-qa approve $ProjectId `
  --type testcase `
  --decision approved `
  --by $Tester `
  --comment "用例检查通过"
```

需要修改：

```powershell
butterfly-qa approve $ProjectId `
  --type testcase `
  --decision changes_requested `
  --by $Tester `
  --comment "补充管理员越权、重复提交和订单状态并发场景"
```

`changes_requested` 或 `rejected` 必须填写 `comment`。修改后状态会进入 `waiting_case_revision`，再使用 `run` 发起用例修订和重新评审。

用例批准后，状态进入：

```text
waiting_manual_execution
```

## 10. 人工执行测试

打开当前 `test_design` JSON，按其中的 `test_cases` 逐条执行。记录：

- `case_id` 和用例版本。
- 测试环境。
- 执行人和执行时间。
- `passed`、`failed`、`blocked` 或 `skipped`。
- 实际结果。
- 缺陷编号。
- 截图、日志、录屏等证据。

没有执行的用例不能填写为 `passed`。

## 11. 导入测试证据

失败、阻塞和关键高风险场景建议保存证据：

```powershell
butterfly-qa evidence add $ProjectId `
  "C:\你的目录\failure.png" `
  --type screenshot `
  --description "TC-002 保存失败截图" `
  --evidence-id tc-002-failure

butterfly-qa evidence add $ProjectId `
  "C:\你的目录\service.log" `
  --type log `
  --description "TC-003 依赖服务异常日志" `
  --evidence-id tc-003-service-log
```

可用证据类型：`screenshot`、`log`、`video`、`file`、`other`。

命令会返回完整的证据 JSON，其中包括：

- `evidence_id`
- `path`
- `sha256`
- `size_bytes`
- `media_type`

把这段信息放到对应执行记录的 `evidence` 数组中。不要手工修改已经导入的证据文件，否则哈希校验会失败。

## 12. 填写执行结果 JSON

复制 [执行结果模板.json](./执行结果模板.json)，并根据当前测试设计修改。

关键规则：

1. `project_id` 必须是当前项目 ID。
2. `test_design_id` 和 `test_design_version` 必须与当前活动测试设计完全一致。
3. 每条已确认用例必须且只能有一条执行记录。
4. `case_id` 和 `case_version` 必须来自当前测试设计。
5. 时间使用 ISO 8601 格式，例如 `2026-08-25T14:30:00+08:00`。
6. `result` 只能是 `passed`、`failed`、`blocked` 或 `skipped`。
7. 失败用例应填写 `defect_refs`，并尽量关联证据。
8. `evidence` 中的信息应直接使用 `evidence add` 的返回结果。

假设文件保存为 `C:\你的目录\execution.json`，提交：

```powershell
butterfly-qa execution submit $ProjectId "C:\你的目录\execution.json"
```

系统会检查：

- 是否遗漏用例。
- 是否包含未知用例。
- 测试设计版本是否过期。
- 证据文件是否存在。
- 证据 SHA-256 和大小是否一致。

提交成功后进入 `generating_report`。

## 13. 生成并审批测试报告

生成报告：

```powershell
butterfly-qa run $ProjectId
```

成功后会返回：

- `artifact_path`：报告 JSON。
- `markdown_path`：方便人工阅读的 Markdown 报告。
- `state`：`waiting_report_approval`。

检查报告中的执行统计、失败和阻塞用例、缺陷、风险、结论及追溯关系。

批准报告：

```powershell
butterfly-qa approve $ProjectId `
  --type report `
  --decision approved `
  --by $Tester `
  --comment "执行数据和风险结论核对通过"
```

要求修改：

```powershell
butterfly-qa approve $ProjectId `
  --type report `
  --decision changes_requested `
  --by $Tester `
  --comment "补充阻塞用例对发布范围的影响"
```

报告退回后会回到 `generating_report`，再运行一次 `run` 生成修订版报告。

报告批准后，状态为：

```text
completed
```

## 14. 状态与操作速查

| 当前状态 | 谁处理 | 下一步 |
| --- | --- | --- |
| `requirement_received` | Agent | 执行一次 `run` |
| `requirement_reviewing` | Agent | 检查产物，无错误后再 `run` |
| `waiting_product_revision` | 产品/需求负责人 | 修订并导入新需求再 `run`，或在确认风险后选择人工强制放行 |
| `requirement_analyzing` | Agent | 检查产物，无错误后再 `run` |
| `testcase_designing` | Agent | 检查产物，无错误后再 `run` |
| `testcase_reviewing` | Agent | 检查产物，无错误后再 `run` |
| `waiting_case_revision` | Agent | 根据人工意见执行 `run` |
| `waiting_testcase_approval` | 测试人员 | `approve --type testcase` |
| `waiting_manual_execution` | 测试人员 | 执行用例、导入证据、提交执行 JSON |
| `generating_report` | Agent | 执行一次 `run` |
| `waiting_report_approval` | 测试负责人 | `approve --type report` |
| `manual_intervention_required` | 人工 | 查看审计记录，解决原因后再决定是否 `run` |
| `completed` | 无 | 流程结束 |

## 15. 错误排查

### 15.1 `run` 返回错误

先看命令输出中的：

```text
agent.role
agent.status
agent.error_type
agent.error_message
error
```

然后查看最近一次 Agent 审计：

```powershell
$LatestRun = Get-ChildItem ".\projects\$ProjectId\agent-runs\*.json" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content $LatestRun.FullName -Raw -Encoding UTF8
```

错误没有解决前，不要继续执行 `run`。

### 15.2 命令显示乱码

确认当前环境已经持久化 UTF-8：

```powershell
conda env config vars list
```

正常情况下应看到 `PYTHONUTF8 = 1`。如果没有，重新执行 `install.ps1`。

### 15.3 CLI 看不到最新命令

说明当前环境中安装的版本过旧。在仓库根目录重新运行安装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -EnvironmentName butterfly-qa
conda activate butterfly-qa
butterfly-qa --help
```

项目开发人员临时验证未安装源码时，才需要使用 `PYTHONPATH=src` 模式；普通使用者不需要了解或设置 `PYTHONPATH`。

### 15.4 Codex 认证失败

```powershell
codex login status
codex login
```

不要把 API Key 写进需求、执行结果、日志或 Git 仓库。

### 15.5 证据校验失败

常见原因：

- 导入后修改了证据文件。
- 执行 JSON 中的路径不是 `evidence add` 返回的路径。
- `evidence_id` 与证据清单不一致。
- 文件被移动或删除。

重新导入时应使用新的 `evidence-id`，不要覆盖旧证据。

## 16. 第一次试用建议

- 选择 5 至 15 条预期用例的小需求。
- 先只导入需求，不急着导入大量历史资料。
- 每次 `run` 后人工检查产物。
- 记录 Agent 漏掉、误判和过度扩展的内容。
- 不要为了让流程跑通而忽略合理的需求评审问题。
- 首轮目标是评估产物质量和人工修订成本，不是追求完全无人值守。
