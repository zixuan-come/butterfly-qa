"""Command-line interface for operating a local Butterfly Agent workflow."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .codex_runner import CodexAgentRunner
from .audit import AgentAuditStore, ResilientAgentRunner
from .evidence import EvidenceService
from .human_actions import HumanApprovalService, ManualExecutionService
from .orchestrator import WorkflowOrchestrator
from .project import InputCategory, ProjectManager
from .schemas import (
    ApprovalDecision,
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    ExecutionBatch,
    HumanApproval,
)
from .storage import ArtifactStore, ArtifactStoreError
from .workflow.models import WorkflowRun


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="butterfly-qa",
        description="Butterfly Agent 本地测试流程编排工具",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="仓库根目录，默认使用当前目录",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    project = commands.add_parser("project", help="项目管理")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    create = project_commands.add_parser("create", help="创建测试项目")
    create.add_argument("project_id")
    create.add_argument("--name", required=True)
    create.add_argument("--created-by", required=True)
    create.set_defaults(handler=_create_project)

    input_parser = commands.add_parser("input", help="原始输入管理")
    input_commands = input_parser.add_subparsers(dest="input_command", required=True)
    add_input = input_commands.add_parser("add", help="导入需求或附件")
    add_input.add_argument("project_id")
    add_input.add_argument("path")
    add_input.add_argument(
        "--category",
        required=True,
        choices=[category.value for category in InputCategory],
    )
    add_input.add_argument("--imported-by", required=True)
    add_input.add_argument("--input-id")
    add_input.set_defaults(handler=_add_input)

    evidence = commands.add_parser("evidence", help="测试证据管理")
    evidence_commands = evidence.add_subparsers(
        dest="evidence_command",
        required=True,
    )
    add_evidence = evidence_commands.add_parser(
        "add",
        help="导入截图、日志、录屏等测试证据",
    )
    add_evidence.add_argument("project_id")
    add_evidence.add_argument("path")
    add_evidence.add_argument(
        "--type",
        required=True,
        choices=["screenshot", "log", "video", "file", "other"],
        dest="evidence_type",
    )
    add_evidence.add_argument("--description", required=True)
    add_evidence.add_argument("--evidence-id")
    add_evidence.set_defaults(handler=_add_evidence)

    status = commands.add_parser("status", help="查看项目状态")
    status.add_argument("project_id")
    status.add_argument("--json", action="store_true", dest="as_json")
    status.set_defaults(handler=_show_status)

    run = commands.add_parser("run", help="使用 Codex 执行当前流程的一步")
    run.add_argument("project_id")
    run.add_argument("--model")
    run.set_defaults(handler=_run_step)

    approve = commands.add_parser("approve", help="提交人工审批")
    approve.add_argument("project_id")
    approve.add_argument(
        "--type",
        required=True,
        choices=["testcase", "report"],
        dest="approval_type",
    )
    approve.add_argument(
        "--decision",
        required=True,
        choices=[decision.value for decision in ApprovalDecision],
    )
    approve.add_argument("--by", required=True, dest="decided_by")
    approve.add_argument("--comment", default="")
    approve.add_argument("--approval-id")
    approve.set_defaults(handler=_submit_approval)

    execution = commands.add_parser("execution", help="人工执行结果管理")
    execution_commands = execution.add_subparsers(
        dest="execution_command",
        required=True,
    )
    submit_execution = execution_commands.add_parser(
        "submit",
        help="提交 ExecutionBatch JSON",
    )
    submit_execution.add_argument("project_id")
    submit_execution.add_argument("path")
    submit_execution.set_defaults(handler=_submit_execution)

    web = commands.add_parser("web", help="启动 Butterfly Agent Web 工作台")
    web.add_argument("--host", default="127.0.0.1", help="监听地址")
    web.add_argument("--port", default=8000, type=int, help="监听端口")
    web.add_argument(
        "--no-open",
        action="store_true",
        help="启动后不自动打开浏览器",
    )
    web.set_defaults(handler=_serve_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (
        ArtifactStoreError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def _workspace(args: argparse.Namespace) -> Path:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace 不存在或不是目录：{workspace}")
    return workspace


def _store(args: argparse.Namespace) -> ArtifactStore:
    return ArtifactStore(_workspace(args) / "projects")


def _load_workflow(store: ArtifactStore, project_id: str) -> WorkflowRun:
    return WorkflowRun.model_validate(store.load_workflow(project_id))


def _create_project(args: argparse.Namespace) -> int:
    manager = ProjectManager(_workspace(args) / "projects")
    project, workflow = manager.create_project(
        args.project_id,
        args.name,
        created_by=args.created_by,
    )
    _print_json(
        {
            "project_id": project.project_id,
            "name": project.name,
            "state": workflow.current_state.value,
            "path": str(manager.store.project_root(project.project_id)),
        }
    )
    return 0


def _add_input(args: argparse.Namespace) -> int:
    manager = ProjectManager(_workspace(args) / "projects")
    imported = manager.import_input(
        args.project_id,
        args.path,
        InputCategory(args.category),
        imported_by=args.imported_by,
        input_id=args.input_id,
    )
    _print_json(imported.model_dump(mode="json"))
    return 0


def _add_evidence(args: argparse.Namespace) -> int:
    store = _store(args)
    evidence = EvidenceService(store).import_file(
        args.project_id,
        args.path,
        args.evidence_type,
        description=args.description,
        evidence_id=args.evidence_id,
    )
    _print_json(evidence.model_dump(mode="json"))
    return 0


def _show_status(args: argparse.Namespace) -> int:
    store = _store(args)
    project = store.load_project(args.project_id)
    workflow = _load_workflow(store, args.project_id)
    payload = {
        "project_id": args.project_id,
        "name": project["name"],
        "state": workflow.current_state.value,
        "input_count": len(workflow.input_files),
        "active_artifacts": {
            name: pointer.model_dump(mode="json")
            for name, pointer in workflow.active_artifacts.items()
        },
        "revision_rounds": {
            "requirement": workflow.requirement_revision_rounds,
            "testcase": workflow.testcase_revision_rounds,
            "report": workflow.report_revision_rounds,
        },
    }
    if args.as_json:
        _print_json(payload)
    else:
        print(f"项目：{payload['name']} ({args.project_id})")
        print(f"状态：{payload['state']}")
        print(f"输入文件：{payload['input_count']}")
        print(f"活动产物：{len(payload['active_artifacts'])}")
        revisions = payload["revision_rounds"]
        print(
            "修订轮次："
            f"需求 {revisions['requirement']} / "
            f"用例 {revisions['testcase']} / 报告 {revisions['report']}"
        )
    return 0


def _run_step(args: argparse.Namespace) -> int:
    workspace = _workspace(args)
    store = ArtifactStore(workspace / "projects")
    workflow = _load_workflow(store, args.project_id)
    runner = ResilientAgentRunner(
        CodexAgentRunner(workspace),
        AgentAuditStore(store),
    )
    result = WorkflowOrchestrator(
        workflow,
        runner,
        artifact_store=store,
        project_root=store.project_root(args.project_id),
        model=args.model,
    ).step()
    latest_response = result.specialist_response or result.main_response
    payload: dict[str, Any] = {
        "state": workflow.current_state.value,
        "action": result.action.model_dump(mode="json") if result.action else None,
        "artifact_path": str(result.artifact_path) if result.artifact_path else None,
        "markdown_path": str(result.markdown_path) if result.markdown_path else None,
        "thread_id": (
            result.specialist_response.thread_id
            if result.specialist_response
            else result.main_response.thread_id
        ),
        "agent": {
            "role": latest_response.role.value,
            "status": latest_response.status.value,
            "error_type": latest_response.error_type,
            "error_message": latest_response.error_message,
        },
        "error": result.error,
    }
    _print_json(payload)
    return 1 if result.error else 0


def _submit_approval(args: argparse.Namespace) -> int:
    store = _store(args)
    workflow = _load_workflow(store, args.project_id)
    approval_type, artifact_type = {
        "testcase": (ApprovalType.TESTCASE_APPROVAL, "test_design"),
        "report": (ApprovalType.REPORT_APPROVAL, "test_report"),
    }[args.approval_type]
    target = workflow.active_artifacts.get(artifact_type)
    if target is None:
        raise ValueError(f"当前没有可审批的 {artifact_type} 产物")
    timestamp = datetime.now(timezone.utc)
    approval = HumanApproval(
        meta=ArtifactMeta(
            artifact_id=args.approval_id or f"approval-{uuid4().hex}",
            artifact_type="human_approval",
            project_id=args.project_id,
            status=ArtifactStatus.COMPLETED,
            created_by=args.decided_by,
            created_at=timestamp,
            updated_at=timestamp,
        ),
        approval_type=approval_type,
        target_artifact_id=target.artifact_id,
        target_artifact_type=target.artifact_type,
        target_artifact_version=target.version,
        decision=ApprovalDecision(args.decision),
        decided_by=args.decided_by,
        decided_at=timestamp,
        comment=args.comment,
    )
    transition = HumanApprovalService(workflow, store).submit(approval)
    _print_json(
        {
            "approval_id": approval.meta.artifact_id,
            "from_state": transition.from_state.value,
            "to_state": transition.to_state.value,
        }
    )
    return 0


def _submit_execution(args: argparse.Namespace) -> int:
    store = _store(args)
    workflow = _load_workflow(store, args.project_id)
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    execution = ExecutionBatch.model_validate(payload)
    path, transition = ManualExecutionService(workflow, store).submit(execution)
    _print_json(
        {
            "artifact_path": str(path),
            "from_state": transition.from_state.value,
            "to_state": transition.to_state.value,
        }
    )
    return 0


def _serve_web(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")

    import uvicorn

    from .web import create_app

    workspace = _workspace(args)
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{browser_host}:{args.port}/"
    print(f"Butterfly Agent Web 工作台：{url}")
    print("按 Ctrl+C 停止服务。")
    if not args.no_open:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(url, browser_host, args.port),
            daemon=True,
        ).start()
    uvicorn.run(create_app(workspace), host=args.host, port=args.port, log_level="info")
    return 0


def _open_browser_when_ready(
    url: str,
    host: str,
    port: int,
    *,
    timeout_seconds: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
