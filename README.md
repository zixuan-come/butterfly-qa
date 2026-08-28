# Butterfly Agent

在微小需求风险演变成高成本缺陷之前发现并阻断它。

Butterfly Agent 是一个基于 Codex 的测试流程自动化原型，目标是把需求评审、需求分析、测试点与功能测试用例设计、用例评审、人工执行和测试报告串成一个可追踪的闭环。

## V1 架构

- 3 个 Agent：主流程 Agent、测试分析与设计 Agent、用例评审 Agent。
- 5 个 Skill：需求评审、需求分析、测试点与测试用例设计、测试用例评估、测试报告生成。
- 运行方式：本地 Python 流程编排 + FastAPI/Vue 工作台 + JSON 结构化产物 + Markdown 报告。
- 暂不包含：接口自动化、UI 自动化、RAG 和外部测试管理平台。

## 目录

```text
src/qa_agent/       Python 核心代码
frontend/           Vue Web 工作台源码
skills/             Skill 定义
agents/             Agent 职责契约
projects/           项目输入、产物和决策记录
tests/              自动化测试
docs/               需求和开发文档
```

## 开发原则

1. 先定义结构化数据协议，再编写 Agent 提示词。
2. 所有流程推进经过主流程 Agent，专业 Agent 不直接互相调用。
3. Agent 输出必须经过结构化校验，无法确认的内容显式标记。
4. 原始需求只读保存，正式需求由人修改。
5. 每次修订产生新版本，不覆盖历史产物。

## 当前进度

详见 [开发任务清单](docs/开发任务清单.md) 和 [需求文档](docs/需求文档.md)。

第一次使用请按照 [Butterfly Agent 使用说明](docs/Butterfly-QA使用说明.md) 逐步操作，并使用 [执行结果模板](docs/执行结果模板.json) 录入人工测试结果。

## 安装

面向日常使用和团队交付，推荐使用一键安装脚本。脚本会创建独立的 `butterfly-qa` Conda 环境、安装项目依赖，并为该环境持久化 `PYTHONUTF8=1`：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
conda activate butterfly-qa
```

复用已有的 `py310` 环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -EnvironmentName py310
conda activate py310
```

安装后可以直接使用 `butterfly-qa`，无需设置 `PYTHONPATH`。

## Web 快速开始

激活安装环境并进入项目目录后，只需执行：

```powershell
butterfly-qa web
```

命令会启动本地服务并在服务就绪后打开浏览器。默认地址为 `http://127.0.0.1:8000/`，按 `Ctrl+C` 停止。前端已经包含在 Python 安装包中，普通使用者不需要安装 Node 或分别启动前后端。

需要使用其他端口或不自动打开浏览器时：

```powershell
butterfly-qa web --port 8080 --no-open
```

下面的 CLI 命令适用于开发、排障和自动化脚本；日常测试流程优先使用 Web 工作台。

## CLI 快速开始

创建项目并导入需求：

```powershell
butterfly-qa project create address-change --name "修改收货地址" --created-by tester-001
butterfly-qa input add address-change .\requirement.md --category requirement --imported-by tester-001
butterfly-qa status address-change
```

导入测试证据：

```powershell
butterfly-qa evidence add address-change .\failure.png --type screenshot --description "收货地址保存失败截图"
butterfly-qa evidence add address-change .\service.log --type log --description "依赖服务日志"
```

执行当前流程的一步：

```powershell
butterfly-qa run address-change
```

人工确认测试用例：

```powershell
butterfly-qa approve address-change --type testcase --decision approved --by tester-001
```

提交人工执行结果：

```powershell
butterfly-qa execution submit address-change .\execution.json
```

所有命令默认以当前目录作为仓库根目录；在其他目录运行时可在子命令前传入 `--workspace <仓库路径>`。
